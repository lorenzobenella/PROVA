"""Test del coach: python -m unittest discover -s coach/tests -t ."""

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path

from coach import config, export, load, notify, readiness as rd, sources, workouts as wo
from coach.models import Activity, Data, Wellness
from coach.planner import DELOAD, TEMPLATE, Planner

START = date(2026, 11, 9)  # lunedì, settimana 1
FTP = 300.0


def cfg(**kw):
    c = config.Config(start=START, ftp_w=FTP, weight_kg=70)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def good_night(day, **kw):
    w = Wellness(day=day, rhr=50, hrv=48, hrv_low=40, hrv_high=52, hrv_status="BALANCED",
                 sleep_h=7.8, sleep_score=82, body_battery=85)
    for k, v in kw.items():
        setattr(w, k, v)
    return w


def ride(day, minutes=60, np_=210, tss=None, p60=None, hour=10):
    return Activity(day=day, start=datetime.combine(day, datetime.min.time()).replace(hour=hour), sport="bike",
                    duration_s=minutes * 60, np=np_, tss=tss, p60=p60, name="Ciclismo indoor")


def data_with(activities=(), nights=None, today=None, has_activities=True):
    nights = nights or {}
    well = {}
    if today:
        for k in range(20):  # 20 notti buone di base
            d = today - timedelta(days=k)
            well[d] = good_night(d)
    well.update(nights)
    return Data(activities=list(activities), wellness=well, has_activities=has_activities)


class TestWorkouts(unittest.TestCase):
    def all_keys(self):
        keys = {k for t in TEMPLATE.values() for k in t} | set(DELOAD) | {"recovery", "mobility"}
        return sorted(keys)

    def test_every_session_fits_the_time(self):
        for key in self.all_keys():
            for block in config.BLOCKS:
                for w in range(4):
                    for minutes in (30, 45, 60, 90, 120):
                        for reduced in (False, True):
                            wk = wo.build(key, block, w, minutes, FTP, reduced)
                            if key == "test" and not reduced:
                                continue  # il ramp test dura quello che dura (~42')
                            self.assertLessEqual(wk.minutes, minutes + 1e-6, (key, block, w, minutes, reduced))

    def test_full_sessions_use_the_time(self):
        for key in ("vo2_long", "threshold", "vo2_short", "sweetspot", "endurance"):
            wk = wo.build(key, "vo2", 0, 120, FTP)
            self.assertAlmostEqual(wk.minutes, 120, delta=0.01, msg=key)

    def test_reduced_has_less_work(self):
        full = wo.build("vo2_long", "vo2", 2, 60, FTP)
        less = wo.build("vo2_long", "vo2", 2, 60, FTP, reduced=True)
        self.assertLess(less.segments[3].times, full.segments[3].times)
        self.assertLess(less.tss, full.tss)
        self.assertIn("ridotta", less.title)

    def test_short_day_trims_reps_not_quality(self):
        wk = wo.build("vo2_long", "vo2", 2, 40, FTP)
        rep = wk.segments[3]
        self.assertEqual(rep.steps[0].pct, 1.12)
        self.assertLessEqual(wk.minutes, 40)

    def test_zwo_is_valid_and_has_the_right_length(self):
        for key in ("vo2_long", "vo2_short", "over_under", "sweetspot", "endurance", "test"):
            wk = wo.build(key, "soglia", 1, 120, FTP)
            root = ET.fromstring(export.zwo(wk, "prova"))
            secs = 0
            for el in root.find("workout"):
                if el.tag == "IntervalsT":
                    secs += int(el.get("Repeat")) * (int(el.get("OnDuration")) + int(el.get("OffDuration")))
                else:
                    secs += int(el.get("Duration"))
            self.assertAlmostEqual(secs, wk.minutes * 60, delta=len(root.find("workout")), msg=key)

    def test_erg_ends_at_workout_length(self):
        wk = wo.build("threshold", "vo2", 0, 60, FTP)
        lines = export.erg(wk, FTP, "x").split("[COURSE DATA]\n")[1].split("\n[END")[0].splitlines()
        self.assertEqual(lines[0], "0.00\t150")
        self.assertAlmostEqual(float(lines[-1].split("\t")[0]), wk.minutes, delta=0.01)

    def test_ramp_test_starts_at_100w(self):
        wk = wo.build("test", "vo2", 3, 120, FTP)
        steps = [s for s in wk.segments if s.label.endswith(" W")]
        self.assertEqual(export.watts(steps[0].pct, FTP), 100)
        self.assertEqual(export.watts(steps[1].pct, FTP) - export.watts(steps[0].pct, FTP), 20)

    def test_describe_uses_watts(self):
        lines = export.describe(wo.build("vo2_long", "vo2", 1, 60, FTP), FTP)
        self.assertIn("5 × (4' a 335 W (112%) + 3' a 150 W)", lines)


class TestReadiness(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 11, 20)
        self.well = {self.day - timedelta(days=k): good_night(self.day - timedelta(days=k)) for k in range(15)}

    def test_good_night_is_green(self):
        r = rd.assess(self.day, self.well, tsb=0)
        self.assertEqual((r.level, r.score, r.provisional), (rd.GREEN, 100, False))

    def test_short_sleep_is_yellow(self):
        self.well[self.day] = good_night(self.day, sleep_h=4.7, sleep_score=46)
        self.assertEqual(rd.assess(self.day, self.well, 0).level, rd.YELLOW)

    def test_several_bad_signals_are_red(self):
        for k in range(3):
            d = self.day - timedelta(days=k)
            self.well[d] = good_night(d, sleep_h=4.5, sleep_score=45)
        self.well[self.day] = good_night(self.day, sleep_h=3.8, sleep_score=44, hrv=36, rhr=57)
        r = rd.assess(self.day, self.well, 0)
        self.assertEqual(r.level, rd.RED)
        self.assertTrue(any("Debito di sonno" in x for x in r.reasons))

    def test_deep_fatigue_alone_is_yellow(self):
        self.assertEqual(rd.assess(self.day, self.well, tsb=-35).level, rd.YELLOW)

    def test_missing_night_is_provisional(self):
        del self.well[self.day]
        r = rd.assess(self.day, self.well, 0)
        self.assertTrue(r.provisional)
        self.assertEqual(r.level, rd.GREEN)


class TestLoad(unittest.TestCase):
    def test_tss_sources(self):
        d = date(2026, 11, 1)
        self.assertEqual(load.activity_tss(ride(d, tss=63.4), FTP), 63.4)
        self.assertAlmostEqual(load.activity_tss(ride(d, 60, np_=240), FTP), 64.0, places=1)
        hr_only = Activity(d, datetime(2026, 11, 1, 9), "bike", 3600, avg_hr=150)
        self.assertAlmostEqual(load.activity_tss(hr_only, FTP, lthr=169), 78.8, places=1)
        self.assertEqual(load.activity_tss(Activity(d, datetime(2026, 11, 1), "light", 3600), FTP), 0)
        self.assertAlmostEqual(load.activity_tss(Activity(d, datetime(2026, 11, 1), "strength", 1800), FTP), 18)

    def test_pmc_converges_to_daily_load(self):
        start = date(2026, 1, 1)
        tss = {start + timedelta(days=k): 60.0 for k in range(400)}
        ctl, atl = load.pmc(tss, start, start + timedelta(days=399))[start + timedelta(days=399)]
        self.assertAlmostEqual(ctl, 60, delta=0.1)
        self.assertAlmostEqual(atl, 60, delta=0.01)


class TestPlanner(unittest.TestCase):
    def planner(self, today, activities=(), nights=None, tests=(), has_activities=True):
        return Planner(cfg(), data_with(activities, nights, today, has_activities), tests, today)

    def test_weekly_template_and_blocks(self):
        p = self.planner(START)
        self.assertEqual(p.base(START).key, "strength_a")
        self.assertEqual(p.base(START + timedelta(days=1)).key, "vo2_long")
        self.assertEqual(p.base(START + timedelta(days=26)).key, "test")          # sabato settimana 4
        self.assertEqual(p.base(START + timedelta(days=29)).key, "over_under")    # martedì settimana 5
        self.assertEqual(p.base(START + timedelta(days=12 * 7 + 1)).key, "vo2_long")  # il ciclo riparte
        self.assertEqual(p.base(START - timedelta(days=2)).key, "vo2_short")      # avvicinamento = settimana 1

    def test_missed_saturday_is_recovered_on_sunday(self):
        sunday = START + timedelta(days=6)
        p = self.planner(sunday).plan(sunday)
        self.assertEqual(p.key, "vo2_short")
        self.assertTrue(p.changes and "Recuperi" in p.changes[0])

    def test_done_saturday_is_not_recovered(self):
        sunday = START + timedelta(days=6)
        p = self.planner(sunday, [ride(sunday - timedelta(days=1), 120, tss=110)]).plan(sunday)
        self.assertEqual(p.key, "endurance")

    def test_no_recovery_before_another_hard_day(self):
        wednesday = START + timedelta(days=2)  # martedì saltato, ma giovedì è soglia
        self.assertEqual(self.planner(wednesday).plan(wednesday).key, "endurance")

    def test_no_recovery_without_activity_data(self):
        sunday = START + timedelta(days=6)
        self.assertEqual(self.planner(sunday, has_activities=False).plan(sunday).key, "endurance")

    def test_yellow_reduces_and_red_recovers(self):
        tue = START + timedelta(days=1)
        yellow = self.planner(tue, nights={tue: good_night(tue, sleep_h=4.6, sleep_score=45)}).plan(tue)
        self.assertTrue(yellow.workout.reduced)
        bad = {tue - timedelta(days=k): good_night(tue - timedelta(days=k), sleep_h=4.0, sleep_score=40,
                                                   hrv=30, hrv_status="LOW") for k in range(3)}
        red = self.planner(tue, nights=bad).plan(tue)
        self.assertEqual(red.workout.kind, "recovery")
        red_monday = self.planner(START, nights={START - timedelta(days=k): bad[tue - timedelta(days=k)]
                                                 for k in range(3)}).plan(START)
        self.assertEqual(red_monday.workout.kind, "mobility")

    def test_status_done(self):
        tue = START + timedelta(days=1)
        p = self.planner(tue, [ride(tue, 60, tss=70)]).plan(tue)
        self.assertEqual(p.status, "fatto")
        p = self.planner(tue, [ride(tue, 15, tss=10)]).plan(tue)
        self.assertEqual(p.status, "fatto in parte")

    def test_ramp_test_is_read_from_garmin(self):
        sat = START + timedelta(days=26)
        acts = [ride(sat, 35, tss=45, p60=420)]
        found = self.planner(sat, acts, tests=[(date(2026, 10, 31), 299.0, "ramp test")]).detect_tests()
        self.assertEqual(found, [(sat, 315, True)])

    def test_implausible_test_is_not_applied(self):
        sat = START + timedelta(days=26)
        found = self.planner(sat, [ride(sat, 35, tss=45, p60=520)],
                             tests=[(date(2026, 10, 31), 299.0, "ramp test")]).detect_tests()
        self.assertEqual(found, [(sat, 390, False)])

    def test_ftp_comes_from_latest_test(self):
        p = self.planner(START, tests=[(date(2026, 10, 31), 299.0, "ramp test")])
        self.assertEqual(p.ftp_at(START), 299.0)
        self.assertEqual(p.ftp_at(date(2026, 10, 1)), FTP)

    def test_availability_exception(self):
        sat = START + timedelta(days=5)
        p = Planner(cfg(exceptions={sat: 60}), data_with(today=sat), [], sat)
        self.assertLessEqual(p.plan(sat).workout.minutes, 60)
        p = Planner(cfg(exceptions={sat: 0}), data_with(today=sat), [], sat)
        self.assertEqual(p.plan(sat).workout.kind, "rest")


class TestNotify(unittest.TestCase):
    def setUp(self):
        self.today = START + timedelta(days=1)
        self.pl = Planner(cfg(), data_with(today=self.today), [], self.today)

    def run_at(self, state, hour, plan=None):
        now = datetime.combine(self.today, datetime.min.time()).replace(hour=hour)
        plan = plan or self.pl.plan(self.today)
        msgs = notify.decide(state, now, plan, self.pl.base(self.today + timedelta(days=1)), FTP, {})
        for m in msgs:
            notify.apply(m, state)
        return [m.text for m in msgs if m.text]

    def test_evening_preview_then_silent_morning(self):
        state = {"days": {}}
        yesterday = Planner(cfg(), data_with(today=START), [], START)
        now = datetime.combine(START, datetime.min.time()).replace(hour=21)
        for m in notify.decide(state, now, yesterday.plan(START), yesterday.base(self.today), FTP, {}):
            notify.apply(m, state)
        self.assertIn(self.today.isoformat(), state["days"])
        self.assertEqual(self.run_at(state, 9), [])  # sonno buono: la seduta resta quella annunciata

    def test_morning_change_is_announced(self):
        state = {"days": {self.today.isoformat(): {"preview": "diverso"}}}
        texts = self.run_at(state, 9)
        self.assertEqual(len(texts), 1)
        self.assertIn("Cambio di programma", texts[0])
        self.assertEqual(self.run_at(state, 13), [])

    def test_done_is_announced_once(self):
        pl = Planner(cfg(), data_with([ride(self.today, 60, tss=70)], today=self.today), [], self.today)
        state = {"days": {}}
        texts = self.run_at(state, 13, pl.plan(self.today))
        self.assertTrue(texts and texts[0].startswith("✅"))
        self.assertEqual(self.run_at(state, 17, pl.plan(self.today)), [])

    def test_provisional_morning_waits_for_data(self):
        pl = Planner(cfg(), Data(wellness={}, has_activities=True), [], self.today)
        state = {"days": {}}
        self.assertEqual(len(self.run_at(state, 6, pl.plan(self.today))), 1)  # piano provvisorio
        self.assertEqual(self.run_at(state, 9, pl.plan(self.today)), [])      # stessa seduta: silenzio


class TestSources(unittest.TestCase):
    def test_garmin_activity(self):
        rec = {"startTimeLocal": "2026-11-06 10:58:02", "activityType": {"typeKey": "indoor_cycling"},
               "duration": 4500.3, "normPower": 214.0, "trainingStressScore": 63.4, "maxAvgPower_60": 300,
               "maxAvgPower_300": 255, "max20MinPower": 225.7, "averageHR": 138.0, "activityName": "Indoor"}
        a = sources.activity_from_garmin(rec)
        self.assertEqual((a.day, a.sport, a.np, a.tss, a.p1200), (date(2026, 11, 6), "bike", 214.0, 63.4, 225.7))
        walk = sources.activity_from_garmin({"startTimeLocal": "2026-11-06 12:48:00",
                                             "activityType": {"typeKey": "walking"}, "duration": 3000})
        self.assertEqual(walk.sport, "light")

    def test_garmin_wellness(self):
        w = sources.wellness_from_garmin(
            "2026-11-07", {"heartRate": {"restingValue": 50}, "bodyBattery": {"maxValue": 78}},
            {"sleepTimeSeconds": 16980, "sleepScores": {"overall": {"value": 46}}},
            {"lastNightAvg": 47, "status": "BALANCED", "baseline": {"balancedLow": 40, "balancedUpper": 51}})
        self.assertEqual((w.rhr, w.sleep_h, w.sleep_score, w.hrv, w.hrv_low, w.body_battery),
                         (50, 4.72, 46, 47, 40, 78))

    def test_strava_duplicates_are_dropped(self):
        d = date(2026, 11, 6)
        garmin = [ride(d, hour=10)]
        strava = [sources.activity_from_strava({"start_date_local": "2026-11-06T10:04:00Z", "sport_type": "VirtualRide",
                                                "moving_time": 3600, "weighted_average_watts": 215,
                                                "device_watts": True}),
                  sources.activity_from_strava({"start_date_local": "2026-11-06T18:00:00Z", "sport_type": "Ride",
                                                "moving_time": 3600, "weighted_average_watts": 200,
                                                "device_watts": False})]
        merged = sources.merge(garmin, strava)
        self.assertEqual(len(merged), 2)
        self.assertIsNone(merged[1].np)  # potenza stimata da Strava: ignorata


class TestConfig(unittest.TestCase):
    def test_repo_config_loads(self):
        c = config.load()
        self.assertEqual(c.start.weekday(), 0)
        self.assertEqual(c.minutes_for(date(2026, 11, 14)), c.availability[5])

    def test_append_test_keeps_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ftp_tests.json"
            path.write_text('[\n  {"date": "2026-10-31", "watts": 299, "note": "ramp test"}\n]\n')
            config.append_test(date(2026, 12, 5), 306.4, "ramp test (letto dal coach)", path)
            rows = json.loads(path.read_text())
            self.assertEqual(rows[-1], {"date": "2026-12-05", "watts": 306, "note": "ramp test (letto dal coach)"})
            self.assertEqual(config.load_tests(path)[-1][1], 306.0)


if __name__ == "__main__":
    unittest.main()
