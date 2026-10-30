"""Client minimo per la Garmin Connect Personal API (sola lettura).

Usa solo la libreria standard di Python.
La chiave si legge dalla variabile d'ambiente GARMIN_PERSONAL_API_KEY oppure,
se manca, dal file .env.local accanto a questo script. Non viene mai stampata.
Tutti i messaggi a console sono in ASCII, cosi' la console di Windows non va in errore.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ENV_FILE = HERE / ".env.local"
KEY_NAME = "GARMIN_PERSONAL_API_KEY"

API = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
TOKEN_FILE = DATA / "token.json"
TOKEN_MAX_AGE = 20 * 3600  # il token dura 24 ore: ne chiediamo uno nuovo dopo 20
PAUSE = 0.4  # pausa breve tra una chiamata e l'altra


class GarminKeyError(Exception):
    """Chiave mancante, sbagliata o scaduta."""


class TooLarge(Exception):
    """HTTP 500: di solito la risposta supera ~256 KB e va chiesto un intervallo piu' piccolo."""


class ApiError(Exception):
    pass


# ---------------------------------------------------------------- chiave e token

def load_key():
    value = os.environ.get(KEY_NAME, "").strip()
    if not value and ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(KEY_NAME + "="):
                value = line.split("=", 1)[1].strip()
                break
    return value.strip("\"'")


def key_status():
    return "impostata" if load_key() else "NON impostata"


def _key_id(key):
    # Impronta corta della chiave: serve solo a capire se e' cambiata. Non e' reversibile.
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _new_token():
    key = load_key()
    if not key:
        raise GarminKeyError(
            "La chiave non e' impostata. Mettila nella variabile d'ambiente "
            + KEY_NAME + " oppure in .env.local dopo il segno =."
        )
    req = urllib.request.Request(
        API + TOKEN_PATH, method="POST",
        headers={"Authorization": "ApiKey " + key, "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8").strip()
    except urllib.error.HTTPError as err:
        raise GarminKeyError(
            "Garmin ha rifiutato la chiave (HTTP %d): manca, e' sbagliata, "
            "e' scaduta o e' stata cancellata." % err.code
        ) from None
    token = body.strip('"')
    if body.startswith("{"):  # per sicurezza, nel caso la risposta arrivi come JSON
        parsed = json.loads(body)
        token = parsed.get("access_token") or parsed.get("token") or ""
    if not token:
        raise GarminKeyError("Garmin ha risposto senza token.")
    DATA.mkdir(exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "token": token, "obtained_at": time.time(), "key_id": _key_id(key),
    }), encoding="utf-8")
    os.chmod(TOKEN_FILE, 0o600)
    return token


def get_token(force=False):
    if not force and TOKEN_FILE.exists():
        try:
            saved = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            fresh = time.time() - saved["obtained_at"] < TOKEN_MAX_AGE
            same_key = saved.get("key_id") == _key_id(load_key())
            if fresh and same_key:
                return saved["token"]
        except (ValueError, KeyError):
            pass
    return _new_token()


# ---------------------------------------------------------------- richieste

def get(path, params=None, accept="application/json", raw=False):
    """GET su personal.connectapi.garmin.com.

    Ritorna il JSON decodificato (o i byte se raw=True), None se Garmin risponde 204
    ("nessun dato in questo intervallo"). Solleva TooLarge su HTTP 500.
    """
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    token = get_token()
    refreshed = False
    for attempt in range(6):
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + token, "Accept": accept,
        })
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                status, body = resp.status, resp.read()
        except urllib.error.HTTPError as err:
            status, body = err.code, (err.read() or b"")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            status, body = None, str(err).encode("ascii", "replace")
        time.sleep(PAUSE)

        if status == 200:
            if raw:
                return body
            return json.loads(body.decode("utf-8")) if body.strip() else None
        if status == 204:
            return None
        if status == 401 and not refreshed:
            token = get_token(force=True)
            refreshed = True
            continue
        if status == 401:
            raise GarminKeyError("Garmin risponde 401 anche con un token nuovo: ricontrolla la chiave.")
        if status == 500:
            raise TooLarge(path)
        if status in (None, 429, 502, 503, 504):
            wait = 2 ** (attempt + 1)
            print("  Garmin risponde %s, riprovo tra %d secondi..." % (status or "errore di rete", wait))
            time.sleep(wait)
            continue
        raise ApiError("HTTP %s su %s: %s" % (status, path, ascii(body[:200])))
    raise ApiError("Troppi tentativi falliti su " + path)


# ---------------------------------------------------------------- lettura dei dati salvati

DATE_KEYS = ("calendarDate", "summaryDate", "date", "calendarDay", "sleepDate")
LIST_KEYS = ("data", "dailySleepSummaries", "sleepSummaries", "hrvSummaries", "dailyWeightSummaries")


def date_of(record):
    for key in DATE_KEYS:
        value = record.get(key)
        if isinstance(value, str) and len(value) >= 10:
            return value[:10]
        if isinstance(value, (int, float)) and value > 10 ** 11:  # epoch in millisecondi
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    return None


def records_from(response):
    """Estrae la lista di record giornalieri da una risposta, qualunque sia la sua forma."""
    if response is None:
        return []
    if isinstance(response, list):
        return [r for r in response if isinstance(r, dict)]
    if isinstance(response, dict):
        for key in LIST_KEYS:
            if isinstance(response.get(key), list):
                return [r for r in response[key] if isinstance(r, dict)]
        for value in response.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
        return [response] if date_of(response) else []
    return []


def load_series(name):
    """Tutti i record di una serie (daily, sleep, hrv, weight) come {data: record}.

    Se una data compare in piu' file vince quello scaricato piu' di recente.
    """
    folder = DATA / name
    if not folder.exists():
        return {}
    chunks = []
    for path in folder.glob("*.json"):
        with open(path, encoding="utf-8") as fh:
            chunks.append(json.load(fh))
    chunks.sort(key=lambda c: c.get("fetched_at", ""))
    out = {}
    for chunk in chunks:
        for part in chunk.get("parts", []):
            for rec in records_from(part.get("response")):
                day = date_of(rec)
                if day:
                    out[day] = rec
    return dict(sorted(out.items()))


def load_activities():
    folder = DATA / "activities"
    acts = {}
    if folder.exists():
        for path in folder.glob("*.json"):
            with open(path, encoding="utf-8") as fh:
                acts.update(json.load(fh))
    return sorted(acts.values(), key=lambda a: a.get("startTimeLocal") or "")


def load_json(name):
    path = DATA / name
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def today():
    return date.today()


def iso(d):
    return d.isoformat()


def parse_day(text):
    return datetime.strptime(text[:10], "%Y-%m-%d").date()

