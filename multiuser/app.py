"""
Multi-user portal server (Phase 6). Stateless: it stores nothing and holds no
database credentials. The browser signs in with Firebase (Google) and sends its
ID token on every call; this server only verifies the token to learn the user id
and does the Python-only work (CV parsing, PDF drawing, email classification).
User data lives in Firestore under users/{uid}, protected by firestore.rules.
"""
from __future__ import annotations

import base64
import contextvars
import io
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
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


# ---------------------------------------------------------------- limits ---
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_JD_CHARS = 20000
MAX_CV_CHARS = 30000
_RATE = {"generate": (6, 3600), "cv": (10, 3600), "check": (10, 600)}  # (calls, seconds) per user
_hits: dict[tuple[str, str], list[float]] = {}


def rate_limit(uid: str, kind: str) -> None:
    """Small in-memory per-user limit so one user cannot burn the server (resets on restart)."""
    calls, window = _RATE[kind]
    now = time.time()
    recent = [t for t in _hits.get((uid, kind), []) if now - t < window]
    if len(recent) >= calls:
        raise HTTPException(status_code=429, detail=f"Too many requests. Try again in {int(window - (now - recent[0])) // 60 + 1} minutes.")
    recent.append(now)
    _hits[(uid, kind)] = recent


def gemini_key(x_gemini_key: str | None = Header(default=None)) -> str:
    """The user's own Gemini key, sent per request. Used for that call only; never stored or logged."""
    key = (x_gemini_key or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{20,120}", key):
        raise HTTPException(status_code=400, detail="Add and unlock your Gemini key first (Setup tab).")
    return key


def _run_with_key(key: str, fn, *args):
    """Runs fn in a copy of the current context with the user's key active (safe across threads)."""
    from src import ai_generate

    def inner():
        ai_generate.use_user_key(key)
        return fn(*args)
    return contextvars.copy_context().run(inner)


def _ai_error(exc: Exception) -> HTTPException:
    text = str(exc)
    low = text.lower()
    if "api key not valid" in low or "api_key_invalid" in low or "permission_denied" in low:
        return HTTPException(status_code=400, detail="Google rejected your Gemini key. Check it in the Setup tab.")
    if "429" in text or "resource_exhausted" in low or "quota" in low:
        return HTTPException(status_code=429, detail="Your Gemini free quota is used up for now. Try again later.")
    return HTTPException(status_code=400, detail=text[:300] or "The AI step failed. Try again.")


def _extract_text(filename: str, content: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        from pypdf import PdfReader
        return "\n\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(content)).pages)
    if lower.endswith(".docx"):
        from docx import Document
        return "\n\n".join(p.text for p in Document(io.BytesIO(content)).paragraphs)
    return content.decode("utf-8", errors="ignore")


# ------------------------------------------------------------------- AI ----

@app.post("/api/gemini/check")
def gemini_check(user: dict = Depends(current_user), key: str = Depends(gemini_key)):
    from src import ai_generate
    rate_limit(user["uid"], "check")
    try:
        out = _run_with_key(key, ai_generate.generate_text, "Reply with the single word: ok", 20)
    except Exception as exc:  # noqa: BLE001
        raise _ai_error(exc) from exc
    return {"ok": bool(out)}


@app.post("/api/cv/parse")
def cv_parse(file: UploadFile = File(None), text: str = Form(None),
             user: dict = Depends(current_user), key: str = Depends(gemini_key)):
    """Reads a CV (PDF, DOCX or pasted text) into the structured model. Nothing is stored here."""
    from src import ai_generate, cv_struct
    rate_limit(user["uid"], "cv")
    links: list[str] = []
    filename = "Pasted text"
    if file is not None:
        filename = (file.filename or "uploaded file")[:120]
        content = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="That file is over 5 MB.")
        try:
            extracted = _extract_text(filename, content)
            if filename.lower().endswith(".pdf"):
                links = cv_struct.extract_links(content)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Could not read that file ({type(exc).__name__}). Try pasting the text.") from exc
    elif text:
        extracted = text
    else:
        raise HTTPException(status_code=400, detail="Provide a file or paste the CV text.")
    if not extracted.strip():
        raise HTTPException(status_code=400, detail="Could not find any text in that CV.")
    if len(extracted) > MAX_CV_CHARS:
        raise HTTPException(status_code=400, detail="That CV is too long. Keep it to a couple of pages.")
    try:
        struct = _run_with_key(key, ai_generate.parse_cv_struct, extracted, links)
    except RuntimeError as exc:
        raise _ai_error(exc) from exc
    return {"text": extracted, "struct": struct, "filename": filename, "links_found": len(links)}


class GenerateRequest(BaseModel):
    core_struct: dict
    core_text: str
    company: str
    position: str
    job_description: str


@app.post("/api/generate")
def generate(req: GenerateRequest, user: dict = Depends(current_user), key: str = Depends(gemini_key)):
    from src import ai_generate, cv_struct, pdf_export
    rate_limit(user["uid"], "generate")
    if len(req.job_description) > MAX_JD_CHARS or len(req.core_text) > MAX_CV_CHARS:
        raise HTTPException(status_code=400, detail="The job description or CV is too long.")
    try:
        core = cv_struct.normalize(req.core_struct)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Your saved CV structure is invalid. Upload your CV again.") from exc
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            cv_future = pool.submit(contextvars.copy_context().run, _with_key, key, ai_generate.tailor_cv_struct, core, req.job_description)
            cl_future = pool.submit(contextvars.copy_context().run, _with_key, key, ai_generate.generate_cover_letter,
                                    req.core_text, req.job_description, req.company, req.position)
            final_cv, stats = cv_future.result()
            letter_text = cl_future.result()
    except (RuntimeError, ValueError) as exc:
        raise _ai_error(exc) from exc
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    date_str = f"{now.day} {now.strftime('%B %Y')}"
    contact = [c for c in core["contact"] if not any(w in (c.get("url", "") + c["text"]).lower() for w in ("github", "portfolio"))]
    cv_pdf, cv_pages = pdf_export.cv_struct_to_pdf(final_cv)
    letter_pdf, _, paras = pdf_export.letter_to_pdf(core["name"], contact, letter_text, date_str)
    if not paras:
        raise HTTPException(status_code=400, detail="The AI returned an empty cover letter. Try again.")
    return {
        "cv_pdf_b64": base64.b64encode(cv_pdf).decode(), "letter_pdf_b64": base64.b64encode(letter_pdf).decode(),
        "cv_text": cv_struct.to_text(final_cv), "letter_text": "\n\n".join(paras),
        "cv_pages": cv_pages, "cv_changes": stats["changed"],
    }


def _with_key(key: str, fn, *args):
    from src import ai_generate
    ai_generate.use_user_key(key)
    return fn(*args)


# --- Same-tab Google sign-in ----------------------------------------------
# Firebase's sign-in helper pages live on <project>.firebaseapp.com. Serving them through
# this site's own address lets the browser sign in by redirect (same tab, no extra pop-up
# tab) without third-party storage problems. Only these two fixed paths are forwarded.
FIREBASE_AUTH_HOST = os.environ.get("FIREBASE_AUTH_HOST", f"{FIREBASE_PROJECT_ID}.firebaseapp.com")


@app.api_route("/__/auth/{path:path}", methods=["GET", "POST"])
@app.api_route("/__/firebase/{path:path}", methods=["GET"])
def firebase_auth_proxy(request: Request, path: str):
    import requests
    if ".." in path:
        raise HTTPException(status_code=404, detail="Not found")
    base = "/__/auth/" if request.url.path.startswith("/__/auth/") else "/__/firebase/"
    url = f"https://{FIREBASE_AUTH_HOST}{base}{path}"
    try:
        if request.method == "POST":
            upstream = requests.post(url, params=dict(request.query_params), timeout=10)
        else:
            upstream = requests.get(url, params=dict(request.query_params), timeout=10)
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Sign-in helper is unavailable. Try again.")
    return Response(content=upstream.content, status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type", "text/html"))


@app.get("/privacy")
def privacy():
    return FileResponse(STATIC_DIR / "privacy.html")


@app.get("/terms")
def terms():
    return FileResponse(STATIC_DIR / "terms.html")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
