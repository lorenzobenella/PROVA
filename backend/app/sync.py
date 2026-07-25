"""Pulls fresh data from Strava (rides) and Garmin Connect (health metrics)
into the local SQLite database, then derives training load / power curves."""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import analysis, garmin_client, strava_client
from .models import Activity, DailyWellness

CYCLING_TYPES = {"Ride", "VirtualRide", "GravelRide", "MountainBikeRide", "EBikeRide", "Handcycle"}


def sync_strava_activities(db: Session, lookback_days: int = 180) -> int:
    latest = db.query(Activity).filter(Activity.source == "strava").order_by(Activity.start_date.desc()).first()
    if latest:
        after = int(latest.start_date.replace(tzinfo=timezone.utc).timestamp()) - 3600
    else:
        after = int((datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp())

    raw_activities = strava_client.list_activities(db, after_epoch=after)
    count = 0

    for raw in raw_activities:
        sport_type = raw.get("sport_type") or raw.get("type") or "Ride"
        if sport_type not in CYCLING_TYPES:
            continue

        external_id = str(raw["id"])
        activity = (
            db.query(Activity)
            .filter(Activity.source == "strava", Activity.external_id == external_id)
            .first()
        )
        if activity is None:
            activity = Activity(source="strava", external_id=external_id)
            db.add(activity)

        activity.name = raw.get("name", "")
        activity.sport_type = sport_type
        activity.start_date = datetime.fromisoformat(raw["start_date"].replace("Z", "+00:00")).replace(tzinfo=None)
        activity.distance_m = raw.get("distance") or 0.0
        activity.moving_time_s = raw.get("moving_time") or 0
        activity.elapsed_time_s = raw.get("elapsed_time") or 0
        activity.elevation_gain_m = raw.get("total_elevation_gain") or 0.0
        activity.avg_speed_ms = raw.get("average_speed") or 0.0
        activity.max_speed_ms = raw.get("max_speed") or 0.0
        activity.avg_hr = raw.get("average_heartrate")
        activity.max_hr = raw.get("max_heartrate")
        activity.avg_power_w = raw.get("average_watts")
        activity.weighted_avg_power_w = raw.get("weighted_average_watts")
        activity.max_power_w = raw.get("max_watts")
        activity.avg_cadence = raw.get("average_cadence")
        activity.calories = raw.get("calories")

        if raw.get("average_watts"):
            try:
                streams = strava_client.get_activity_streams(db, external_id)
                curve = analysis.compute_power_curve(streams)
                if curve:
                    activity.power_curve_json = json.dumps(curve)
            except Exception:
                pass

        count += 1

    db.commit()
    _recompute_training_loads(db)
    return count


def _recompute_training_loads(db: Session) -> None:
    cutoff = datetime.utcnow() - timedelta(days=90)
    recent = db.query(Activity).filter(Activity.start_date >= cutoff).all()
    curves = [analysis.parse_power_curve(a.power_curve_json) for a in recent if a.power_curve_json]
    ftp = analysis.estimate_ftp(curves)

    for activity in db.query(Activity).all():
        activity.training_load = analysis.compute_training_load(
            moving_time_s=activity.moving_time_s,
            avg_power_w=activity.avg_power_w,
            weighted_avg_power_w=activity.weighted_avg_power_w,
            ftp=ftp,
            avg_hr=activity.avg_hr,
        )
    db.commit()


def sync_garmin_wellness(db: Session, days: int = 30) -> int:
    client = garmin_client.get_client(db)
    count = 0
    today = datetime.utcnow().date()

    for offset in range(days):
        day = today - timedelta(days=offset)
        date_str = day.isoformat()

        row = db.query(DailyWellness).filter(DailyWellness.date == day).first()
        if row is not None and offset > 2:
            # Keep the last 3 days fresh (Garmin backfills), skip older cached days.
            continue

        data = garmin_client.fetch_daily_wellness(client, date_str)
        if row is None:
            row = DailyWellness(date=day)
            db.add(row)

        row.sleep_score = data.get("sleep_score")
        row.sleep_duration_s = data.get("sleep_duration_s")
        row.deep_sleep_s = data.get("deep_sleep_s")
        row.rem_sleep_s = data.get("rem_sleep_s")
        row.hrv_status = data.get("hrv_status")
        row.hrv_last_night_avg = data.get("hrv_last_night_avg")
        row.body_battery_max = data.get("body_battery_max")
        row.body_battery_min = data.get("body_battery_min")
        row.stress_avg = data.get("stress_avg")
        row.spo2_avg = data.get("spo2_avg")
        row.resting_hr = data.get("resting_hr")
        row.training_readiness_score = data.get("training_readiness_score")
        count += 1

    db.commit()
    return count


def sync_all(db: Session) -> dict:
    result = {"strava_activities": 0, "garmin_days": 0, "errors": []}

    try:
        result["strava_activities"] = sync_strava_activities(db)
    except Exception as e:
        result["errors"].append(f"strava: {e}")

    try:
        result["garmin_days"] = sync_garmin_wellness(db)
    except Exception as e:
        result["errors"].append(f"garmin: {e}")

    return result
