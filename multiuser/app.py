"""
Multi-user portal server (Phase 6). Stateless: it stores nothing and holds no
database credentials. The browser signs in with Firebase (Google) and sends its
ID token on every call; this server only verifies the token to learn the user id
and does the Python-only work (CV parsing, PDF drawing, email classification).
User data lives in Firestore under users/{uid}, protected by firestore.rules.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "job-tracker-820d3")
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Job Tracker (multi-user)")


def verify_id_token(token: str) -> dict:
    """Returns the verified Firebase token claims (uid in 'sub'/'user_id'). Raises ValueError if invalid."""
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token
    return id_token.verify_firebase_token(token, g_requests.Request(), audience=FIREBASE_PROJECT_ID)


def current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in first.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = verify_id_token(token)
    except Exception:  # noqa: BLE001 - any failure means not authenticated; never leak details
        claims = None
    if not claims or not claims.get("sub"):
        raise HTTPException(status_code=401, detail="Your sign-in expired. Sign in again.")
    return {"uid": claims["sub"], "email": claims.get("email", ""), "name": claims.get("name", "")}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return user


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
