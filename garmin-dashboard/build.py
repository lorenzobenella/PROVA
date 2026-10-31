"""Passo 6: costruisce dashboard.html (un solo file, senza server) dai dati in data/.

Obiettivo della dashboard: alzare FTP e VO2 max in bici.
- FTP Garmin: ricostruita da ogni uscita con potenza come NP / IF, perche' Garmin
  calcola l'Intensity Factor con la FTP impostata in quel momento.
- FTP stimata: 95% della miglior potenza di 20 minuti negli ultimi 90 giorni.
- VO2 max stimato: 16,6 + 8,87 x (potenza 5 minuti / peso), formula di Sitko et al. 2021.
  La Personal API non espone il VO2 max dell'orologio: questa e' una stima.
"""

import bisect
import json
import re
import statistics
from collections import defaultdict
from datetime import timedelta

import garmin_api as g

OUT = g.HERE / "dashboard.html"
PUBLISH = g.HERE / "dashboard_publish.html"  # stessa pagina senza involucro, per pubblicarla come Artifact
TEMPLATE = g.HERE / "dashboard_template.html"

DURATIONS = [1, 5, 10, 20, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200]
# oltre questi valori (watt) la potenza e' quasi certamente un errore del sensore
PLAUSIBLE = {1: 2500, 5: 2000, 10: 1800, 20: 1500, 30: 1400, 60: 1000, 120: 800,
             300: 650, 600: 550, 1200: 500, 1800: 480, 3600: 450, 7200: 420}
SPORTS = ["bike", "walk", "strength", "other"]


def r1(v):
    return None if v is None else round(v, 1)


def sport_of(act):
    kind = (act.get("activityType") or {}).get("typeKey", "")
    if any(s in kind for s in ("cycling", "biking", "ride")):
        return "bike"
    if "walk" in kind or kind == "hiking":
        return "walk"
    if kind.startswith("strength"):
        return "strength"
    return "other"


def day_of(act):
    return g.parse_day(act["startTimeLocal"])


def monday(d):
    return d - timedelta(days=d.weekday())


def power(act, sec):
    value = act.get("maxAvgPower_%d" % sec)
    if not value and sec == 1200:
        value = act.get("max20MinPower")
    if not value or value <= 0 or value > PLAUSIBLE[sec]:
        return None
    return value


class Steps:
    """Valore piu' recente alla data d (o il primo disponibile se d e' prima di tutti)."""

    def __init__(self, pairs):
        pairs = sorted(pairs)
        self.days = [p[0] for p in pairs]
        self.vals = [p[1] for p in pairs]

    def at(self, d, fallback_first=True):
        if not self.days:
            return None
        i = bisect.bisect_right(self.days, d) - 1
        if i < 0:
            return self.vals[0] if fallback_first else None
        return self.vals[i]


def garmin_ftp(bike):
    raw = []
    for act in bike:
        np_, if_ = act.get("normPower"), act.get("intensityFactor") or 0
        if np_ and if_ > 0.2:
            raw.append((day_of(act), np_ / if_))
    kept = []
    for i, (d, v) in enumerate(raw):
        near = [x[1] for x in raw[max(0, i - 7): i + 8]]
        med = statistics.median(near)
        if abs(v - med) / med <= 0.12:  # scarta le uscite con un sensore diverso o IF anomalo
            kept.append((d, v))
    # un valore per settimana (mediana), cosi' l'arrotondamento dell'IF non fa rumore
    weeks = defaultdict(list)
    for d, v in kept:
        weeks[monday(d)].append(v)
    return Steps([(w + timedelta(days=6), round(statistics.median(v))) for w, v in weeks.items()])


def ftp_since(steps, now, tol=3):
    """Primo giorno della serie di settimane consecutive con la FTP attuale (+-3 W di arrotondamento)."""
    if now is None:
        return None
    since = None
    for d, v in reversed(list(zip(steps.days, steps.vals))):
        if abs(v - now) > tol:
            break
        since = d - timedelta(days=6)
    return since and since.isoformat()


def rolling_best(acts, sec, end, days=90):
    start = end - timedelta(days=days - 1)
    best, when, name = None, None, None
    for act in acts:
        d = day_of(act)
        if start <= d <= end:
            p = power(act, sec)
            if p and (best is None or p > best):
                best, when, name = p, d, act.get("activityName")
    return best, when, name


def main():
    acts = [a for a in g.load_activities() if a.get("startTimeLocal")]
    if not acts:
        print("Nessuna attivita' in data/: lancia prima pull.py")
        return 1
    today = g.today()
    bike = [a for a in acts if sport_of(a) == "bike"]
    bike_pw = [a for a in bike if power(a, 300) or power(a, 1200)]

    weights = []
    for d, rec in g.load_series("weight").items():
        w = (rec.get("latestWeight") or {}).get("weight")
        kg = (w / 1000 if w > 1000 else w) if w else None
        if kg and 35 <= kg <= 150:  # scarta pesate impossibili (es. 8 kg inserito per errore)
            weights.append((g.parse_day(d), kg))
    weight = Steps(weights)
    ftp_g = garmin_ftp(bike)

    # TSS ricalcolato con una FTP coerente nel tempo (95% del miglior 20' sugli ultimi 180 giorni):
    # il TSS di Garmin usa la FTP impostata, che fino al 2021 era troppo bassa e gonfiava tutto.
    ref_cache = {}

    def ftp_ref(d):
        if d not in ref_cache:
            b20 = rolling_best(bike_pw, 1200, d, days=180)[0]
            ref_cache[d] = 0.95 * b20 if b20 else ftp_g.at(d)
        return ref_cache[d]

    def tss(act):
        np_, secs = act.get("normPower"), act.get("duration") or 0
        if not np_ or not secs or sport_of(act) != "bike":
            return 0.0
        ref = ftp_ref(day_of(act))
        return secs * np_ * np_ / (ref * ref * 36) if ref else 0.0

    def vo2(p5, d):
        kg = weight.at(d)
        return 16.6 + 8.87 * p5 / kg if p5 and kg else None

    # ------------------------------------------------ serie settimanali
    first = monday(day_of(acts[0]))
    weeks = []
    w = first
    while w <= today:
        weeks.append(w)
        w += timedelta(days=7)
    idx = {w: i for i, w in enumerate(weeks)}
    n = len(weeks)
    weekly = {k: [0.0] * n for k in ("h_bike", "h_walk", "h_strength", "h_other",
                                       "z3", "z4", "z5", "tss")}
    for act in acts:
        i = idx[monday(day_of(act))]
        weekly["h_" + sport_of(act)][i] += (act.get("duration") or 0) / 3600
        if sport_of(act) == "bike":
            weekly["tss"][i] += tss(act)
            weekly["z3"][i] += (act.get("powerTimeInZone_3") or 0) / 60
            weekly["z4"][i] += (act.get("powerTimeInZone_4") or 0) / 60
            weekly["z5"][i] += sum((act.get("powerTimeInZone_%d" % z) or 0) for z in (5, 6, 7)) / 60
    for k in weekly:
        weekly[k] = [r1(v) for v in weekly[k]]

    weekly["ftp_garmin"], weekly["ftp_est"], weekly["p5"], weekly["vo2"] = [], [], [], []
    for w in weeks:
        end = min(w + timedelta(days=6), today)
        b20 = rolling_best(bike_pw, 1200, end)[0]
        b5 = rolling_best(bike_pw, 300, end)[0]
        weekly["ftp_garmin"].append(ftp_g.at(end, fallback_first=False))
        weekly["ftp_est"].append(r1(0.95 * b20) if b20 else None)
        weekly["p5"].append(r1(b5))
        weekly["vo2"].append(r1(vo2(b5, end)))
    weekly["t"] = [w.isoformat() for w in weeks]

    # ------------------------------------------------ carico (CTL / ATL dal TSS)
    tss_day = defaultdict(float)
    for act in acts:
        tss_day[day_of(act)] += tss(act)
    pmc = {"t": [], "ctl": [], "atl": []}
    ctl = atl = 0.0
    d = day_of(acts[0])
    while d <= today:
        ctl += (tss_day[d] - ctl) / 42
        atl += (tss_day[d] - atl) / 7
        pmc["t"].append(d.isoformat())
        pmc["ctl"].append(r1(ctl))
        pmc["atl"].append(r1(atl))
        d += timedelta(days=1)

    # ------------------------------------------------ recupero giorno per giorno
    daily = g.load_series("daily")
    sleep = g.load_series("sleep")
    hrv = g.load_series("hrv")
    days = sorted(set(daily) | set(sleep) | set(hrv))
    health = {k: [] for k in ("t", "rhr", "hrv", "hrv_lo", "hrv_hi", "sleep", "score")}
    for day in days:
        rhr = ((daily.get(day) or {}).get("heartRate") or {}).get("restingValue")
        s = sleep.get(day) or {}
        secs = s.get("sleepTimeSeconds") or 0
        score = ((s.get("sleepScores") or {}).get("overall") or {}).get("value")
        h = hrv.get(day) or {}
        base = h.get("baseline") or {}
        row = (rhr or None, h.get("lastNightAvg"), base.get("balancedLow"), base.get("balancedUpper"),
               r1(secs / 3600) if secs > 0 else None, score)
        if any(v is not None for v in row):
            health["t"].append(day)
            for k, v in zip(("rhr", "hrv", "hrv_lo", "hrv_hi", "sleep", "score"), row):
                health[k].append(v)

    # ------------------------------------------------ in alto: obiettivo e numeri chiave
    def ago(days_back):
        return today - timedelta(days=days_back)

    b20_now, b20_day, _ = rolling_best(bike_pw, 1200, today)
    b5_now, b5_day, _ = rolling_best(bike_pw, 300, today)
    b5_old = rolling_best(bike_pw, 300, ago(365))[0]
    b20_old = rolling_best(bike_pw, 1200, ago(365))[0]
    kg_now = weight.at(today)
    ftp_now = ftp_g.at(today, fallback_first=False)
    hero = {
        "ftp": ftp_now, "ftp_90": ftp_g.at(ago(90), False), "ftp_365": ftp_g.at(ago(365), False),
        "ftp_since": ftp_since(ftp_g, ftp_now),
        "ftp_max": max(ftp_g.vals) if ftp_g.vals else None,
        "ftp_max_day": ftp_g.days[ftp_g.vals.index(max(ftp_g.vals))].isoformat() if ftp_g.vals else None,
        "kg": r1(kg_now), "kg_day": weight.days[-1].isoformat() if weight.days else None,
        "ftp_est": r1(0.95 * b20_now) if b20_now else None, "b20_day": b20_day and b20_day.isoformat(),
        "ftp_est_365": r1(0.95 * b20_old) if b20_old else None,
        "p5": r1(b5_now), "p5_day": b5_day and b5_day.isoformat(), "p5_365": r1(b5_old),
        "vo2": r1(vo2(b5_now, today)), "vo2_365": r1(vo2(b5_old, ago(365))),
        "ctl": pmc["ctl"][-1], "atl": pmc["atl"][-1],
    }

    # ------------------------------------------------ questa settimana contro le 4 prima
    this_w = monday(today)
    prev = [this_w - timedelta(days=7 * k) for k in range(1, 5)]
    week = {"start": this_w.isoformat(), "days": (today - this_w).days + 1, "sports": {}}
    for sport in SPORTS:
        def agg(ws):
            sel = [a for a in acts if sport_of(a) == sport and monday(day_of(a)) in ws]
            return (sum((a.get("duration") or 0) for a in sel) / 3600,
                    sum((a.get("distance") or 0) for a in sel) / 1000, len(sel))
        now = agg({this_w})
        before = agg(set(prev))
        week["sports"][sport] = {"h": r1(now[0]), "km": r1(now[1]), "n": now[2],
                                 "h4": r1(before[0] / 4), "km4": r1(before[1] / 4), "n4": r1(before[2] / 4)}
    i_now = idx.get(this_w)
    q = lambda i: (weekly["z4"][i] or 0) + (weekly["z5"][i] or 0)
    prev_i = [idx[w] for w in prev if w in idx]
    week["q"] = r1(q(i_now)) if i_now is not None else 0
    week["q4"] = r1(sum(q(i) for i in prev_i) / 4)
    week["tss"] = weekly["tss"][i_now] if i_now is not None else 0
    week["tss4"] = r1(sum(weekly["tss"][i] or 0 for i in prev_i) / 4)

    # ------------------------------------------------ curva di potenza
    def curve(start, end):
        out = []
        for sec in DURATIONS:
            vals = [power(a, sec) for a in bike_pw if start <= day_of(a) <= end]
            vals = [v for v in vals if v]
            out.append(r1(max(vals)) if vals else None)
        return out

    alltime = []
    for sec in DURATIONS:
        best = max(((power(a, sec), a) for a in bike_pw if power(a, sec)), default=(None, None),
                   key=lambda x: x[0])
        alltime.append([r1(best[0]), best[1] and best[1]["startTimeLocal"][:10],
                        best[1] and best[1].get("activityName")])
    curve_data = {"durations": DURATIONS, "last90": curve(ago(89), today),
                  "yearago": curve(ago(365 + 89), ago(365)), "alltime": alltime}

    # ------------------------------------------------ record e giornate piu' grandi
    def top(items, key):
        items = [x for x in items if key(x)]
        return max(items, key=key) if items else None

    records = []
    for sec, label in ((5, "Potenza 5 secondi"), (60, "Potenza 1 minuto"), (300, "Potenza 5 minuti"),
                       (1200, "Potenza 20 minuti"), (3600, "Potenza 60 minuti")):
        a = top(bike_pw, lambda x, s=sec: power(x, s))
        if a:
            records.append({"label": label, "value": round(power(a, sec)), "unit": "W",
                            "date": a["startTimeLocal"][:10], "name": a.get("activityName")})
    outdoor = [a for a in bike if "indoor" not in a["activityType"]["typeKey"]
               and "virtual" not in a["activityType"]["typeKey"]]
    for label, key, unit, conv in (
        ("Uscita più lunga (strada)", lambda a: a.get("distance"), "km", lambda v: round(v / 1000, 1)),
        ("Più dislivello in un'uscita", lambda a: a.get("elevationGain"), "m", lambda v: round(v)),
    ):
        a = top(outdoor, key)
        if a:
            records.append({"label": label, "value": conv(key(a)), "unit": unit,
                            "date": a["startTimeLocal"][:10], "name": a.get("activityName")})
    a = top(bike, tss)
    if a:
        records.append({"label": "Uscita più dura (TSS)", "value": round(tss(a)),
                        "unit": "TSS", "date": a["startTimeLocal"][:10], "name": a.get("activityName")})
    i = max(range(n), key=lambda k: weekly["h_bike"][k] or 0)
    records.append({"label": "Settimana con più ore in bici", "value": weekly["h_bike"][i], "unit": "h",
                    "date": weeks[i].isoformat(), "name": ""})
    i = max(range(len(pmc["ctl"])), key=lambda k: pmc["ctl"][k])
    records.append({"label": "Fitness (CTL) più alta", "value": round(pmc["ctl"][i]), "unit": "",
                    "date": pmc["t"][i], "name": ""})

    payload = {"generated": today.isoformat(), "hero": hero, "week": week, "curve": curve_data,
               "records": records, "weekly": weekly, "pmc": pmc, "health": health,
               "counts": {"acts": len(acts), "bike": len(bike), "first": acts[0]["startTimeLocal"][:10]}}
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
    OUT.write_text(html, encoding="utf-8")
    head = html.split("<head>", 1)[1].split("</head>", 1)[0]
    head = re.sub(r'\s*<meta (charset|name="viewport")[^>]*>', "", head)
    body = html.split("<body>", 1)[1].rsplit("</body>", 1)[0]
    PUBLISH.write_text(head.strip() + "\n" + body.strip() + "\n", encoding="utf-8")
    print("Dashboard scritta in %s (%d KB)" % (OUT, len(html) // 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
