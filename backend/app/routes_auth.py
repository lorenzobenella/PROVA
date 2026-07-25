from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import garmin_client, strava_client
from .db import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/strava/login")
def strava_login():
    return RedirectResponse(strava_client.get_authorize_url())


@router.get("/strava/callback")
def strava_callback(code: str, db: Session = Depends(get_db)):
    strava_client.exchange_code(db, code)
    return RedirectResponse("/?strava=connected")


@router.get("/status")
def auth_status(db: Session = Depends(get_db)):
    strava_token = strava_client.get_valid_token(db) if _has_strava_config() else None
    return {
        "strava_connected": strava_token is not None,
        "garmin_connected": garmin_client.is_connected(db),
    }


def _has_strava_config() -> bool:
    from .config import settings

    return bool(settings.strava_client_id and settings.strava_client_secret)


class GarminLoginRequest(BaseModel):
    email: str
    password: str


class GarminMfaRequest(BaseModel):
    session_id: str
    code: str


@router.post("/garmin/login")
def garmin_login(body: GarminLoginRequest, db: Session = Depends(get_db)):
    try:
        result = garmin_client.start_login(body.email, body.password)
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

    if result.get("mfa_required"):
        return {"mfa_required": True, "session_id": result["session_id"]}

    garmin_client._save_session(db, result["email"], result["client"])
    return {"mfa_required": False, "connected": True}


@router.post("/garmin/mfa")
def garmin_mfa(body: GarminMfaRequest, db: Session = Depends(get_db)):
    try:
        garmin_client.finish_mfa(db, body.session_id, body.code)
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))
    return {"connected": True}
