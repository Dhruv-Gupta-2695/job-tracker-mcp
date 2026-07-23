"""
Stores your core CV and every AI-generated tailored CV/cover letter in a
dedicated Google Drive folder. Uses the drive.file scope, which means this
can only ever see/touch files it created itself -- never your whole Drive.

Why Drive at all: Render's free tier (and most free hosts) wipes the
container's local disk between deploys/restarts, so anything written to
local disk is not a safe place to keep a CV permanently. Drive is free,
persistent, and already authenticated via the same Google login as Gmail/
Sheets, so no extra setup is needed beyond the one shared re-auth.
"""
from __future__ import annotations

import io
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from . import google_auth, tracker

FOLDER_NAME = "Job Tracker CVs"
CORE_CV_FILENAME = "core_cv.txt"


def get_drive_service():
    return build("drive", "v3", credentials=google_auth.get_credentials())


def _get_or_create_folder(service) -> str:
    """The folder id is cached in the _State tab after the first call, so
    normal usage never re-searches/re-creates it. Search-by-name is still
    the fallback (e.g. after a fresh checkout with no cached state) --
    drive.file lets a query see any file/folder the app itself created,
    even from state that's otherwise been lost."""
    cached = tracker.get_state_value("cv_drive_folder_id")
    if cached:
        return cached

    resp = service.files().list(
        q=f"name='{FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        spaces="drive", fields="files(id)",
    ).execute()
    files = resp.get("files", [])
    if files:
        folder_id = files[0]["id"]
    else:
        folder = service.files().create(
            body={"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
            fields="id",
        ).execute()
        folder_id = folder["id"]

    tracker.set_state_value("cv_drive_folder_id", folder_id)
    return folder_id


def save_core_cv(text: str) -> str:
    """Create or overwrite the single "core CV" file (there is always
    exactly one -- uploading a new one replaces the old one in place, it
    does not version itself; every AI-tailored output DOES get versioned,
    see save_generated_file). Returns the file's Drive id."""
    service = get_drive_service()
    folder_id = _get_or_create_folder(service)
    media = MediaIoBaseUpload(io.BytesIO(text.encode("utf-8")), mimetype="text/plain", resumable=False)

    file_id = tracker.get_state_value("core_cv_file_id")
    if file_id:
        try:
            service.files().update(fileId=file_id, media_body=media).execute()
            return file_id
        except Exception:  # noqa: BLE001 - file may have been deleted/moved out from under us
            pass  # fall through and create a fresh one rather than fail the whole upload

    created = service.files().create(
        body={"name": CORE_CV_FILENAME, "parents": [folder_id]},
        media_body=media, fields="id",
    ).execute()
    tracker.set_state_value("core_cv_file_id", created["id"])
    return created["id"]


def download_file(file_id: str) -> bytes:
    """Raw bytes of any file this app created (drive.file scope only ever
    sees files it created itself). Used both for the core CV and for
    letting the webapp proxy a generated PDF download without requiring
    the browser to be signed into the right Google account."""
    service = get_drive_service()
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def get_core_cv() -> Optional[str]:
    """Returns the core CV's plain text, or None if none has been uploaded
    yet (the webapp UI should prompt for an upload in that case)."""
    file_id = tracker.get_state_value("core_cv_file_id")
    if not file_id:
        return None
    return download_file(file_id).decode("utf-8", errors="ignore")


def has_core_cv() -> bool:
    return bool(tracker.get_state_value("core_cv_file_id"))


def save_generated_file(filename: str, content_bytes: bytes, mimetype: str = "application/pdf") -> tuple[str, str]:
    """Upload one generated file (a tailored CV or cover letter PDF) to the
    shared folder -- each call creates a new file, so nothing here is ever
    overwritten; that history is exactly what backs the CV "database"/
    similarity-matching feature. Returns (file_id, webViewLink)."""
    service = get_drive_service()
    folder_id = _get_or_create_folder(service)
    media = MediaIoBaseUpload(io.BytesIO(content_bytes), mimetype=mimetype, resumable=False)
    created = service.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media, fields="id, webViewLink",
    ).execute()
    link = created.get("webViewLink") or f"https://drive.google.com/file/d/{created['id']}/view"
    return created["id"], link
