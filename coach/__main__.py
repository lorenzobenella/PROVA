"""python -m coach: scarica i dati, programma la giornata, scrive i file e (se configurato) ti avvisa.

    python -m coach                       esecuzione normale (quella di GitHub Actions)
    python -m coach --offline             usa i dati già scaricati da garmin-dashboard/pull.py
    python -m coach --offline --date 2026-11-03 --hour 7 --no-notify   simula un altro giorno
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config, export, notify, sources
from .planner import Planner, day_label


def _plan_json(p, ftp):
    r = p.readiness
    return {
        "giorno": p.day.isoformat(), "fase": p.phase, "seduta": p.workout.title, "tipo": p.workout.kind,
        "minuti": round(p.workout.minutes), "tss": round(p.workout.tss), "stato": p.status,
        "prontezza": None if r is None else {"livello": r.level, "punteggio": r.score,
                                             "provvisoria": r.provisional, "motivi": r.reasons},
        "modifiche": p.changes, "dettaglio": export.describe(p.workout, ftp), "note": p.workout.notes,
    }


def write_outputs(out_dir, planner, today_plan, upcoming, warnings, new_tests):
    out_dir = Path(out_dir)
    wdir = out_dir / "allenamenti"
    wdir.mkdir(parents=True, exist_ok=True)
    ftp = planner.ftp_at(planner.today)
    md = export.markdown(today_plan, upcoming, planner.week_so_far(), planner.indicators(), warnings, new_tests, ftp)
    (out_dir / "PIANO.md").write_text(md, encoding="utf-8")
    (out_dir / "piano.json").write_text(json.dumps(
        {"oggi": _plan_json(today_plan, ftp), "prossimi": [_plan_json(u, ftp) for u in upcoming]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    zwo_files, keep = {}, set()
    for p in (today_plan, upcoming[0]):
        if not p.workout.is_bike:
            continue
        stem = export.file_stem(p)
        name = "%s - %s" % (day_label(p.day, False), p.workout.title)
        z = export.zwo(p.workout, name, p.workout.purpose).encode("utf-8")
        (wdir / (stem + ".zwo")).write_bytes(z)
        (wdir / (stem + ".erg")).write_text(export.erg(p.workout, planner.ftp_at(p.day), name), encoding="utf-8")
        keep |= {stem + ".zwo", stem + ".erg"}
        zwo_files[p.day] = (stem + ".zwo", z)
    for f in wdir.iterdir():
        if f.name not in keep:
            f.unlink()
    return md, zwo_files


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m coach", description="Allenatore virtuale di ciclismo")
    ap.add_argument("--offline", action="store_true", help="usa i dati già scaricati in garmin-dashboard/data")
    ap.add_argument("--date", help="simula un altro giorno (AAAA-MM-GG)")
    ap.add_argument("--hour", type=int, help="simula l'ora locale (conta per le notifiche)")
    ap.add_argument("--no-notify", action="store_true", help="non inviare notifiche")
    ap.add_argument("--out", default=str(config.OUT_DIR), help="cartella di uscita")
    ap.add_argument("--state", default=str(config.STATE_FILE), help="file di memoria delle notifiche")
    ap.add_argument("--quiet", action="store_true",
                    help="non stampa dati di salute (attivo da solo su GitHub Actions, dove i log sono pubblici)")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # la console di Windows non conosce le emoji
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    quiet = args.quiet or bool(os.environ.get("GITHUB_ACTIONS"))
    simulated = bool(args.date) or args.offline

    cfg = config.load()
    tz = ZoneInfo(cfg.tz)
    now = datetime.now(tz)
    today = date.fromisoformat(args.date) if args.date else now.date()
    if args.date or args.hour is not None:
        now = datetime.combine(today, time(args.hour if args.hour is not None else now.hour), tzinfo=tz)

    data = sources.collect(today, offline=args.offline)
    tests = config.load_tests()
    planner = Planner(cfg, data, tests, today)
    old_ftp = planner.ftp_at(today)

    new_tests = planner.detect_tests()
    if new_tests and not simulated:
        for d, watts, ok in new_tests:
            if ok:
                config.append_test(d, watts, "ramp test (letto dal coach)")
        planner = Planner(cfg, data, config.load_tests(), today)

    today_plan = planner.plan(today)
    upcoming = planner.upcoming()
    md, zwo_files = write_outputs(args.out, planner, today_plan, upcoming, data.warnings, new_tests)

    sent = 0
    if not args.no_notify and notify.configured():
        state = notify.load_state(args.state)
        msgs = notify.decide(state, now, today_plan, upcoming[0], planner.ftp_at(today), zwo_files,
                             new_tests, old_ftp)
        for msg in msgs:
            err = notify.send(msg)
            if err:
                data.warnings.append(err)
                continue
            notify.apply(msg, state)
            sent += bool(msg.text)
        notify.save_state(args.state, state, today)
    elif not args.no_notify and not quiet:
        print("(nessun canale di notifica configurato: imposta TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID o NTFY_TOPIC)")

    if quiet:
        print("Piano scritto in %s. Notifiche inviate: %d." % (args.out, sent))
    else:
        print(md)
    for w in data.warnings:
        print("AVVISO:", w, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
