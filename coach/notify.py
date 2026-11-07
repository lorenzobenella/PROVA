"""Notifiche sul telefono (Telegram o ntfy) e memoria di cosa è già stato inviato.

Il coach gira ogni 4 ore, ma scrive solo quando serve:
- la sera (dalle 19) manda l'anteprima di domani;
- la mattina, quando sonno e HRV sono sincronizzati, scrive solo se la seduta cambia
  rispetto all'anteprima (o se l'anteprima non era partita);
- quando l'allenamento risulta fatto manda il riepilogo e la seduta di domani.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from . import export

EVENING_HOUR = 19   # da quest'ora parte l'anteprima di domani
FALLBACK_HOUR = 11  # se a quest'ora sonno e HRV non ci sono ancora, manda comunque il piano
MORNING_HOUR = 5


@dataclass
class Message:
    text: str
    files: list = field(default_factory=list)       # [(nome, bytes)]
    marks: list = field(default_factory=list)       # [(chiave_giorno, campo, valore)]


# ---------------------------------------------------------------- stato

def load_state(path):
    path = Path(path)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"days": {}, "tests": []}


def save_state(path, state, today):
    keep = (today - timedelta(days=14)).isoformat()
    state["days"] = {d: v for d, v in state.get("days", {}).items() if d >= keep}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------- decisioni

def decide(state, now, today_plan, tomorrow_plan, ftp, zwo_files, new_tests=(), old_ftp=None):
    """Messaggi da inviare adesso. `zwo_files` = {giorno: (nome, bytes)} per allegare la seduta."""
    days = state.get("days", {})
    today, tomorrow = today_plan.day.isoformat(), tomorrow_plan.day.isoformat()
    t = days.get(today, {})
    tm = days.get(tomorrow, {})
    hour = now.hour
    out = []

    for d, watts, ok in new_tests:
        if d.isoformat() not in state.get("tests", []):
            msg = Message(export.message_test(d, watts, ok, old_ftp or ftp))
            msg.marks.append(("_tests", d.isoformat(), True))
            out.append(msg)

    h_today = export.plan_hash(today_plan, ftp)
    h_tomorrow = export.plan_hash(tomorrow_plan, ftp)

    def attach(plan):
        f = zwo_files.get(plan.day)
        return [f] if f else []

    if today_plan.status == "fatto":
        if not t.get("done"):
            out.append(Message(export.message_done(today_plan, ftp, tomorrow_plan), attach(tomorrow_plan),
                               [(today, "done", True), (tomorrow, "preview", h_tomorrow)]))
            tm = dict(tm, preview=h_tomorrow)
    elif hour >= MORNING_HOUR:
        r = today_plan.readiness
        final = (r is not None and not r.provisional) or hour >= FALLBACK_HOUR
        sent = t.get("final") or t.get("preview")
        if final and t.get("final") != h_today:
            if sent == h_today:
                out.append(Message("", marks=[(today, "final", h_today)]))  # nessun cambio: solo memoria
            else:
                out.append(Message(export.message_today(today_plan, ftp, updated=bool(sent)), attach(today_plan),
                                   [(today, "final", h_today)]))
        elif not final and not sent:
            out.append(Message(export.message_today(today_plan, ftp), attach(today_plan),
                               [(today, "preview", h_today)]))

    if hour >= EVENING_HOUR and not tm.get("preview"):
        out.append(Message(export.message_preview(tomorrow_plan, ftp), attach(tomorrow_plan),
                           [(tomorrow, "preview", h_tomorrow)]))
    return out


def apply(msg, state):
    for day, name, value in msg.marks:
        if day == "_tests":
            state.setdefault("tests", []).append(name)
        else:
            state.setdefault("days", {}).setdefault(day, {})[name] = value


# ---------------------------------------------------------------- canali

def configured():
    return bool((os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))
                or os.environ.get("NTFY_TOPIC"))


def _multipart(fields, name, filename, content):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, k, v)).encode())
    parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
                  'Content-Type: application/octet-stream\r\n\r\n' % (boundary, name, filename)).encode())
    parts.append(content + b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode())
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def _telegram(text, files):
    api = "https://api.telegram.org/bot%s/" % os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    body = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(urllib.request.Request(api + "sendMessage", data=body), timeout=30).read()
    for filename, content in files:
        data, ctype = _multipart({"chat_id": chat}, "document", filename, content)
        req = urllib.request.Request(api + "sendDocument", data=data, headers={"Content-Type": ctype})
        urllib.request.urlopen(req, timeout=60).read()


def _ntfy(text):
    plain = re.sub(r"<[^>]+>", "", text)
    title, _, body = plain.partition("\n")
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    url = "%s/%s?%s" % (server, os.environ["NTFY_TOPIC"], urllib.parse.urlencode({"title": title}))
    urllib.request.urlopen(urllib.request.Request(url, data=(body or title).encode("utf-8")), timeout=30).read()


def send(msg):
    """Invia un messaggio; ritorna None se tutto ok, altrimenti il testo dell'errore."""
    if not msg.text:
        return None
    try:
        if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
            _telegram(msg.text, msg.files)
        if os.environ.get("NTFY_TOPIC"):
            _ntfy(msg.text)
    except urllib.error.HTTPError as err:
        return "Notifica non inviata: HTTP %d" % err.code
    except (urllib.error.URLError, TimeoutError) as err:
        return "Notifica non inviata: %s" % err
    return None
