from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from . import analysis, sync
from .db import get_db
from .models import Activity, DailyWellness

router = APIRouter(prefix="/api", tags=["api"])


@router.post("/sync")
def run_sync(db: Session = Depends(get_db)):
    return sync.sync_all(db)


@router.get("/cycling/activities")
def cycling_activities(limit: int = 50, db: Session = Depends(get_db)):
    activities = (
        db.query(Activity).order_by(Activity.start_date.desc()).limit(limit).all()
    )
    return [
        {
            "id": a.id,
            "name": a.name,
            "sport_type": a.sport_type,
            "start_date": a.start_date.isoformat(),
            "distance_km": round(a.distance_m / 1000, 1),
            "moving_time_s": a.moving_time_s,
            "elevation_gain_m": a.elevation_gain_m,
            "avg_speed_kmh": round(a.avg_speed_ms * 3.6, 1),
            "avg_hr": a.avg_hr,
            "avg_power_w": a.avg_power_w,
            "weighted_avg_power_w": a.weighted_avg_power_w,
            "training_load": a.training_load,
            "power_curve": analysis.parse_power_curve(a.power_curve_json),
        }
        for a in activities
    ]


@router.get("/cycling/power-curve")
def cycling_power_curve(days: int = 90, db: Session = Depends(get_db)):
    cutoff = datetime.utcnow() - timedelta(days=days)
    activities = db.query(Activity).filter(Activity.start_date >= cutoff).all()
    curves = [analysis.parse_power_curve(a.power_curve_json) for a in activities if a.power_curve_json]

    best: dict[str, float] = {}
    for curve in curves:
        for duration, watts in curve.items():
            if watts > best.get(duration, 0):
                best[duration] = watts

    return {
        "best_power_curve": best,
        "estimated_ftp": analysis.estimate_ftp(curves),
    }


@router.get("/cycling/pmc")
def cycling_pmc(days: int = 180, db: Session = Depends(get_db)):
    cutoff = datetime.utcnow() - timedelta(days=days)
    activities = db.query(Activity).filter(Activity.start_date >= cutoff).all()

    loads: dict = {}
    for a in activities:
        if a.training_load:
            d = a.start_date.date()
            loads[d] = loads.get(d, 0.0) + a.training_load

    return analysis.compute_pmc(list(loads.items()))


@router.get("/health/wellness")
def health_wellness(days: int = 30, db: Session = Depends(get_db)):
    cutoff = datetime.utcnow().date() - timedelta(days=days)
    rows = (
        db.query(DailyWellness)
        .filter(DailyWellness.date >= cutoff)
        .order_by(DailyWellness.date)
        .all()
    )
    return [
        {
            "date": r.date.isoformat(),
            "sleep_score": r.sleep_score,
            "sleep_duration_h": round(r.sleep_duration_s / 3600, 1) if r.sleep_duration_s else None,
            "hrv_status": r.hrv_status,
            "hrv_last_night_avg": r.hrv_last_night_avg,
            "body_battery_max": r.body_battery_max,
            "body_battery_min": r.body_battery_min,
            "stress_avg": r.stress_avg,
            "spo2_avg": r.spo2_avg,
            "resting_hr": r.resting_hr,
            "training_readiness_score": r.training_readiness_score,
        }
        for r in rows
    ]


@router.get("/health/readiness")
def health_readiness(db: Session = Depends(get_db)):
    today = datetime.utcnow().date()
    row = db.query(DailyWellness).filter(DailyWellness.date == today).first()
    if row is None:
        row = (
            db.query(DailyWellness)
            .filter(DailyWellness.date < today)
            .order_by(DailyWellness.date.desc())
            .first()
        )
    if row is None:
        return {"score": None, "label": "no data yet — run a sync first", "components": {}}

    wellness = {
        "training_readiness_score": row.training_readiness_score,
        "sleep_score": row.sleep_score,
        "body_battery_max": row.body_battery_max,
        "stress_avg": row.stress_avg,
        "hrv_status": row.hrv_status,
    }
    readiness = analysis.compute_readiness(wellness)
    readiness["date"] = row.date.isoformat()
    return readiness


@router.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    return {
        "readiness": health_readiness(db),
        "pmc_tail": cycling_pmc(db=db)[-14:],
        "recent_activities": cycling_activities(limit=5, db=db),
        "power_curve": cycling_power_curve(db=db),
    }
