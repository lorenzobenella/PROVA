from datetime import datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class StravaToken(Base):
    """Single-row table holding the current user's Strava OAuth tokens."""

    __tablename__ = "strava_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    athlete_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    access_token: Mapped[str] = mapped_column(String, nullable=False)
    refresh_token: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)


class GarminSession(Base):
    """Single-row table holding a serialized garth/garminconnect session."""

    __tablename__ = "garmin_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    email: Mapped[str] = mapped_column(String, nullable=False)
    token_store: Mapped[str] = mapped_column(Text, nullable=False)  # garth.dumps() blob


class Activity(Base):
    """A single cycling (or other) activity, from Strava or Garmin."""

    __tablename__ = "activity"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_activity_source_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, nullable=False)  # "strava" | "garmin"
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, default="")
    sport_type: Mapped[str] = mapped_column(String, default="Ride")
    start_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    distance_m: Mapped[float] = mapped_column(Float, default=0.0)
    moving_time_s: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_time_s: Mapped[int] = mapped_column(Integer, default=0)
    elevation_gain_m: Mapped[float] = mapped_column(Float, default=0.0)

    avg_speed_ms: Mapped[float] = mapped_column(Float, default=0.0)
    max_speed_ms: Mapped[float] = mapped_column(Float, default=0.0)

    avg_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_hr: Mapped[float | None] = mapped_column(Float, nullable=True)

    avg_power_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    weighted_avg_power_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_power_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_cadence: Mapped[float | None] = mapped_column(Float, nullable=True)
    calories: Mapped[float | None] = mapped_column(Float, nullable=True)

    power_curve_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # {"5":w,"60":w,"300":w,"1200":w,"3600":w}
    training_load: Mapped[float | None] = mapped_column(Float, nullable=True)  # computed TSS/TRIMP


class DailyWellness(Base):
    """Garmin daily health snapshot."""

    __tablename__ = "daily_wellness"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(Date, unique=True, nullable=False)

    sleep_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sleep_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    deep_sleep_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    rem_sleep_s: Mapped[float | None] = mapped_column(Float, nullable=True)

    hrv_status: Mapped[str | None] = mapped_column(String, nullable=True)
    hrv_last_night_avg: Mapped[float | None] = mapped_column(Float, nullable=True)

    body_battery_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_battery_min: Mapped[float | None] = mapped_column(Float, nullable=True)

    stress_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    spo2_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    resting_hr: Mapped[float | None] = mapped_column(Float, nullable=True)

    training_readiness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
