"""Passo 5: scarica lo storico Garmin in data/ come JSON.

    python pull.py                  ultimi 12 mesi (le volte dopo scarica solo il nuovo)
    python pull.py --all            tutto lo storico
    python pull.py --since 2020-01-01
    python pull.py --fit 12345678   scarica il file FIT originale di un'attivita'

Ogni finestra viene salvata subito su disco: se lo script si ferma, rilancialo e
riparte da dove era arrivato.
"""

import argparse
import json
import sys
from datetime import date, timedelta

import garmin_api as g

EPOCH = date(2000, 1, 1)  # le finestre sono allineate a questa data, cosi' i nomi dei file restano stabili
SETTLE_DAYS = 3  # una finestra e' "chiusa" se scaricata almeno 3 giorni dopo la sua fine
PAGE = 20  # oltre 45 attivita' per pagina il server risponde 500

SERIES = [
    # nome, etichetta, giorni per finestra, funzione che scarica [a, b]
    ("daily", "riepiloghi giornalieri", 60,
     lambda a, b: g.get("/usersummary-service/v2/dailysummaries", {"startDate": a, "endDate": b})),
    ("sleep", "sonno", 31,
     lambda a, b: g.get("/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": a, "endDate": b})),
    ("hrv", "HRV", 365,
     lambda a, b: g.get("/hrv-service/hrv/daily/%s/%s" % (a, b))),
    ("weight", "peso", 365,
     lambda a, b: g.get("/weight-service/weight/range/%s/%s" % (a, b))),
]


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    tmp.replace(path)


# ---------------------------------------------------------------- serie per intervallo di date

def chunks(start, end, days):
    i = (start - EPOCH).days // days
    while True:
        a = EPOCH + timedelta(days=i * days)
        if a > end:
            return
        yield a, a + timedelta(days=days - 1)
        i += 1


def fetch_split(fn, a, b, label):
    """Scarica [a, b]; se Garmin risponde 500 dimezza l'intervallo e riprova."""
    try:
        return [{"from": g.iso(a), "to": g.iso(b), "response": fn(g.iso(a), g.iso(b))}], 0
    except g.TooLarge:
        n = (b - a).days + 1
        if n <= 1:
            print("  [%s] %s: errore 500 anche su un giorno solo, lo salto" % (label, a))
            return [{"from": g.iso(a), "to": g.iso(b), "response": None, "error": 500}], 1
        mid = a + timedelta(days=n // 2 - 1)
        print("  [%s] %s -> %s: risposta troppo grande, dimezzo" % (label, a, b))
        left, e1 = fetch_split(fn, a, mid, label)
        right, e2 = fetch_split(fn, mid + timedelta(days=1), b, label)
        return left + right, e1 + e2


def is_closed(chunk_file, b):
    if not chunk_file.exists():
        return False
    with open(chunk_file, encoding="utf-8") as fh:
        saved = json.load(fh)
    if saved.get("errors"):
        return False
    return g.parse_day(saved["fetched_at"]) >= b + timedelta(days=SETTLE_DAYS)


def pull_series(name, label, days, fn, start, end):
    todo = []
    for a, b in chunks(start, end, days):
        path = g.DATA / name / ("%s_%s.json" % (a, b))
        if not is_closed(path, b):
            todo.append((a, b, path))
    if not todo:
        print("[%s] gia' aggiornato" % label)
        return
    print("[%s] %d finestre da scaricare" % (label, len(todo)))
    for i, (a, b, path) in enumerate(todo, 1):
        parts, errors = fetch_split(fn, a, min(b, end), label)
        write_json(path, {"from": g.iso(a), "to": g.iso(b), "fetched_at": g.iso(g.today()),
                          "errors": errors, "parts": parts})
        found = sum(len(g.records_from(p["response"])) for p in parts)
        print("  [%s] %d/%d  %s -> %s  %d giorni con dati" % (label, i, len(todo), a, min(b, end), found))


# ---------------------------------------------------------------- attivita'

def pull_activities(start):
    """start=None vuol dire tutto lo storico."""
    folder = g.DATA / "activities"
    state_path = g.DATA / "activities_state.json"
    state = g.load_json("activities_state.json") or {}
    months = {}
    known = set()
    if folder.exists():
        for path in folder.glob("*.json"):
            with open(path, encoding="utf-8") as fh:
                months[path.stem] = json.load(fh)
            known.update(months[path.stem].keys())

    def covered():
        since = state.get("covered_since")
        if since == "all":
            return True
        return bool(since) and start is not None and g.parse_day(since) <= start

    phase = "nuove" if known else "storico"
    offset = 0
    print("[attivita'] %d gia' su disco, cerco le %s" % (len(known), phase))
    while True:
        page = None
        for _ in range(3):
            try:
                page = g.get("/activitylist-service/activities/search/activities",
                             {"start": offset, "limit": PAGE}) or []
                break
            except g.TooLarge:
                print("  [attivita'] errore 500 a quota %d, riprovo" % offset)
        if page is None:
            raise g.ApiError("La lista attivita' continua a rispondere 500 a quota %d" % offset)

        touched = set()
        hit_known = False
        for act in page:
            act_id = str(act.get("activityId"))
            hit_known = hit_known or act_id in known
            month = (act.get("startTimeLocal") or "0000-00")[:7]
            months.setdefault(month, {})[act_id] = act
            known.add(act_id)
            touched.add(month)
        for month in touched:
            write_json(folder / ("%s.json" % month), months[month])
        offset += len(page)
        oldest = min((a.get("startTimeLocal") or "9999") for a in page) if page else "-"
        print("  [attivita'] %d scaricate, la piu' vecchia di questa pagina: %s" % (offset, oldest[:10]))

        if len(page) < PAGE:
            state["covered_since"] = "all"
            break
        if phase == "nuove" and hit_known:
            if covered():
                break
            phase = "storico"
            offset = max(offset, len(known) - PAGE)  # salto in fondo a quello che ho gia', con un po' di sovrapposizione
            print("[attivita'] riprendo lo storico da quota %d" % offset)
            continue
        if phase == "storico" and start is not None and oldest < g.iso(start):
            state["covered_since"] = g.iso(start)
            break
    write_json(state_path, state)


# ---------------------------------------------------------------- dati singoli

def pull_singletons():
    for filename, path in (
        ("hrzones.json", "/biometric-service/heartRateZones/v2"),
        ("fitnessage.json", "/fitnessage-service/fitnessage/v2/%s" % g.iso(g.today())),
    ):
        try:
            write_json(g.DATA / filename, g.get(path))
        except (g.ApiError, g.TooLarge) as err:
            print("  [%s] non disponibile: %s" % (filename, err))
    print("[zone cardio ed eta' fitness] fatto")


def pull_fit(activity_id):
    body = g.get("/download-service/files/activity/%s" % activity_id,
                 accept="application/x-zip-compressed", raw=True)
    path = g.DATA / "fit" / ("%s.zip" % activity_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body or b"")
    print("File FIT salvato in", path)


# ---------------------------------------------------------------- riepilogo

def summary():
    acts = g.load_activities()
    print("")
    print("RIEPILOGO")
    if acts:
        print("  Attivita': %d, dal %s al %s" % (
            len(acts), (acts[0].get("startTimeLocal") or "?")[:10],
            (acts[-1].get("startTimeLocal") or "?")[:10]))
    for name, label in (("daily", "Giorni"), ("sleep", "Notti di sonno"),
                        ("hrv", "Notti con HRV"), ("weight", "Pesate")):
        series = g.load_series(name)
        if name == "sleep":
            series = {d: r for d, r in series.items() if (r.get("sleepTimeSeconds") or 0) > 0}
        if name == "daily":
            series = {d: r for d, r in series.items() if r.get("movement") or r.get("heartRate")}
        if series:
            days = list(series)
            print("  %s: %d, dal %s al %s" % (label, len(days), days[0], days[-1]))
        else:
            print("  %s: nessun dato" % label)


def main():
    parser = argparse.ArgumentParser(description="Scarica i dati dalla Garmin Personal API")
    parser.add_argument("--all", action="store_true", help="tutto lo storico")
    parser.add_argument("--since", help="data di inizio YYYY-MM-DD")
    parser.add_argument("--fit", help="scarica il file FIT di questa attivita'")
    args = parser.parse_args()

    try:
        if args.fit:
            pull_fit(args.fit)
            return 0
        end = g.today()
        if args.all:
            start = None
        elif args.since:
            start = g.parse_day(args.since)
        else:
            start = end - timedelta(days=365)

        pull_activities(start)
        if start is None:
            acts = g.load_activities()
            start = g.parse_day(acts[0]["startTimeLocal"]) if acts else date(2006, 1, 1)
            print("[storico completo] dati di salute dal %s" % start)

        for name, label, days, fn in SERIES:
            series_start = start
            if name in ("sleep", "hrv"):
                # sonno e HRV non possono iniziare prima dei riepiloghi giornalieri
                daily = [d for d, r in g.load_series("daily").items()
                         if r.get("movement") or r.get("heartRate")]
                if daily:
                    series_start = max(start, g.parse_day(daily[0]))
            try:
                pull_series(name, label, days, fn, series_start, end)
            except g.ApiError as err:
                print("  [%s] salto questa serie: %s" % (label, err))
        pull_singletons()
    except (g.GarminKeyError, g.ApiError) as err:
        print("ERRORE:", err)
        return 1
    summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
