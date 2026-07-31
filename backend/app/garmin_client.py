"""Garmin Connect client, using the unofficial garminconnect/garth library
(Garmin has no public personal-use API), including 2FA/MFA support.

The logged-in session is serialized (client.garth.dumps()) and stored in the
local database so the user doesn't have to re-authenticate on every sync.
"""

import numbers
import secrets
import uuid
from collections.abc import Callable

from garminconnect import Garmin
from sqlalchemy.orm import Session

from .models import GarminSession

# In-memory holding area for logins that are paused waiting on an MFA code.
# Keyed by a one-time session id handed to the frontend.
_pending_mfa: dict[str, dict] = {}


def _save_session(db: Session, email: str, client: Garmin) -> None:
    row = db.get(GarminSession, 1)
    if row is None:
        row = GarminSession(id=1, email=email, token_store=client.client.dumps())
        db.add(row)
    else:
        row.email = email
        row.token_store = client.client.dumps()
    db.commit()


def start_login(email: str, password: str) -> dict:
    """Begin a Garmin login. Returns either {"connected": True} or
    {"mfa_required": True, "session_id": ...} if a 2FA code is needed."""

    client = Garmin(email, password, return_on_mfa=True)
    mfa_status, client_state = client.login()

    if mfa_status == "needs_mfa":
        session_id = uuid.uuid4().hex + secrets.token_hex(4)
        _pending_mfa[session_id] = {"client": client, "state": client_state, "email": email}
        return {"mfa_required": True, "session_id": session_id}

    return {"mfa_required": False, "client": client, "email": email}


def finish_mfa(db: Session, session_id: str, mfa_code: str) -> None:
    pending = _pending_mfa.pop(session_id, None)
    if pending is None:
        raise RuntimeError("Unknown or expired Garmin login session; please retry the login.")

    client: Garmin = pending["client"]
    client.resume_login(pending["state"], mfa_code)
    _save_session(db, pending["email"], client)


def get_client(db: Session) -> Garmin:
    row = db.get(GarminSession, 1)
    if row is None:
        raise RuntimeError("Garmin not connected. Log in via /api/auth/garmin/login first.")
    client = Garmin()
    client.client.loads(row.token_store)
    return client


def is_connected(db: Session) -> bool:
    return db.get(GarminSession, 1) is not None


def _g(d, *path, default=None):
    """Tolerant nested-dict getter for Garmin's loosely-typed JSON payloads."""
    cur = d
    for key in path:
        if cur is None:
            return default
        if isinstance(key, int):
            if not isinstance(cur, list) or len(cur) <= key:
                return default
            cur = cur[key]
        else:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(key)
    return default if cur is None else cur


def client_from_token_store(path: str) -> Garmin:
    """Build a client from a garth token directory (or garmin_tokens.json file).

    Lets the MCP server run without the web app's SQLite database.
    """
    client = Garmin()
    client.client.load(path)
    return client


def client_from_credentials(email: str, password: str) -> Garmin:
    """Non-interactive login. Raises if Garmin demands an MFA code, since there
    is no channel to prompt for one here."""

    client = Garmin(email, password, return_on_mfa=True)
    mfa_status, _ = client.login()
    if mfa_status == "needs_mfa":
        raise RuntimeError(
            "Garmin asked for an MFA code, which cannot be entered non-interactively. "
            "Run 'python -m app.garmin_login' once to create a token store."
        )
    return client


def fetch_sleep(client: Garmin, date_str: str) -> dict:
    """Sleep duration, stage breakdown and sleep score for one night."""

    sleep = client.get_sleep_data(date_str)
    dto = _g(sleep, "dailySleepDTO", default={})
    return {
        "sleep_duration_s": _g(dto, "sleepTimeSeconds"),
        "deep_sleep_s": _g(dto, "deepSleepSeconds"),
        "rem_sleep_s": _g(dto, "remSleepSeconds"),
        "light_sleep_s": _g(dto, "lightSleepSeconds"),
        "awake_s": _g(dto, "awakeSleepSeconds"),
        "sleep_score": _g(dto, "sleepScores", "overall", "value"),
        "sleep_quality": _g(dto, "sleepScores", "overall", "qualifierKey"),
    }


def fetch_hrv(client: Garmin, date_str: str) -> dict:
    """Overnight heart rate variability status."""

    hrv = client.get_hrv_data(date_str)
    summary = _g(hrv, "hrvSummary", default={})
    return {
        "hrv_status": _g(summary, "status"),
        "hrv_last_night_avg": _g(summary, "lastNightAvg"),
        "hrv_last_night_5min_high": _g(summary, "lastNight5MinHigh"),
        "hrv_weekly_avg": _g(summary, "weeklyAvg"),
        "hrv_baseline_low": _g(summary, "baseline", "lowUpper"),
        "hrv_baseline_high": _g(summary, "baseline", "balancedUpper"),
    }


def fetch_body_battery(client: Garmin, date_str: str) -> dict:
    """Body Battery highs, lows and how much was charged/drained."""

    bb = client.get_body_battery(date_str)
    if not bb:
        return {}

    day = bb[0]
    result = {
        "body_battery_charged": _g(day, "charged"),
        "body_battery_drained": _g(day, "drained"),
    }
    values = [
        v[1]
        for v in _g(day, "bodyBatteryValuesArray", default=[])
        if isinstance(v, list) and len(v) > 1 and isinstance(v[1], numbers.Number)
    ]
    if values:
        result["body_battery_max"] = max(values)
        result["body_battery_min"] = min(values)
    return result


def fetch_stress(client: Garmin, date_str: str) -> dict:
    """All-day stress average, max and time spent at each stress level."""

    stress = client.get_stress_data(date_str)
    return {
        "stress_avg": _g(stress, "avgStressLevel"),
        "stress_max": _g(stress, "maxStressLevel"),
        "stress_rest_s": _g(stress, "restStressDuration"),
        "stress_low_s": _g(stress, "lowStressDuration"),
        "stress_medium_s": _g(stress, "mediumStressDuration"),
        "stress_high_s": _g(stress, "highStressDuration"),
    }


def fetch_spo2(client: Garmin, date_str: str) -> dict:
    """Overnight pulse oximetry."""

    spo2 = client.get_spo2_data(date_str)
    return {
        "spo2_avg": _g(spo2, "averageSpO2") or _g(spo2, "avgSpO2"),
        "spo2_lowest": _g(spo2, "lowestSpO2"),
    }


def fetch_training_readiness(client: Garmin, date_str: str) -> dict:
    """Garmin's 0-100 readiness score plus the factors feeding it."""

    readiness = client.get_training_readiness(date_str)
    if isinstance(readiness, list):
        readiness = readiness[0] if readiness else None
    if not isinstance(readiness, dict):
        return {}

    return {
        "training_readiness_score": _g(readiness, "score"),
        "training_readiness_level": _g(readiness, "level"),
        "training_readiness_feedback": _g(readiness, "feedbackShort"),
        "readiness_sleep_score": _g(readiness, "sleepScore"),
        "readiness_hrv_factor": _g(readiness, "hrvFactorPercent"),
        "readiness_recovery_time_h": _g(readiness, "recoveryTime"),
        "readiness_acwr_factor": _g(readiness, "acuteLoadFactorPercent"),
        "readiness_stress_factor": _g(readiness, "stressHistoryFactorPercent"),
    }


def fetch_daily_stats(client: Garmin, date_str: str) -> dict:
    """Daily rollup: resting heart rate, steps and calories."""

    stats = client.get_stats(date_str)
    return {
        "resting_hr": _g(stats, "restingHeartRate"),
        "steps": _g(stats, "totalSteps"),
        "calories_total": _g(stats, "totalKilocalories"),
    }


# Registry used both by fetch_daily_wellness and by the MCP server, so the two
# always cover exactly the same set of metrics.
WELLNESS_FETCHERS: dict[str, Callable[[Garmin, str], dict]] = {
    "sleep": fetch_sleep,
    "hrv": fetch_hrv,
    "body_battery": fetch_body_battery,
    "stress": fetch_stress,
    "spo2": fetch_spo2,
    "training_readiness": fetch_training_readiness,
    "daily_stats": fetch_daily_stats,
}


def fetch_metric(client: Garmin, metric: str, date_str: str) -> dict:
    """Fetch one metric for one day, tolerating Garmin's frequent partial data.

    Returns {} when Garmin has nothing for that day, which is normal: the watch
    may not have been worn, or the day may not have synced yet.
    """
    fetcher = WELLNESS_FETCHERS.get(metric)
    if fetcher is None:
        raise ValueError(f"Unknown metric '{metric}'. Known: {', '.join(WELLNESS_FETCHERS)}")
    try:
        return {k: v for k, v in fetcher(client, date_str).items() if v is not None}
    except Exception:
        return {}


def fetch_daily_wellness(client: Garmin, date_str: str) -> dict:
    """Pull and normalize one day's worth of Garmin health metrics."""

    result: dict = {"date": date_str}
    for metric in WELLNESS_FETCHERS:
        result.update(fetch_metric(client, metric, date_str))
    return result
