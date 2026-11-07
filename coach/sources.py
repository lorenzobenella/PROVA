"""Fonti dati: Garmin Connect Personal API (principale) e Strava (facoltativa).

Garmin passa dal client già presente in garmin-dashboard/garmin_api.py (chiave in
GARMIN_PERSONAL_API_KEY). Strava si attiva solo se ci sono STRAVA_CLIENT_ID,
STRAVA_CLIENT_SECRET e STRAVA_REFRESH_TOKEN; le uscite già presenti su Garmin
vengono scartate, così nulla viene contato due volte.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from .config import ROOT
from .models import Activity, Data, Wellness

GARMIN_DIR = ROOT / "garmin-dashboard"
ACTIVITY_DAYS = 120  # abbastanza per una CTL (costante di 42 giorni) affidabile
WELLNESS_DAYS = 35
WEIGHT_DAYS = 60
PAGE = 20  # oltre 45 attività per pagina la Personal API risponde 500

# oltre questi watt la potenza è quasi certamente un errore del sensore
PLAUSIBLE = {60: 1000, 300: 650, 1200: 500}


def _garmin():
    if str(GARMIN_DIR) not in sys.path:
        sys.path.insert(0, str(GARMIN_DIR))
    import garmin_api  # noqa: E402  (modulo della dashboard, solo libreria standard)
    return garmin_api


# ---------------------------------------------------------------- conversione record Garmin

def sport_of(kind):
    kind = kind or ""
    if any(s in kind for s in ("cycling", "biking", "ride")) and "e_bike" not in kind:
        return "bike"
    if kind.startswith("strength"):
        return "strength"
    if "walk" in kind or kind in ("hiking", "mobility", "yoga", "breathwork", "e_bike_fitness"):
        return "light"
    return "other"


def _power(value, sec):
    return value if value and 0 < value <= PLAUSIBLE[sec] else None


def activity_from_garmin(rec):
    text = rec.get("startTimeLocal")
    if not text:
        return None
    start = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    sport = sport_of((rec.get("activityType") or {}).get("typeKey"))
    tss = rec.get("trainingStressScore")
    return Activity(
        day=start.date(), start=start, sport=sport,
        duration_s=float(rec.get("duration") or 0),
        np=rec.get("normPower") or None,
        avg_hr=rec.get("averageHR") or None,
        tss=tss if sport == "bike" and tss and tss > 0 else None,
        p60=_power(rec.get("maxAvgPower_60"), 60),
        p300=_power(rec.get("maxAvgPower_300"), 300),
        p1200=_power(rec.get("maxAvgPower_1200") or rec.get("max20MinPower"), 1200),
        name=rec.get("activityName") or "", source="garmin",
    )


def wellness_from_garmin(day_iso, daily, sleep, hrv):
    d = daily or {}
    s = sleep or {}
    h = hrv or {}
    secs = s.get("sleepTimeSeconds") or 0
    base = h.get("baseline") or {}
    w = Wellness(
        day=date.fromisoformat(day_iso),
        rhr=(d.get("heartRate") or {}).get("restingValue") or None,
        hrv=h.get("lastNightAvg") or None,
        hrv_low=base.get("balancedLow"), hrv_high=base.get("balancedUpper"),
        hrv_status=h.get("status"),
        sleep_h=round(secs / 3600, 2) if secs > 0 else None,
        sleep_score=((s.get("sleepScores") or {}).get("overall") or {}).get("value"),
        body_battery=(d.get("bodyBattery") or {}).get("maxValue"),
        stress=(d.get("stress") or {}).get("avgLevel"),
    )
    return w


def _wellness_map(daily, sleep, hrv):
    out = {}
    for day in sorted(set(daily) | set(sleep) | set(hrv)):
        w = wellness_from_garmin(day, daily.get(day), sleep.get(day), hrv.get(day))
        if any(v is not None for v in (w.rhr, w.hrv, w.sleep_h, w.body_battery)):
            out[w.day] = w
    return out


def _weights(series):
    out = []
    for day, rec in series.items():
        kg = (rec.get("latestWeight") or {}).get("weight")
        kg = kg / 1000 if kg and kg > 1000 else kg
        if kg and 35 <= kg <= 150:  # scarta le pesate impossibili
            out.append((date.fromisoformat(day), round(kg, 1)))
    return sorted(out)


# ---------------------------------------------------------------- Garmin: dal vivo o dai file

def garmin_live(today, data):
    g = _garmin()
    if not g.load_key():
        data.warnings.append("Garmin: GARMIN_PERSONAL_API_KEY non impostata, lavoro senza dati")
        return
    since = today - timedelta(days=ACTIVITY_DAYS)
    try:
        raw, offset = [], 0
        while True:
            page = None
            for _ in range(3):
                try:
                    page = g.get("/activitylist-service/activities/search/activities",
                                 {"start": offset, "limit": PAGE}) or []
                    break
                except g.TooLarge:
                    continue
            if page is None:
                raise g.ApiError("la lista attività risponde 500")
            raw.extend(page)
            offset += len(page)
            oldest = min(((a.get("startTimeLocal") or "9999")[:10] for a in page), default="0000")
            if len(page) < PAGE or oldest < since.isoformat():
                break
        data.activities = [a for a in map(activity_from_garmin, raw) if a and a.day >= since]
        data.has_activities = True
    except (g.GarminKeyError, g.ApiError) as err:
        data.warnings.append("Garmin attività: %s" % err)

    def series(path_fn, days, window):
        out = {}
        end = today
        start = today - timedelta(days=days)
        a = start
        while a <= end:
            b = min(a + timedelta(days=window - 1), end)
            try:
                for rec in g.records_from(path_fn(a.isoformat(), b.isoformat())):
                    day = g.date_of(rec)
                    if day:
                        out[day] = rec
            except (g.ApiError, g.TooLarge) as err:
                data.warnings.append("Garmin %s: %s" % (path_fn.__name__, err))
            a = b + timedelta(days=1)
        return out

    def daily(a, b):
        return g.get("/usersummary-service/v2/dailysummaries", {"startDate": a, "endDate": b})

    def sleep(a, b):
        return g.get("/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": a, "endDate": b})

    def hrv(a, b):
        return g.get("/hrv-service/hrv/daily/%s/%s" % (a, b))

    def weight(a, b):
        return g.get("/weight-service/weight/range/%s/%s" % (a, b))

    try:
        data.wellness = _wellness_map(series(daily, WELLNESS_DAYS, 60), series(sleep, WELLNESS_DAYS, 28),
                                      series(hrv, WELLNESS_DAYS, 60))
        data.weights = _weights(series(weight, WEIGHT_DAYS, 365))
    except g.GarminKeyError as err:
        data.warnings.append("Garmin benessere: %s" % err)


def garmin_offline(today, data):
    """Legge i dati già scaricati da garmin-dashboard/pull.py (per provare il coach in locale)."""
    g = _garmin()
    since = today - timedelta(days=ACTIVITY_DAYS)
    acts = [a for a in map(activity_from_garmin, g.load_activities()) if a]
    data.activities = [a for a in acts if since <= a.day <= today]
    data.has_activities = bool(acts)
    well = _wellness_map(g.load_series("daily"), g.load_series("sleep"), g.load_series("hrv"))
    data.wellness = {d: w for d, w in well.items() if d <= today}
    data.weights = [(d, kg) for d, kg in _weights(g.load_series("weight")) if d <= today]
    if not acts:
        data.warnings.append("Nessun dato in garmin-dashboard/data: lancia prima pull.py")


# ---------------------------------------------------------------- Strava (facoltativo)

STRAVA_API = "https://www.strava.com/api/v3"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_ENV = ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN")
STRAVA_BIKE = {"Ride", "VirtualRide", "GravelRide", "MountainBikeRide", "Handcycle", "Velomobile"}
STRAVA_STRENGTH = {"WeightTraining", "Crossfit"}
STRAVA_LIGHT = {"Walk", "Hike", "Yoga", "EBikeRide", "EMountainBikeRide"}


def strava_configured():
    return all(os.environ.get(k) for k in STRAVA_ENV)


def _http_json(url, data=None, headers=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def activity_from_strava(rec):
    start = datetime.fromisoformat(rec["start_date_local"].replace("Z", ""))
    kind = rec.get("sport_type") or rec.get("type") or ""
    sport = ("bike" if kind in STRAVA_BIKE else "strength" if kind in STRAVA_STRENGTH
             else "light" if kind in STRAVA_LIGHT else "other")
    # la potenza stimata da Strava (senza misuratore) non è affidabile: la ignoro
    np_ = rec.get("weighted_average_watts") if rec.get("device_watts") else None
    return Activity(day=start.date(), start=start, sport=sport,
                    duration_s=float(rec.get("moving_time") or rec.get("elapsed_time") or 0),
                    np=np_, avg_hr=rec.get("average_heartrate"), name=rec.get("name") or "",
                    source="strava")


def strava_live(today, data):
    try:
        token = _http_json(STRAVA_TOKEN_URL, {
            "client_id": os.environ["STRAVA_CLIENT_ID"],
            "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
            "refresh_token": os.environ["STRAVA_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        })
        if token.get("refresh_token") and token["refresh_token"] != os.environ["STRAVA_REFRESH_TOKEN"]:
            data.warnings.append("Strava ha cambiato il refresh token: aggiorna il secret STRAVA_REFRESH_TOKEN")
        headers = {"Authorization": "Bearer " + token["access_token"]}
        after = int(datetime.combine(today - timedelta(days=ACTIVITY_DAYS), datetime.min.time()).timestamp())
        out, page = [], 1
        while True:
            batch = _http_json("%s/athlete/activities?%s" % (STRAVA_API, urllib.parse.urlencode(
                {"after": after, "per_page": 100, "page": page})), headers=headers)
            out.extend(activity_from_strava(r) for r in batch)
            if len(batch) < 100:
                break
            page += 1
        return out
    except (urllib.error.URLError, KeyError, ValueError) as err:
        data.warnings.append("Strava: %s" % err)
        return []


def merge(primary, extra, tolerance_min=10):
    """Aggiunge a primary le attività di extra che non ci sono già (stesso inizio +-10')."""
    out = list(primary)
    for a in extra:
        dup = any(abs((a.start - b.start).total_seconds()) <= tolerance_min * 60 for b in primary)
        if not dup:
            out.append(a)
    return sorted(out, key=lambda a: a.start)


def collect(today, offline=False):
    data = Data()
    (garmin_offline if offline else garmin_live)(today, data)
    if not offline and strava_configured():
        extra = strava_live(today, data)
        data.activities = merge(data.activities, [a for a in extra if a.day <= today])
        data.has_activities = data.has_activities or bool(extra)
    data.activities.sort(key=lambda a: a.start)
    return data
