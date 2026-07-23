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
from pydantic import BaseModel  # noqa: E402

from src import (  # noqa: E402
    ai_generate, config, cv_store, cv_versions, github_trigger,
    pdf_export, prompts, similarity, tracker,
)

app = FastAPI(title="Job Tracker")
security = HTTPBasic()


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


# --- Core CV ---

@app.get("/api/cv", dependencies=[Depends(require_auth)])
def get_cv():
    return {"has_cv": cv_store.has_core_cv(), "text": cv_store.get_core_cv()}


@app.post("/api/cv", dependencies=[Depends(require_auth)])
def upload_cv(file: UploadFile = File(None), text: str = Form(None)):
    if file is not None:
        content = file.file.read()
        extracted = _extract_text_from_upload(file.filename or "", content)
    elif text:
        extracted = text
    else:
        raise HTTPException(status_code=400, detail="Provide either a file upload or a text field.")

    if not extracted.strip():
        raise HTTPException(status_code=400, detail="Could not extract any text from that upload.")

    cv_store.save_core_cv(extracted)
    return {"ok": True, "text": extracted}


# --- Generation ---

class GenerateRequest(BaseModel):
    company: str
    position: str
    job_description: str
    force_new: bool = False  # generate fresh even if a similar version already exists


@app.post("/api/generate", dependencies=[Depends(require_auth)])
def generate(req: GenerateRequest):
    core_cv = cv_store.get_core_cv()
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

    cv_text = ai_generate.tailor_cv(core_cv, req.job_description)
    cover_letter_text = ai_generate.generate_cover_letter(core_cv, req.job_description)

    safe_company = "".join(c for c in req.company if c.isalnum() or c in " -_") or "Company"
    cv_pdf = pdf_export.cv_to_pdf(config.CANDIDATE_NAME, cv_text)
    cover_letter_pdf = pdf_export.cover_letter_to_pdf(config.CANDIDATE_NAME, req.company, cover_letter_text)

    cv_file_id, cv_link = cv_store.save_generated_file(f"CV - {safe_company} - {req.position}.pdf", cv_pdf)
    cl_file_id, cl_link = cv_store.save_generated_file(
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
    missing = [p for p in ("{core_cv}", "{job_description}") if p not in req.template]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Template must contain both {{core_cv}} and {{job_description}}. Missing: {', '.join(missing)}",
        )
    prompts.set_prompt(req.name, req.template)
    return {"ok": True}


# --- Manual scan trigger ---

@app.post("/api/trigger-scan", dependencies=[Depends(require_auth)])
def trigger_scan():
    try:
        github_trigger.trigger_scan()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/config", dependencies=[Depends(require_auth)])
def get_config():
    """Lets the frontend know which optional features are actually usable,
    so it can hide buttons that would just fail (e.g. no GitHub PAT set)."""
    return {"can_trigger_scan": bool(config.GITHUB_PAT and config.GITHUB_REPO)}


# --- Frontend ---

@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def index():
    return FileResponse(PROJECT_ROOT / "webapp" / "static" / "index.html")
