"""Il cervello del coach: periodizzazione, adattamento del giorno e controllo di cio' che hai fatto.

Ciclo di 12 settimane, ripetuto: tre blocchi da 4 settimane (3 di carico crescente + 1 di
scarico che finisce con il ramp test). Ogni giorno:
1. parte dalla seduta prevista dal modello settimanale del blocco;
2. se ieri è saltata una seduta chiave e oggi e domani sono giorni facili, la recupera;
3. la adatta alla prontezza del mattino (verde come prevista, giallo ridotta, rosso recupero);
4. guarda le attività Garmin/Strava del giorno per capire se l'hai già fatta.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from . import load, readiness as rd, workouts as wo

TEMPLATE = {
    # lun, mar, mer, gio, ven, sab, dom
    "vo2": ["strength_a", "vo2_long", "endurance", "threshold", "strength_b", "vo2_short", "endurance"],
    "soglia": ["strength_m", "over_under", "endurance", "vo2_maint", "rest", "sweetspot", "endurance"],
    "misto": ["strength_m", "vo2_long", "endurance", "threshold", "rest", "threshold_long", "endurance"],
}
DELOAD = ["strength_deload", "opener", "endurance_short", "endurance_short", "rest", "test", "endurance_deload"]
BLOCK_NAMES = {"vo2": "VO2max + forza", "soglia": "soglia (FTP)", "misto": "VO2max + soglia"}
DAY_NAMES = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
          "settembre", "ottobre", "novembre", "dicembre"]

RAMP_FACTOR = 0.75        # FTP = 75% del miglior minuto del ramp test
TEST_TOLERANCE = 0.12     # oltre +-12% dalla FTP attuale il test non viene applicato da solo


def day_label(d, with_weekday=True):
    text = "%d %s" % (d.day, MONTHS[d.month - 1])
    return "%s %s" % (DAY_NAMES[d.weekday()], text) if with_weekday else text


@dataclass
class DayPlan:
    day: object
    phase: str
    key: str
    block: str
    week: int
    workout: object
    minutes: int
    readiness: object = None
    changes: list = field(default_factory=list)
    activities: list = field(default_factory=list)
    done_tss: float = 0.0
    status: str = "da fare"


class Planner:
    def __init__(self, cfg, data, tests, today):
        self.cfg = cfg
        self.data = data
        self.tests = list(tests)
        self.today = today
        self.by_day = defaultdict(list)
        for act in data.activities:
            self.by_day[act.day].append(act)
        self.tss = load.daily_tss(data.activities, self.ftp_at, cfg.lthr)
        start = min([today - timedelta(days=120)] + list(self.tss))
        self.pmc = load.pmc(self.tss, start, today)

    # ------------------------------------------------------------ riferimenti

    def ftp_at(self, day):
        past = [t for t in self.tests if t[0] <= day]
        return past[-1][1] if past else self.cfg.ftp_w

    def ftp_source(self, day):
        past = [t for t in self.tests if t[0] <= day]
        if not past:
            return "da atleta.toml"
        d, _, note = past[-1]
        return "%s del %s" % (note, day_label(d, False))

    def weight_at(self, day):
        recent = [(d, kg) for d, kg in self.data.weights if day - timedelta(days=60) <= d <= day]
        return (recent[-1][1], "bilancia Garmin") if recent else (self.cfg.weight_kg, "atleta.toml")

    def tsb(self, day):
        """Forma del mattino: CTL - ATL a fine giornata di ieri."""
        v = self.pmc.get(day - timedelta(days=1))
        return v[0] - v[1] if v else None

    def phase(self, day):
        idx = max(0, (day - self.cfg.start).days // 7)
        block = self.cfg.blocks[(idx // 4) % len(self.cfg.blocks)]
        w = idx % 4
        if day < self.cfg.start:
            label = "Avvicinamento: il piano parte %s" % day_label(self.cfg.start)
        else:
            label = "Settimana %d · blocco %s · %s" % (
                idx + 1, BLOCK_NAMES[block], "scarico e test" if w == 3 else "carico %d di 3" % (w + 1))
        return block, w, label

    def base_key(self, day):
        block, w, _ = self.phase(day)
        return (DELOAD if w == 3 else TEMPLATE[block])[day.weekday()]

    def _build(self, key, block, w, day, minutes=None, reduced=False):
        minutes = self.cfg.minutes_for(day) if minutes is None else minutes
        return wo.build(key, block, w, minutes, self.ftp_at(day), reduced=reduced)

    # ------------------------------------------------------------ piano

    def base(self, day):
        block, w, label = self.phase(day)
        key = self.base_key(day)
        minutes = self.cfg.minutes_for(day)
        return DayPlan(day=day, phase=label, key=key, block=block, week=w,
                       workout=self._build(key, block, w, day), minutes=minutes)

    def bike_tss(self, day):
        return sum(load.activity_tss(a, self.ftp_at(day), self.cfg.lthr)
                   for a in self.by_day.get(day, []) if a.sport == "bike")

    def test_done(self, day):
        ftp = self.ftp_at(day - timedelta(days=1))
        return any(a.sport == "bike" and a.p60 and a.p60 >= 1.15 * ftp for a in self.by_day.get(day, []))

    def missed_key(self, day):
        """La seduta chiave di ieri, se non risulta fatta."""
        if not self.data.has_activities:
            return None  # senza attività non posso sapere se l'hai fatta
        y = day - timedelta(days=1)
        prev = self.base(y)
        if not prev.workout.is_key:
            return None
        if prev.key == "test":
            return None if self.test_done(y) else prev
        return None if self.bike_tss(y) >= 0.5 * prev.workout.tss else prev

    def plan(self, day):
        """Piano definitivo: per oggi e i giorni passati usa prontezza e attività; per il futuro è il piano base."""
        p = self.base(day)
        if day > self.today:
            return p
        r = rd.assess(day, self.data.wellness, self.tsb(day))
        p.readiness = r
        key, block, w = p.key, p.block, p.week

        missed = self.missed_key(day)
        if missed and not p.workout.is_key and p.minutes >= 45 and r.level == rd.GREEN:
            tomorrow_hard = self.base(day + timedelta(days=1)).workout.is_key
            if missed.key == "test" or not tomorrow_hard:
                key, block, w = missed.key, missed.block, missed.week
                p.workout = self._build(key, block, w, day)
                p.key = key
                p.changes.append("Recuperi la seduta di ieri (%s): non risulta fatta e oggi era un giorno facile."
                                 % missed.workout.title)

        if r.level == rd.YELLOW and p.workout.kind not in ("rest", "recovery", "mobility"):
            p.workout = self._build(key, block, w, day, reduced=True)
            p.changes.append("Prontezza gialla: stessa seduta con meno volume.")
        elif r.level == rd.RED and p.workout.kind not in ("rest", "recovery", "mobility"):
            was = p.workout.title
            if p.workout.kind == "strength":
                p.workout = wo.mobility()
            else:
                p.workout = wo.recovery(p.minutes)
            if key == "test":
                p.workout.notes.insert(0, "Test rimandato: serve freschezza.")
            p.changes.append("Prontezza rossa: al posto di \"%s\" solo recupero." % was)
        self._status(p)
        return p

    def _status(self, p):
        acts = self.by_day.get(p.day, [])
        p.activities = acts
        p.done_tss = sum(load.activity_tss(a, self.ftp_at(p.day), self.cfg.lthr) for a in acts)
        wk = p.workout
        past = p.day < self.today
        if wk.kind == "rest":
            hard = [a for a in acts if a.sport != "light"]
            p.status = "riposo" if not hard else "riposo previsto, ma ti sei allenato"
        elif wk.is_bike:
            bike = self.bike_tss(p.day)
            if wk.kind == "test":
                done = self.test_done(p.day)
            else:
                done = bike >= 0.6 * max(1.0, wk.tss)
            p.status = "fatto" if done else "fatto in parte" if bike > 0 else ("saltato" if past else "da fare")
        else:
            kinds = {"strength": ("strength",), "mobility": ("strength", "light", "other")}[wk.kind]
            done = any(a.sport in kinds for a in acts)
            p.status = "fatto" if done else ("saltato" if past else "da fare")

    def upcoming(self, days=6):
        return [self.base(self.today + timedelta(days=k)) for k in range(1, days + 1)]

    def week_so_far(self):
        monday = self.today - timedelta(days=self.today.weekday())
        first = max(monday, self.cfg.start)
        out, d = [], first
        while d < self.today:
            out.append(self.plan(d))
            d += timedelta(days=1)
        return out

    # ------------------------------------------------------------ test FTP

    def detect_tests(self, days_back=7):
        """Ramp test fatti nei giorni previsti e non ancora registrati: [(giorno, watt, applicabile)]."""
        known = {t[0] for t in self.tests}
        found = []
        for k in range(days_back + 1):
            d = self.today - timedelta(days=k)
            if d in known or self.plan(d).key != "test":
                continue
            best = max((a.p60 for a in self.by_day.get(d, []) if a.sport == "bike" and a.p60), default=None)
            if not best or not self.test_done(d):
                continue
            watts = round(RAMP_FACTOR * best)
            old = self.ftp_at(d - timedelta(days=1))
            found.append((d, watts, abs(watts - old) / old <= TEST_TOLERANCE))
        return found

    # ------------------------------------------------------------ indicatori

    def indicators(self):
        d = self.today
        ftp = self.ftp_at(d)
        kg, kg_src = self.weight_at(d)
        recent = [a for a in self.data.activities if a.sport == "bike" and d - timedelta(days=90) <= a.day <= d]
        b20 = max((a.p1200 for a in recent if a.p1200), default=None)
        b5 = max((a.p300 for a in recent if a.p300), default=None)
        ctl, atl = self.pmc.get(d, (0.0, 0.0))
        weeks = []
        for k in range(1, 5):
            end = d - timedelta(days=d.weekday() + 1 + 7 * (k - 1))   # domenica delle ultime 4 settimane
            start = end - timedelta(days=6)
            weeks.append(sum(a.duration_s for a in self.data.activities
                             if a.sport == "bike" and start <= a.day <= end) / 3600)
        planned_week = sum(self.base(d - timedelta(days=d.weekday()) + timedelta(days=i)).workout.minutes
                           for i in range(7)) / 60
        return {
            "ftp": ftp, "ftp_src": self.ftp_source(d), "kg": kg, "kg_src": kg_src, "wkg": ftp / kg,
            "ftp_est": 0.95 * b20 if b20 else None, "b20": b20, "b5": b5,
            "vo2_est": 16.6 + 8.87 * b5 / kg if b5 else None,  # Sitko et al. 2021
            "ctl": ctl, "atl": atl, "tsb": self.tsb(d),
            "hours_4w": statistics.mean(weeks) if self.data.has_activities else None,
            "hours_planned": planned_week,
        }
