from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .db import init_db
from .routes_api import router as api_router
from .routes_auth import router as auth_router

app = FastAPI(title="Cycling & Health Analytics")

app.include_router(auth_router)
app.include_router(api_router)

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")
