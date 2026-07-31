"""MCP server exposing Garmin Connect wellness data to MCP clients.

Garmin's official developer program splits activities from health data, and the
health half needs separate approval. Connectors built on the official API can
therefore serve activities while returning nothing for sleep, HRV, Body Battery,
stress, SpO2 and training readiness. This server fills that gap by reading the
same metrics through the unofficial garminconnect/garth client that the rest of
this app already uses.

Run it over stdio:

    python -m app.mcp_server

Authentication is resolved in this order:

1. ``GARMIN_TOKEN_STORE`` - path to a garth token directory (recommended).
2. The web app's SQLite database, if you already logged in through the UI.
3. ``GARMIN_EMAIL`` / ``GARMIN_PASSWORD``, which fails when Garmin wants MFA.

Create a token store once with ``python -m app.garmin_login``.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from garminconnect import Garmin
from mcp.server import MCPServer

from . import garmin_client

# Garmin is queried one day at a time, so a range costs one request per day.
# A small pool keeps multi-week queries responsive without hammering an
# unofficial endpoint hard enough to get the account rate limited.
MAX_WORKERS = int(os.environ.get("GARMIN_MCP_MAX_WORKERS", "4"))
MAX_DAYS = 100

mcp = MCPServer(
    name="garmin-wellness",
    version="0.1.0",
    instructions=(
        "Read Garmin Connect recovery and wellness data: sleep, HRV, Body Battery, "
        "stress, SpO2, training readiness and resting heart rate. Use "
        "get_wellness_snapshot for a quick 'how recovered am I today' answer, and the "
        "per-metric tools when you need history over a date range. Days where the "
        "watch was not worn are omitted rather than returned as zeroes."
    ),
)

_client: Garmin | None = None


def _build_client() -> Garmin:
    """Resolve credentials from the environment, then the app database."""

    token_store = os.environ.get("GARMIN_TOKEN_STORE")
    if token_store:
        return garmin_client.client_from_token_store(token_store)

    try:
        from .db import SessionLocal
    except Exception:  # pragma: no cover - only when SQLAlchemy is unavailable
        SessionLocal = None

    if SessionLocal is not None:
        db = SessionLocal()
        try:
            if garmin_client.is_connected(db):
                return garmin_client.get_client(db)
        finally:
            db.close()

    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if email and password:
        return garmin_client.client_from_credentials(email, password)

    raise RuntimeError(
        "Garmin is not connected. Run 'python -m app.garmin_login' to create a token "
        "store and point GARMIN_TOKEN_STORE at it, or log in through the web app first."
    )


def _get_client() -> Garmin:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def _resolve_range(from_date: str | None, to_date: str | None, limit: int) -> list[str]:
    """Turn the optional filters into a concrete list of days, newest first."""

    limit = max(1, min(int(limit), MAX_DAYS))
    end = date.fromisoformat(to_date) if to_date else date.today()

    if from_date:
        start = date.fromisoformat(from_date)
        if start > end:
            raise ValueError(f"from_date ({from_date}) is after to_date ({end.isoformat()}).")
        span = (end - start).days + 1
        days = min(span, limit)
    else:
        days = limit

    return [(end - timedelta(days=offset)).isoformat() for offset in range(days)]


def _series(metric: str, from_date: str | None, to_date: str | None, limit: int) -> list[dict]:
    """Fetch one metric across a date range, dropping days Garmin has no data for."""

    client = _get_client()
    dates = _resolve_range(from_date, to_date, limit)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        payloads = list(pool.map(lambda d: garmin_client.fetch_metric(client, metric, d), dates))

    return [{"date": d, **payload} for d, payload in zip(dates, payloads) if payload]


@mcp.tool(
    description=(
        "Get a single snapshot of the latest recovery metrics: sleep, HRV, Body "
        "Battery, stress, SpO2, training readiness and resting heart rate. Ideal for "
        "quick recovery-based coaching recommendations."
    )
)
def get_wellness_snapshot(day: str | None = None) -> dict:
    """Everything for one day (YYYY-MM-DD), defaulting to today."""

    client = _get_client()
    day = day or _today()
    snapshot = garmin_client.fetch_daily_wellness(client, day)

    if len(snapshot) == 1:  # only the "date" key survived
        return {
            "date": day,
            "data_available": False,
            "note": (
                "Garmin returned no wellness data for this day. The watch may not have "
                "been worn, or the day may not have synced to Garmin Connect yet."
            ),
        }
    return {**snapshot, "data_available": True}


@mcp.tool(
    description=(
        "Get sleep summaries: total duration, deep/light/REM/awake breakdown and "
        "sleep score. Returns the most recent nights when no dates are given."
    )
)
def get_sleep_summary(
    from_date: str | None = None, to_date: str | None = None, limit: int = 7
) -> list[dict]:
    return _series("sleep", from_date, to_date, limit)


@mcp.tool(
    description=(
        "Get HRV (heart rate variability) status including last night's average, "
        "5-minute high, weekly average and the personal baseline range."
    )
)
def get_hrv_status(
    from_date: str | None = None, to_date: str | None = None, limit: int = 7
) -> list[dict]:
    return _series("hrv", from_date, to_date, limit)


@mcp.tool(
    description=(
        "Get Body Battery energy levels per day: highest, lowest, total charged and "
        "total drained."
    )
)
def get_body_battery(
    from_date: str | None = None, to_date: str | None = None, limit: int = 7
) -> list[dict]:
    return _series("body_battery", from_date, to_date, limit)


@mcp.tool(
    description=(
        "Get daily stress metrics: average and max stress plus time spent at rest, "
        "low, medium and high stress levels."
    )
)
def get_stress(
    from_date: str | None = None, to_date: str | None = None, limit: int = 7
) -> list[dict]:
    return _series("stress", from_date, to_date, limit)


@mcp.tool(description="Get overnight pulse oximetry (SpO2) averages and lowest values.")
def get_spo2(
    from_date: str | None = None, to_date: str | None = None, limit: int = 7
) -> list[dict]:
    return _series("spo2", from_date, to_date, limit)


@mcp.tool(
    description=(
        "Get training readiness score (0-100), level, feedback and the contributing "
        "factors: sleep, HRV, recovery time, stress history and acute load."
    )
)
def get_training_readiness(
    from_date: str | None = None, to_date: str | None = None, limit: int = 7
) -> list[dict]:
    return _series("training_readiness", from_date, to_date, limit)


@mcp.tool(
    description=(
        "Get daily rollups of resting heart rate, step count and total calories."
    )
)
def get_daily_stats(
    from_date: str | None = None, to_date: str | None = None, limit: int = 7
) -> list[dict]:
    return _series("daily_stats", from_date, to_date, limit)


@mcp.tool(
    description=(
        "Check whether the server can reach Garmin Connect and which metrics are "
        "currently returning data. Use this to diagnose empty results."
    )
)
def get_connection_status() -> dict:
    try:
        client = _get_client()
    except Exception as exc:
        return {"connected": False, "error": str(exc)}

    today = _today()
    probe = {
        metric: bool(garmin_client.fetch_metric(client, metric, today))
        for metric in garmin_client.WELLNESS_FETCHERS
    }
    if not any(probe.values()):
        # Today is frequently still empty early in the morning; try yesterday
        # before reporting the account as dataless.
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        probe = {
            metric: bool(garmin_client.fetch_metric(client, metric, yesterday))
            for metric in garmin_client.WELLNESS_FETCHERS
        }
        today = yesterday

    return {"connected": True, "probed_date": today, "metrics_with_data": probe}


def _today() -> str:
    return date.today().isoformat()


def main() -> None:
    logging.basicConfig(level=os.environ.get("GARMIN_MCP_LOG_LEVEL", "INFO"))
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
