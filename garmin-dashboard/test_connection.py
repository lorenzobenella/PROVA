"""Passo 3: prova la connessione e mostra le ultime 5 attivita'.

    python test_connection.py          prova completa
    python test_connection.py --check  dice solo se la chiave e' impostata
"""

import sys

import garmin_api as g


def main():
    print("Chiave Garmin:", g.key_status())
    if "--check" in sys.argv:
        return 0 if g.load_key() else 1
    if not g.load_key():
        return 1
    try:
        g.get_token(force=True)
        print("Token ottenuto: la chiave funziona.")
        acts = g.get("/activitylist-service/activities/search/activities",
                      {"start": 0, "limit": 5}) or []
    except g.GarminKeyError as err:
        print("ERRORE:", err)
        return 1
    print("Ultime %d attivita':" % len(acts))
    for act in acts:
        when = (act.get("startTimeLocal") or "?")[:16]
        kind = (act.get("activityType") or {}).get("typeKey", "?")
        km = (act.get("distance") or 0) / 1000
        name = (act.get("activityName") or "").encode("ascii", "replace").decode("ascii")
        print("  %s  %-18s %7.2f km  %s" % (when, kind, km, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
