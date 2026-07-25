"""Garmin Connect client, using the unofficial garminconnect/garth library
(Garmin has no public personal-use API), including 2FA/MFA support.

The logged-in session is serialized (client.garth.dumps()) and stored in the
local database so the user doesn't have to re-authenticate on every sync.
"""

import secrets
import uuid

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


def fetch_daily_wellness(client: Garmin, date_str: str) -> dict:
    """Pull and normalize one day's worth of Garmin health metrics."""

    result: dict = {"date": date_str}

    try:
        sleep = client.get_sleep_data(date_str)
        dto = _g(sleep, "dailySleepDTO", default={})
        result["sleep_duration_s"] = _g(dto, "sleepTimeSeconds")
        result["deep_sleep_s"] = _g(dto, "deepSleepSeconds")
        result["rem_sleep_s"] = _g(dto, "remSleepSeconds")
        result["sleep_score"] = _g(dto, "sleepScores", "overall", "value")
    except Exception:
        pass

    try:
        hrv = client.get_hrv_data(date_str)
        summary = _g(hrv, "hrvSummary", default={})
        result["hrv_status"] = _g(summary, "status")
        result["hrv_last_night_avg"] = _g(summary, "lastNightAvg")
    except Exception:
        pass

    try:
        bb = client.get_body_battery(date_str)
        if bb:
            values = [v[1] for v in _g(bb[0], "bodyBatteryValuesArray", default=[]) if isinstance(v, list) and len(v) > 1]
            if values:
                result["body_battery_max"] = max(values)
                result["body_battery_min"] = min(values)
    except Exception:
        pass

    try:
        stress = client.get_stress_data(date_str)
        result["stress_avg"] = _g(stress, "avgStressLevel")
    except Exception:
        pass

    try:
        spo2 = client.get_spo2_data(date_str)
        result["spo2_avg"] = _g(spo2, "averageSpO2") or _g(spo2, "avgSpO2")
    except Exception:
        pass

    try:
        readiness = client.get_training_readiness(date_str)
        if isinstance(readiness, list) and readiness:
            result["training_readiness_score"] = _g(readiness[0], "score")
        elif isinstance(readiness, dict):
            result["training_readiness_score"] = _g(readiness, "score")
    except Exception:
        pass

    try:
        stats = client.get_stats(date_str)
        result["resting_hr"] = _g(stats, "restingHeartRate")
    except Exception:
        pass

    return result
