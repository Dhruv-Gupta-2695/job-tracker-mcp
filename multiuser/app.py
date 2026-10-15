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

MAX_BODY_BYTES = 8 * 1024 * 1024


@app.middleware("http")
async def safety(request, call_next):
    """Caps request size, adds browser security headers, and logs only method, path and status
    (never headers, query strings or bodies, which can carry a user's keys or tokens)."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        return Response(status_code=413, content="Request too large")
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001 - log the type only; messages can contain secrets
        print(f"[error] {request.method} {request.url.path} -> {type(exc).__name__}", flush=True)
        raise
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        if response.status_code >= 400:
            print(f"[api] {request.method} {request.url.path} -> {response.status_code}", flush=True)
    return response


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
_RATE = {"generate": (6, 3600), "cv": (10, 3600), "check": (10, 600), "scan": (12, 3600), "letter": (10, 3600), "reply": (20, 3600), "draft": (30, 3600)}  # (calls, seconds) per user
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


def _check_generate_input(req: GenerateRequest) -> dict:
    from src import cv_struct
    if len(req.job_description) > MAX_JD_CHARS or len(req.core_text) > MAX_CV_CHARS:
        raise HTTPException(status_code=400, detail="The job description or CV is too long.")
    try:
        return cv_struct.normalize(req.core_struct)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Your saved CV structure is invalid. Upload your CV again.") from exc


@app.post("/api/generate")
def generate_cv(req: GenerateRequest, user: dict = Depends(current_user), key: str = Depends(gemini_key)):
    """Tailored CV only (one Gemini call)."""
    from src import ai_generate, cv_struct, pdf_export
    rate_limit(user["uid"], "generate")
    core = _check_generate_input(req)
    try:
        final_cv, stats = _run_with_key(key, ai_generate.tailor_cv_struct, core, req.job_description)
    except (RuntimeError, ValueError) as exc:
        raise _ai_error(exc) from exc
    cv_pdf, cv_pages = pdf_export.cv_struct_to_pdf(final_cv)
    return {
        "cv_pdf_b64": base64.b64encode(cv_pdf).decode(), "cv_text": cv_struct.to_text(final_cv),
        "cv_pages": cv_pages, "cv_changes": stats["changed"],
    }


@app.post("/api/generate-letter")
def generate_letter(req: GenerateRequest, user: dict = Depends(current_user), key: str = Depends(gemini_key)):
    """Cover letter only (one Gemini call)."""
    from src import ai_generate, pdf_export
    rate_limit(user["uid"], "letter")
    core = _check_generate_input(req)
    try:
        letter_text = _run_with_key(key, ai_generate.generate_cover_letter, req.core_text, req.job_description, req.company, req.position)
    except (RuntimeError, ValueError) as exc:
        raise _ai_error(exc) from exc
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    date_str = f"{now.day} {now.strftime('%B %Y')}"
    contact = [c for c in core["contact"] if not any(w in (c.get("url", "") + c["text"]).lower() for w in ("github", "portfolio"))]
    letter_pdf, _, paras = pdf_export.letter_to_pdf(core["name"], contact, letter_text, date_str)
    if not paras:
        raise HTTPException(status_code=400, detail="The AI returned an empty cover letter. Try again.")
    return {"letter_pdf_b64": base64.b64encode(letter_pdf).decode(), "letter_text": "\n\n".join(paras)}


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


def _gmail_token(x_gmail_token: str | None) -> str:
    token = (x_gmail_token or "").strip()
    if not token or len(token) > 4096:
        raise HTTPException(status_code=400, detail="Connect Gmail first.")
    return token


class ScanListRequest(BaseModel):
    since_iso: str | None = None      # last scan time (UTC ISO); None = first scan
    days: int = 60                    # look-back for a first scan
    seen_ids: list[str] = []          # message ids already processed


@app.post("/api/scan/list")
def scan_list(req: ScanListRequest, user: dict = Depends(current_user), x_gmail_token: str | None = Header(default=None)):
    """Step 1 of a scan: which messages are new (oldest first). Cheap; the browser then sends
    them in small batches to /api/scan/process, so no single request runs long."""
    from datetime import timedelta, timezone
    from src import gmail_rest
    token = _gmail_token(x_gmail_token)
    if len(req.seen_ids) > 6000:
        raise HTTPException(status_code=400, detail="Too much data in one scan.")
    rate_limit(user["uid"], "scan")
    if req.since_iso:
        try:
            since = datetime.fromisoformat(req.since_iso.replace("Z", "+00:00"))
            since = datetime.fromtimestamp(since.timestamp()) - timedelta(hours=1)  # overlap; seen ids dedupe
        except ValueError:
            raise HTTPException(status_code=400, detail="Bad scan time.")
    else:
        since = datetime.now() - timedelta(days=max(1, min(req.days, 180)))
    try:
        ids = gmail_rest.list_message_ids(token, since, set(req.seen_ids))
    except gmail_rest.GmailTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except gmail_rest.GmailBusyError as exc:
        raise HTTPException(status_code=429, detail="Gmail is busy. Try the scan again in a minute.") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="Could not read Gmail right now. Try again.") from exc
    return {"ids": ids, "started_at": datetime.now(timezone.utc).isoformat()}


class ScanProcessRequest(BaseModel):
    ids: list[str]
    existing: list[dict] = []
    user_email: str = ""


@app.post("/api/scan/process")
def scan_process(req: ScanProcessRequest, user: dict = Depends(current_user), x_gmail_token: str | None = Header(default=None)):
    """Step 2: reads one small batch of messages and returns the new or changed applications."""
    from src import gmail_rest, scan_core
    token = _gmail_token(x_gmail_token)
    if len(req.ids) > 60 or len(req.existing) > 3000:
        raise HTTPException(status_code=400, detail="Batch too large.")
    try:
        messages = gmail_rest.fetch_messages(token, req.ids)
    except gmail_rest.GmailTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except gmail_rest.GmailBusyError as exc:
        raise HTTPException(status_code=429, detail="Gmail is busy. Waiting a moment...") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="Could not read Gmail right now. Try again.") from exc
    out = scan_core.process_messages(messages, req.existing, set(), req.user_email or user.get("email", ""))
    out["seen"] = list(dict.fromkeys(list(out["seen"]) + list(req.ids)))  # ids that no longer exist are done too
    return out


# ------------------------------------------------------------- replies ----
# Sending is off unless the server owner turns it on: ENABLE_EMAIL_SEND=true and a REPLY_SECRET
# (a long random string used to sign confirmations). Drafting with AI and saving to Gmail Drafts
# always work. The caps below protect users from mistakes and loops.
ENABLE_EMAIL_SEND = os.environ.get("ENABLE_EMAIL_SEND", "").strip().lower() == "true"
REPLY_SECRET = os.environ.get("REPLY_SECRET", "").strip()
MAX_SENDS_PER_DAY = int(os.environ.get("MAX_SENDS_PER_DAY", "5") or 5)
_recent_sends: dict[str, list[dict]] = {}  # best-effort memory per user (also checked: the log the page sends)


def can_send() -> bool:
    return ENABLE_EMAIL_SEND and len(REPLY_SECRET) >= 16


@app.get("/api/config")
def api_config(user: dict = Depends(current_user)):
    return {"can_send": can_send(), "max_sends_per_day": MAX_SENDS_PER_DAY}


def _reply_call(fn):
    from src import gmail_rest, reply_logic
    try:
        return fn()
    except reply_logic.ReplyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except gmail_rest.GmailScopeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except gmail_rest.GmailTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except gmail_rest.GmailBusyError as exc:
        raise HTTPException(status_code=429, detail="Gmail is busy. Try again in a minute.") from exc
    except HTTPException:
        raise
    except RuntimeError as exc:  # AI errors
        raise _ai_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        print(f"[reply] unexpected error: {type(exc).__name__}", flush=True)
        raise HTTPException(status_code=502, detail="Could not complete that with Gmail. Try again.") from exc


def _load_thread(token: str, thread_id: str):
    from src import gmail_rest, reply_logic
    if not re.fullmatch(r"[A-Za-z0-9]{6,40}", thread_id or ""):
        raise reply_logic.ReplyError("That email thread id is not valid.")
    me = gmail_rest.get_profile_email(token)
    messages = gmail_rest.get_thread(token, thread_id)
    if not messages:
        raise reply_logic.ReplyError("That email thread could not be found in Gmail.")
    return me, messages


class ReplyContextReq(BaseModel):
    thread_id: str


class ReplyWriteReq(BaseModel):
    thread_id: str
    instructions: str = ""
    company: str = ""
    position: str = ""
    status: str = ""
    core_text: str = ""
    name: str = ""


class ReplyBodyReq(BaseModel):
    thread_id: str
    body: str
    status: str = ""


class ReplySendReq(BaseModel):
    thread_id: str
    body: str
    confirm_token: str = ""
    company: str = ""
    status: str = ""
    sent_log: list[dict] = []


@app.post("/api/reply/context")
def reply_context(req: ReplyContextReq, user: dict = Depends(current_user), x_gmail_token: str | None = Header(default=None)):
    """The last message in the chain: subject, whether you sent or received it, and when."""
    token = _gmail_token(x_gmail_token)

    def go():
        me, messages = _load_thread(token, req.thread_id)
        from src.gmail_client import extract_email_address
        last = messages[-1]
        mine = extract_email_address(last["from"]).lower() == me
        return {"subject": (last["subject"] or "").strip() or "(no subject)",
                "direction": "sent" if mine else "received",
                "date": datetime.fromtimestamp(last["ts"], ZoneInfo("UTC")).isoformat(), "messages": len(messages)}
    return _reply_call(go)


@app.post("/api/reply/write")
def reply_write(req: ReplyWriteReq, user: dict = Depends(current_user), key: str = Depends(gemini_key),
                x_gmail_token: str | None = Header(default=None)):
    from src import ai_generate, reply_logic
    token = _gmail_token(x_gmail_token)
    rate_limit(user["uid"], "reply")

    def go():
        reply_logic.check_not_rejected(req.status)
        me, messages = _load_thread(token, req.thread_id)
        to, last = reply_logic.choose_recipient(messages, me)
        prompt = reply_logic.build_prompt((req.name or user.get("name") or "the applicant")[:80], req.company, req.position,
                                          messages, me, req.core_text[:MAX_CV_CHARS], req.instructions)
        body = _run_with_key(key, ai_generate.generate_text, prompt)
        return {"to": to, "subject": reply_logic.reply_subject(last["subject"]), "body": body, "can_send": can_send()}
    return _reply_call(go)


@app.post("/api/reply/draft")
def reply_draft(req: ReplyBodyReq, user: dict = Depends(current_user), x_gmail_token: str | None = Header(default=None)):
    """Puts the reply in Gmail Drafts (threaded). Never sends anything."""
    from src import gmail_rest, reply_logic
    token = _gmail_token(x_gmail_token)
    rate_limit(user["uid"], "draft")

    def go():
        body = reply_logic.lint_body(req.body)
        reply_logic.check_not_rejected(req.status)
        me, messages = _load_thread(token, req.thread_id)
        to, last = reply_logic.choose_recipient(messages, me)
        subject = reply_logic.reply_subject(last["subject"])
        draft_id = gmail_rest.create_draft(token, reply_logic.build_raw(to, subject, body, last), req.thread_id)
        return {"ok": True, "draft_id": draft_id, "to": to}
    return _reply_call(go)


def _sent_today(uid: str, client_log: list[dict]) -> tuple[int, set[str]]:
    today = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d")
    entries = [e for e in client_log[-200:] if isinstance(e, dict)] + _recent_sends.get(uid, [])
    keys = {e.get("key") for e in entries if e.get("status", "sent") in ("sent", "sending")}
    n = len({e.get("key") for e in entries if e.get("day") == today and e.get("status", "sent") in ("sent", "sending")})
    return n, keys


def _require_send_enabled():
    from src import reply_logic
    if not can_send():
        raise reply_logic.ReplyError("Sending is turned off on this site. Save the reply to Gmail Drafts instead.")


@app.post("/api/reply/prepare")
def reply_prepare(req: ReplySendReq, user: dict = Depends(current_user), x_gmail_token: str | None = Header(default=None)):
    """Step 1 of sending: validate everything and return a preview plus a confirmation token."""
    from src import reply_logic
    token = _gmail_token(x_gmail_token)

    def go():
        _require_send_enabled()
        body = reply_logic.lint_body(req.body)
        reply_logic.check_not_rejected(req.status)
        me, messages = _load_thread(token, req.thread_id)
        to, last = reply_logic.choose_recipient(messages, me)
        subject = reply_logic.reply_subject(last["subject"])
        used, _ = _sent_today(user["uid"], req.sent_log)
        if used >= MAX_SENDS_PER_DAY:
            raise reply_logic.ReplyError(f"Daily limit of {MAX_SENDS_PER_DAY} sent emails reached. Try tomorrow.")
        return {"to": to, "subject": subject, "body": body, "company": req.company,
                "sends_left_today": MAX_SENDS_PER_DAY - used,
                "confirm_token": reply_logic.make_token(REPLY_SECRET, user["uid"], req.thread_id, to, subject, body)}
    return _reply_call(go)


@app.post("/api/reply/send")
def reply_send(req: ReplySendReq, user: dict = Depends(current_user), x_gmail_token: str | None = Header(default=None)):
    """Step 2: sends only with a valid, unexpired confirmation for this exact recipient, subject and text."""
    import time
    from src import gmail_rest, reply_logic
    token = _gmail_token(x_gmail_token)

    def go():
        _require_send_enabled()
        body = reply_logic.lint_body(req.body)
        reply_logic.check_not_rejected(req.status)
        me, messages = _load_thread(token, req.thread_id)
        to, last = reply_logic.choose_recipient(messages, me)
        subject = reply_logic.reply_subject(last["subject"])
        reply_logic.verify_token(REPLY_SECRET, user["uid"], req.thread_id, to, subject, body, req.confirm_token)

        key = reply_logic.idempotency_key(req.thread_id, to, subject, body)
        used, keys = _sent_today(user["uid"], req.sent_log)
        if key in keys:
            return {"ok": True, "already_sent": True, "to": to, "entry": None}
        if used >= MAX_SENDS_PER_DAY:
            raise reply_logic.ReplyError(f"Daily limit of {MAX_SENDS_PER_DAY} sent emails reached. Try tomorrow.")
        now = time.time()
        recent = _recent_sends.setdefault(user["uid"], [])
        recent[:] = [e for e in recent if now - e["ts"] < 86400]
        for e in list(recent) + [x for x in req.sent_log[-200:] if isinstance(x, dict)]:
            if e.get("thread_id") == req.thread_id and e.get("status", "sent") in ("sent", "sending") \
                    and now - float(e.get("ts", 0)) < reply_logic.THREAD_COOLDOWN_SECONDS:
                raise reply_logic.ReplyError("You just sent a reply in this thread. Wait a few minutes before another.")

        # Remember the attempt BEFORE calling Gmail so a retry can never double-send.
        entry = {"key": key, "thread_id": req.thread_id, "to": to, "subject": subject, "ts": now,
                 "day": datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d"), "status": "sending"}
        recent.append(entry)
        try:
            message_id = gmail_rest.send_message(token, reply_logic.build_raw(to, subject, body, last), req.thread_id)
        except Exception:
            recent.remove(entry)
            raise
        entry["status"] = "sent"
        return {"ok": True, "to": to, "entry": {**entry, "message_id": message_id}}
    return _reply_call(go)


@app.get("/manifest.json", include_in_schema=False)
def manifest():
    return FileResponse(STATIC_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # Served from the root so it can control the whole site; never cached so updates arrive at once.
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


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
