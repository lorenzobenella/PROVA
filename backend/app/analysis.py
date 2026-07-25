"""Cycling performance and health analytics.

All formulas here are simplified, widely-used approximations (Coggan's
Performance Management Chart, TrainingPeaks-style hrTSS) — good enough for
personal trend-spotting, not a substitute for a coach or a lab test.
"""

import json
import math
from datetime import date, timedelta

POWER_CURVE_DURATIONS = (5, 60, 300, 1200, 3600)  # seconds: sprint, 1min, 5min, 20min, 60min


def compute_power_curve(streams: dict) -> dict[str, float]:
    """Best average power for a set of standard durations, from a Strava
    activity streams payload ({"watts": {...}, "time": {...}})."""

    watts = (streams.get("watts") or {}).get("data")
    times = (streams.get("time") or {}).get("data")
    if not watts or not times or len(watts) != len(times):
        return {}

    curve: dict[str, float] = {}
    n = len(watts)
    prefix = [0.0] * (n + 1)
    for i, w in enumerate(watts):
        prefix[i + 1] = prefix[i] + (w or 0)

    for duration in POWER_CURVE_DURATIONS:
        best = 0.0
        left = 0
        for right in range(n):
            while times[right] - times[left] > duration:
                left += 1
            span = times[right] - times[left]
            if span <= 0:
                continue
            avg = (prefix[right + 1] - prefix[left]) / (right - left + 1)
            best = max(best, avg)
        if best > 0:
            curve[str(duration)] = round(best, 1)

    return curve


def estimate_ftp(power_curves: list[dict], default: float | None = None) -> float | None:
    """FTP ~= 95% of best recent 20-minute power."""

    best_20min = 0.0
    for curve in power_curves:
        val = curve.get("1200")
        if val and val > best_20min:
            best_20min = val
    if best_20min > 0:
        return round(best_20min * 0.95, 1)
    return default


def compute_training_load(
    moving_time_s: int,
    avg_power_w: float | None,
    weighted_avg_power_w: float | None,
    ftp: float | None,
    avg_hr: float | None,
    lthr: float | None = None,
) -> float | None:
    """TSS if power+FTP are available, otherwise an hrTSS-style estimate."""

    hours = moving_time_s / 3600
    if hours <= 0:
        return None

    np_watts = weighted_avg_power_w or avg_power_w
    if np_watts and ftp:
        intensity_factor = np_watts / ftp
        return round(hours * intensity_factor * intensity_factor * 100, 1)

    if avg_hr:
        threshold_hr = lthr or 160  # reasonable default LTHR for a recreational cyclist
        ratio = avg_hr / threshold_hr
        return round(hours * ratio * ratio * 100, 1)

    return None


def compute_pmc(daily_loads: list[tuple[date, float]]) -> list[dict]:
    """Performance Management Chart: CTL (fitness, 42d EWMA), ATL (fatigue,
    7d EWMA), TSB (form) = yesterday's CTL - yesterday's ATL."""

    if not daily_loads:
        return []

    by_date = {d: load for d, load in daily_loads}
    start, end = min(by_date), max(by_date)

    ctl = atl = 0.0
    ctl_decay = 1 - math.exp(-1 / 42)
    atl_decay = 1 - math.exp(-1 / 7)

    out = []
    d = start
    while d <= end:
        load = by_date.get(d, 0.0)
        tsb = ctl - atl
        ctl = ctl + (load - ctl) * ctl_decay
        atl = atl + (load - atl) * atl_decay
        out.append({
            "date": d.isoformat(),
            "load": round(load, 1),
            "ctl": round(ctl, 1),
            "atl": round(atl, 1),
            "tsb": round(tsb, 1),
        })
        d += timedelta(days=1)

    return out


_HRV_STATUS_SCORE = {
    "BALANCED": 100,
    "UNBALANCED": 55,
    "LOW": 35,
    "POOR": 20,
}


def compute_readiness(wellness: dict) -> dict:
    """Composite 0-100 readiness score blending whatever wellness signals are
    available that day, plus a plain-language label."""

    components: dict[str, float] = {}

    if wellness.get("training_readiness_score") is not None:
        components["training_readiness"] = wellness["training_readiness_score"]
    if wellness.get("sleep_score") is not None:
        components["sleep"] = wellness["sleep_score"]
    if wellness.get("body_battery_max") is not None:
        components["body_battery"] = wellness["body_battery_max"]
    if wellness.get("stress_avg") is not None:
        components["stress"] = max(0, 100 - wellness["stress_avg"])
    status = wellness.get("hrv_status")
    if status and status.upper() in _HRV_STATUS_SCORE:
        components["hrv"] = _HRV_STATUS_SCORE[status.upper()]

    if not components:
        return {"score": None, "label": "insufficient data", "components": {}}

    weights = {
        "training_readiness": 0.4,
        "sleep": 0.2,
        "hrv": 0.2,
        "body_battery": 0.1,
        "stress": 0.1,
    }
    total_weight = sum(weights[k] for k in components)
    score = sum(components[k] * weights[k] for k in components) / total_weight

    if score >= 75:
        label = "well recovered — good day to train hard"
    elif score >= 55:
        label = "moderate recovery — normal training is fine"
    elif score >= 35:
        label = "fatigued — prioritize easy/recovery riding"
    else:
        label = "very fatigued — consider a rest day"

    return {"score": round(score, 1), "label": label, "components": components}


def parse_power_curve(power_curve_json: str | None) -> dict:
    if not power_curve_json:
        return {}
    try:
        return json.loads(power_curve_json)
    except ValueError:
        return {}
