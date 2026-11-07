"""Dati in ingresso, indipendenti dalla fonte (Garmin o Strava)."""

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Activity:
    day: date
    start: datetime          # ora locale
    sport: str               # bike | strength | light | other
    duration_s: float
    np: float | None = None  # normalized power (W)
    avg_hr: float | None = None
    tss: float | None = None  # TSS gia' calcolato dalla fonte (Garmin), se c'e'
    p60: float | None = None  # miglior 1' (serve a leggere il ramp test)
    p300: float | None = None
    p1200: float | None = None
    name: str = ""
    source: str = "garmin"


@dataclass
class Wellness:
    day: date
    rhr: float | None = None
    hrv: float | None = None
    hrv_low: float | None = None
    hrv_high: float | None = None
    hrv_status: str | None = None
    sleep_h: float | None = None
    sleep_score: float | None = None
    body_battery: float | None = None
    stress: float | None = None

    @property
    def has_morning_data(self):
        return self.sleep_h is not None or self.hrv is not None


@dataclass
class Data:
    activities: list = field(default_factory=list)
    wellness: dict = field(default_factory=dict)   # {date: Wellness}
    weights: list = field(default_factory=list)    # [(date, kg)]
    warnings: list = field(default_factory=list)
    has_activities: bool = False                   # False se la fonte delle attivita' non ha risposto
