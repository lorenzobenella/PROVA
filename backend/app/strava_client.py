"""Strava OAuth2 + REST API client (official public API)."""

import time
import urllib.parse

import httpx
from sqlalchemy.orm import Session

from .config import settings
from .models import StravaToken

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_BASE = "https://www.strava.com/api/v3"


def get_authorize_url() -> str:
    params = {
        "client_id": settings.strava_client_id,
        "redirect_uri": settings.strava_redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": "read,activity:read_all,profile:read_all",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(db: Session, code: str) -> StravaToken:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": settings.strava_client_id,
            "client_secret": settings.strava_client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return _store_token(db, data)


def _store_token(db: Session, data: dict) -> StravaToken:
    token = db.get(StravaToken, 1)
    if token is None:
        token = StravaToken(id=1)
        db.add(token)
    token.athlete_id = data.get("athlete", {}).get("id") or token.athlete_id
    token.access_token = data["access_token"]
    token.refresh_token = data["refresh_token"]
    token.expires_at = data["expires_at"]
    db.commit()
    db.refresh(token)
    return token


def _refresh(db: Session, token: StravaToken) -> StravaToken:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": settings.strava_client_id,
            "client_secret": settings.strava_client_secret,
            "refresh_token": token.refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return _store_token(db, resp.json())


def get_valid_token(db: Session) -> StravaToken | None:
    token = db.get(StravaToken, 1)
    if token is None:
        return None
    if token.expires_at <= int(time.time()) + 60:
        token = _refresh(db, token)
    return token


def _auth_headers(db: Session) -> dict:
    token = get_valid_token(db)
    if token is None:
        raise RuntimeError("Strava not connected. Visit /api/auth/strava/login first.")
    return {"Authorization": f"Bearer {token.access_token}"}


def list_activities(db: Session, after_epoch: int | None = None, per_page: int = 100) -> list[dict]:
    activities: list[dict] = []
    page = 1
    headers = _auth_headers(db)
    while True:
        params: dict = {"per_page": per_page, "page": page}
        if after_epoch:
            params["after"] = after_epoch
        resp = httpx.get(f"{API_BASE}/athlete/activities", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        activities.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return activities


def get_activity_streams(db: Session, activity_id: str) -> dict:
    headers = _auth_headers(db)
    resp = httpx.get(
        f"{API_BASE}/activities/{activity_id}/streams",
        headers=headers,
        params={"keys": "watts,time", "key_by_type": "true"},
        timeout=30,
    )
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    return resp.json()
