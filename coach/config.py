"""Configurazione dell'atleta (atleta.toml) e storico dei test FTP (ftp_tests.json)."""

import json
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG_FILE = HERE / "atleta.toml"
# Lo stesso file usato dalla dashboard Garmin: una sola fonte per i test FTP.
FTP_TESTS_FILE = ROOT / "garmin-dashboard" / "ftp_tests.json"
OUT_DIR = HERE / "out"
STATE_FILE = HERE / "state" / "state.json"

WEEKDAYS = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
BLOCKS = ("vo2", "soglia", "misto")


@dataclass
class Config:
    weight_kg: float = 70.0
    height_cm: float = 178.0
    ftp_w: float = 300.0
    lthr: float | None = 169.0
    tz: str = "Europe/Rome"
    start: date = date(2026, 11, 9)
    blocks: list = field(default_factory=lambda: list(BLOCKS))
    availability: list = field(default_factory=lambda: [60, 60, 60, 60, 60, 120, 120])
    exceptions: dict = field(default_factory=dict)

    def minutes_for(self, day):
        return self.exceptions.get(day, self.availability[day.weekday()])


def load(path=CONFIG_FILE):
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    athlete = raw.get("atleta", {})
    plan = raw.get("piano", {})
    avail = raw.get("disponibilita", {})
    cfg = Config()
    cfg.weight_kg = float(athlete.get("peso_kg", cfg.weight_kg))
    cfg.height_cm = float(athlete.get("altezza_cm", cfg.height_cm))
    cfg.ftp_w = float(athlete.get("ftp_w", cfg.ftp_w))
    cfg.lthr = float(athlete["lthr_bici"]) if athlete.get("lthr_bici") else None
    cfg.tz = athlete.get("fuso_orario", cfg.tz)
    start = plan.get("inizio", cfg.start)
    cfg.start = start if isinstance(start, date) else date.fromisoformat(str(start))
    if cfg.start.weekday() != 0:
        raise ValueError("piano.inizio deve essere un lunedì (%s non lo è)" % cfg.start)
    blocks = plan.get("blocchi") or cfg.blocks
    unknown = [b for b in blocks if b not in BLOCKS]
    if unknown:
        raise ValueError("Blocchi sconosciuti in atleta.toml: %s (validi: %s)" % (unknown, ", ".join(BLOCKS)))
    cfg.blocks = list(blocks)
    cfg.availability = [int(avail.get(d, cfg.availability[i])) for i, d in enumerate(WEEKDAYS)]
    cfg.exceptions = {date.fromisoformat(str(k)): int(v) for k, v in (raw.get("eccezioni") or {}).items()}
    return cfg


# ---------------------------------------------------------------- test FTP

def load_tests(path=FTP_TESTS_FILE):
    """[(data, watt, nota)] ordinati per data."""
    path = Path(path)
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8") or "[]")
    return sorted((date.fromisoformat(r["date"]), float(r["watts"]), r.get("note") or "test") for r in rows)


def append_test(day, watts, note, path=FTP_TESTS_FILE):
    """Aggiunge un test mantenendo il formato del file (una riga per test)."""
    path = Path(path)
    rows = json.loads(path.read_text(encoding="utf-8") or "[]") if path.exists() else []
    rows.append({"date": day.isoformat(), "watts": int(round(watts)), "note": note})
    rows.sort(key=lambda r: r["date"])
    body = ",\n".join("  " + json.dumps(r, ensure_ascii=False) for r in rows)
    path.write_text("[\n" + body + "\n]\n", encoding="utf-8")
