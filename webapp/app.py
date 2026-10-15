"""
The live UI for the job tracker: view applications, upload/update your core
CV, generate AI-tailored CVs + cover letters as PDFs, browse everything
you've generated before, edit the generation prompts, and trigger a scan
on demand.

Run locally:      uvicorn webapp.app:app --reload
Deploy for free:  see the "Deploy the webapp" section of the README.

Everything here is thin glue around src/ -- the actual logic (Sheets,
Drive, AI calls, PDF rendering, GitHub dispatch) lives there and is fully
unit-tested on its own; this file is mostly request/response plumbing.
"""
from __future__ import annotations

import io
import secrets
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse  # noqa: E402
from fastapi.security import HTTPBasic, HTTPBasicCredentials  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from src import (  # noqa: E402
    ai_generate, config, cv_store, cv_struct, cv_versions, github_trigger,
    pdf_export, prompts, reply_mail, similarity, tracker,
)

app = FastAPI(title="Job Tracker")


@app.on_event("startup")
async def _limit_threads():
    """Fewer worker threads = less memory on the 512 MB free tier."""
    import anyio.to_thread
    anyio.to_thread.current_default_thread_limiter().total_tokens = 4


def _rss_mb() -> float:
    """Current resident memory of this process in MB (Linux; 0 elsewhere)."""
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * 4096 / 1024 / 1024
    except Exception:  # noqa: BLE001
        return 0.0


@app.middleware("http")
async def log_memory(request, call_next):
    """Render's free tier kills the app above 512 MB. Log memory after every
    POST and after any request that leaves the process above 250 MB, so the
    Render logs show exactly which action makes it grow."""
    before = _rss_mb()
    response = await call_next(request)
    after = _rss_mb()
    if request.method != "GET" or after > 250:
        print(f"[mem] {request.method} {request.url.path} -> {response.status_code} "
              f"rss {before:.0f} -> {after:.0f} MB", flush=True)
    return response
security = HTTPBasic()

# Icons only -- genuinely public/static, no auth needed, and the service
# worker precaches them directly, which is simplest without any auth
# dance. manifest.json and sw.js get their own routes below (sw.js in
# particular needs to be served from "/" for its scope to cover the whole
# app, not just /static/).
app.mount("/static/icons", StaticFiles(directory=PROJECT_ROOT / "webapp" / "static" / "icons"), name="icons")


@app.get("/manifest.json", include_in_schema=False)
def manifest():
    return FileResponse(PROJECT_ROOT / "webapp" / "static" / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # Served from "/" (not /static/) so its default scope covers the whole
    # app -- a service worker's scope is limited to its own path and below
    # unless the Service-Worker-Allowed header says otherwise.
    return FileResponse(PROJECT_ROOT / "webapp" / "static" / "sw.js", media_type="application/javascript")


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    """Simple shared-password gate. This app is meant to be deployed on a
    free host with a public URL, and it holds your CV, your job-application
    history, and the ability to burn your Anthropic API credits -- so it's
    never left open. If WEBAPP_PASSWORD isn't set, every request is
    rejected with a clear message rather than silently running unlocked."""
    if not config.WEBAPP_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="WEBAPP_PASSWORD is not set -- refusing to serve requests "
            "unauthenticated. Set it in your environment and restart.",
        )
    correct = secrets.compare_digest(credentials.password, config.WEBAPP_PASSWORD)
    if not correct:
        raise HTTPException(status_code=401, detail="Incorrect password", headers={"WWW-Authenticate": "Basic"})


MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_JD_CHARS = 20000


def _drive_guard(fn, *args, **kwargs):
    """Run a Drive-backed call and turn Google's errors into a message you can act on
    (an unhandled error would otherwise reach the browser as a blank 500)."""
    from googleapiclient.errors import HttpError
    try:
        return fn(*args, **kwargs)
    except HttpError as exc:
        text = str(exc)
        print(f"[drive] HttpError: {text}", flush=True)
        low = text.lower()
        if "insufficient" in low and ("scope" in low or "permission" in low):
            detail = ("The Google login on this server has no Drive permission. Re-run the Google login "
                      "locally (delete token.json, run python3 run_once.py), then paste the new token.json "
                      "into GMAIL_TOKEN_JSON on Render and in GitHub secrets.")
        elif "accessnotconfigured" in low or "has not been used" in low or "is disabled" in low:
            detail = ("The Google Drive API is switched off for your Google Cloud project. Enable it at "
                      "console.cloud.google.com > APIs & Services > Library > Google Drive API, wait a minute, retry.")
        else:
            detail = f"Google Drive refused the request: {getattr(exc, 'reason', None) or text[:300]}"
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:  # noqa: BLE001
        print(f"[drive] unexpected error: {exc!r}", flush=True)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


def _extract_text_from_upload(filename: str, content: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    if lower.endswith(".docx"):
        from docx import Document
        doc = Document(io.BytesIO(content))
        return "\n\n".join(p.text for p in doc.paragraphs)
    # .txt, .md, or anything else: treat as plain text
    return content.decode("utf-8", errors="ignore")


# --- Applications ---

@app.get("/api/applications", dependencies=[Depends(require_auth)])
def get_applications():
    apps = tracker.list_applications()
    counts: dict[str, int] = {}
    for a in apps:
        counts[a.get("Status", "")] = counts.get(a.get("Status", ""), 0) + 1
    return {"applications": apps, "counts": counts, "sheet_url": config.GOOGLE_SHEET_URL}


# --- Email replies (AI draft -> Gmail draft or guarded send) ---

class ReplyWriteRequest(BaseModel):
    thread_id: str
    instructions: str = ""


class ReplyBodyRequest(BaseModel):
    thread_id: str
    body: str


class ReplySendRequest(ReplyBodyRequest):
    confirm_token: str


def _reply_call(fn, *args):
    try:
        return fn(*args)
    except reply_mail.ReplyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:  # e.g. GEMINI_API_KEY missing
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        print(f"[reply] unexpected error: {exc!r}", flush=True)
        raise HTTPException(status_code=500, detail=f"Something went wrong: {exc}") from exc


@app.get("/api/reply/context", dependencies=[Depends(require_auth)])
def reply_context(thread_id: str):
    return _reply_call(reply_mail.thread_summary, thread_id)


@app.post("/api/reply/write", dependencies=[Depends(require_auth)])
def reply_write(req: ReplyWriteRequest):
    return _reply_call(reply_mail.write_reply, req.thread_id, req.instructions)


@app.post("/api/reply/draft", dependencies=[Depends(require_auth)])
def reply_save_draft(req: ReplyBodyRequest):
    return _reply_call(reply_mail.save_draft, req.thread_id, req.body)


@app.post("/api/reply/prepare", dependencies=[Depends(require_auth)])
def reply_prepare(req: ReplyBodyRequest):
    return _reply_call(reply_mail.prepare_send, req.thread_id, req.body)


@app.post("/api/reply/send", dependencies=[Depends(require_auth)])
def reply_send(req: ReplySendRequest):
    return _reply_call(reply_mail.send_reply, req.thread_id, req.body, req.confirm_token)


# --- Core CV ---

@app.get("/api/cv", dependencies=[Depends(require_auth)])
def get_cv():
    def build():
        has_cv = cv_store.has_core_cv()
        return {
            "has_cv": has_cv,
            "text": cv_store.get_core_cv(),
            # Only the latest upload is kept (a new one replaces the old in place).
            "filename": (tracker.get_state_value("core_cv_filename") or "") if has_cv else "",
            "uploaded_at": (tracker.get_state_value("core_cv_uploaded_at") or "") if has_cv else "",
        }
    return _drive_guard(build)


@app.post("/api/cv", dependencies=[Depends(require_auth)])
def upload_cv(file: UploadFile = File(None), text: str = Form(None)):
    source_name = "Pasted text"
    content = b""
    if file is not None:
        source_name = (file.filename or "uploaded file")[:120]
        content = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="That file is over 5 MB. Upload a smaller CV or paste the text.")
        try:
            extracted = _extract_text_from_upload(file.filename or "", content)
        except Exception as exc:  # noqa: BLE001 - corrupt/encrypted/scanned files
            raise HTTPException(status_code=400, detail=f"Could not read that file ({type(exc).__name__}). Try pasting the CV text instead.") from exc
    elif text:
        extracted = text
    else:
        raise HTTPException(status_code=400, detail="Provide either a file upload or a text field.")

    if not extracted.strip():
        raise HTTPException(status_code=400, detail="Could not extract any text from that upload.")

    links: list[str] = []
    if file is not None and (file.filename or "").lower().endswith(".pdf"):
        try:
            links = cv_struct.extract_links(content)
        except Exception as exc:  # noqa: BLE001 - links are a bonus, never block the upload
            print(f"[cv] link extraction failed: {exc!r}", flush=True)

    _drive_guard(cv_store.save_core_cv, extracted)
    struct_error = ""
    try:
        _store_struct(extracted, links)
    except Exception as exc:  # noqa: BLE001 - upload is saved; structure is retried at generate time
        struct_error = str(exc)[:300]
        print(f"[cv] structure parse failed: {exc!r}", flush=True)
    from datetime import datetime, timezone
    uploaded_at = datetime.now(timezone.utc).isoformat()
    tracker.set_state_value("core_cv_filename", source_name)
    tracker.set_state_value("core_cv_uploaded_at", uploaded_at)
    return {"ok": True, "text": extracted, "filename": source_name, "uploaded_at": uploaded_at,
            "structure_ok": not struct_error, "structure_error": struct_error}


def _store_struct(text: str, links: list[str]) -> dict:
    """Parses the core CV into the structured model and stores it next to the text."""
    import json
    struct = ai_generate.parse_cv_struct(text, links)
    tracker.set_state_value("core_cv_struct", json.dumps(struct, ensure_ascii=False))
    tracker.set_state_value("core_cv_struct_hash", cv_struct.text_hash(text))
    return struct


def _get_struct(text: str) -> dict:
    """Stored structure if it matches the current core CV text, else parse now (no links)."""
    import json
    raw = tracker.get_state_value("core_cv_struct")
    if raw and tracker.get_state_value("core_cv_struct_hash") == cv_struct.text_hash(text):
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return _store_struct(text, [])


# --- Generation ---

class GenerateRequest(BaseModel):
    company: str
    position: str
    job_description: str
    force_new: bool = False  # generate fresh even if a similar version already exists


@app.post("/api/generate", dependencies=[Depends(require_auth)])
def generate(req: GenerateRequest):
    if len(req.job_description) > MAX_JD_CHARS:
        raise HTTPException(status_code=400, detail=f"The job description is longer than {MAX_JD_CHARS} characters. Trim it.")
    core_cv = _drive_guard(cv_store.get_core_cv)
    if not core_cv:
        raise HTTPException(status_code=400, detail="Upload a core CV first (see the CV Manager tab).")

    match = None if req.force_new else similarity.find_similar(req.job_description)
    if match is not None:
        return {
            "similar_match": {
                "company": match.version["Company"],
                "position": match.version["Position"],
                "score": round(match.score, 2),
                "cv_link": match.version["Tailored CV Drive Link"],
                "cover_letter_link": match.version["Cover Letter Drive Link"],
            },
            "generated": None,
        }

    # The CV and the cover letter are independent Gemini calls: run them side by side.
    from concurrent.futures import ThreadPoolExecutor
    try:
        core_struct = _get_struct(core_cv)
        with ThreadPoolExecutor(max_workers=2) as pool:
            cv_future = pool.submit(ai_generate.tailor_cv_struct, core_struct, req.job_description)
            cl_future = pool.submit(ai_generate.generate_cover_letter, core_cv, req.job_description, req.company, req.position)
            final_cv, stats = cv_future.result()
            cover_letter_text = cl_future.result()
    except (RuntimeError, ValueError) as exc:
        print(f"[generate] AI step failed: {exc}", flush=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        print(f"[generate] unexpected AI error: {exc!r}", flush=True)
        raise HTTPException(status_code=500, detail=f"AI step failed: {type(exc).__name__}: {exc}") from exc
    if not cover_letter_text.strip():
        raise HTTPException(status_code=400, detail="Gemini returned an empty answer. Try again in a minute.")
    print(f"[generate] cv edits applied={stats['changed']} kept_original={stats['kept_original']}", flush=True)

    cv_text = cv_struct.to_text(final_cv)
    safe_company = "".join(c for c in req.company if c.isalnum() or c in " -_") or "Company"
    cv_pdf, cv_pages = pdf_export.cv_struct_to_pdf(final_cv)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    date_str = f"{now.day} {now.strftime('%B %Y')}"
    # Header contact line for the letter: the CV's own contact items, minus the portfolio-type links.
    letter_contact = [c for c in core_struct["contact"]
                      if not any(w in (c.get("url", "") + c["text"]).lower() for w in ("github", "portfolio"))]
    cover_letter_pdf, _, letter_paras = pdf_export.letter_to_pdf(
        core_struct["name"], letter_contact, cover_letter_text, date_str)
    if not letter_paras:
        raise HTTPException(status_code=400, detail="Gemini returned an empty cover letter. Try again in a minute.")
    cover_letter_text = "\n\n".join(letter_paras)

    cv_file_id, cv_link = _drive_guard(cv_store.save_generated_file, f"CV - {safe_company} - {req.position}.pdf", cv_pdf)
    cl_file_id, cl_link = _drive_guard(
        cv_store.save_generated_file,
        f"Cover Letter - {safe_company} - {req.position}.pdf", cover_letter_pdf
    )
    cv_versions.add_version(
        company=req.company, position=req.position, job_description=req.job_description,
        cv_link=cv_link, cover_letter_link=cl_link, cv_file_id=cv_file_id, cover_letter_file_id=cl_file_id,
    )

    return {
        "similar_match": None,
        "generated": {
            "cv_text": cv_text, "cover_letter_text": cover_letter_text,
            "cv_file_id": cv_file_id, "cover_letter_file_id": cl_file_id,
            "cv_link": cv_link, "cover_letter_link": cl_link,
            "cv_pages": cv_pages, "cv_changes": stats["changed"],
        },
    }


@app.get("/api/download/{file_id}", dependencies=[Depends(require_auth)])
def download(file_id: str):
    content = cv_store.download_file(file_id)
    return StreamingResponse(
        io.BytesIO(content), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{file_id}.pdf"'},
    )


@app.get("/api/cv-versions", dependencies=[Depends(require_auth)])
def get_cv_versions():
    return {"versions": list(reversed(cv_versions.list_versions()))}


# --- Prompts ---

@app.get("/api/prompts", dependencies=[Depends(require_auth)])
def get_prompts():
    return {"prompts": prompts.list_prompts()}


class PromptUpdate(BaseModel):
    name: str
    template: str


@app.post("/api/prompts", dependencies=[Depends(require_auth)])
def update_prompt(req: PromptUpdate):
    cv_ph = "{core_cv_json}" if req.name == "cv_tailor_json" else "{core_cv}"
    missing = [p for p in (cv_ph, "{job_description}") if p not in req.template]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Template must contain both {cv_ph} and {{job_description}}. Missing: {', '.join(missing)}",
        )
    prompts.set_prompt(req.name, req.template)
    return {"ok": True}


# --- Manual scan trigger ---

@app.post("/api/trigger-scan", dependencies=[Depends(require_auth)])
def trigger_scan(only_if_stale_minutes: float | None = None):
    """Fires the scan workflow. With ?only_if_stale_minutes=N it becomes a
    safe backup trigger: it skips (ok=True, triggered=False) if any run was
    already created in the last N minutes, so an external scheduler can ping
    this a little after the primary one without causing a double run."""
    try:
        if only_if_stale_minutes is not None:
            triggered = github_trigger.trigger_scan_if_stale(only_if_stale_minutes)
        else:
            github_trigger.trigger_scan()
            triggered = True
    except RuntimeError as exc:
        # Log the reason too, so it shows up in Render's logs -- the 400
        # response body alone isn't visible from cron-job.org's test run.
        print(f"[trigger-scan] failed: {exc}", flush=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "triggered": triggered}


@app.get("/api/config", dependencies=[Depends(require_auth)])
def get_config():
    """Lets the frontend know which optional features are actually usable,
    so it can hide buttons that would just fail (e.g. no GitHub PAT set)."""
    return {
        "can_trigger_scan": bool(config.GITHUB_PAT and config.GITHUB_REPO),
        "can_send_email": config.ENABLE_EMAIL_SEND,
    }


# --- Frontend ---

@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def index():
    return FileResponse(PROJECT_ROOT / "webapp" / "static" / "index.html")
