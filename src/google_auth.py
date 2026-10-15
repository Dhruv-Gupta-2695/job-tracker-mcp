"""
Shared Google OAuth logic used by both the Gmail client and the Sheets
client -- one login grants both scopes at once (gmail.readonly +
spreadsheets), backed by the same credentials.json/token.json pair, so you
only ever go through the browser consent screen once for the whole project.
"""
from __future__ import annotations

import os
import threading
from typing import Optional

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from . import config


class GoogleAuthExpired(RuntimeError):
    """Raised when the cached refresh token no longer works -- most likely
    because the Google Cloud project is still in "Testing" publishing
    status, where Google expires refresh tokens after 7 days. Fix: run
    'python3 run_once.py' manually on a machine with a browser to log in
    again. The stale token.json is deleted automatically so the next
    manual run goes straight to the interactive login instead of failing
    the same way."""


def get_credentials() -> Credentials:
    """Authenticate (via cached token or interactive OAuth flow) and return
    valid Google credentials, usable for building either the Gmail or
    Sheets API client. Raises GoogleAuthExpired if the cached refresh token
    has expired/been revoked -- callers running unattended (cron, GitHub
    Actions, the daemon) should catch this specifically rather than let the
    process hang trying to open a browser that isn't there."""
    creds: Optional[Credentials] = None
    token_path = config.GMAIL_TOKEN_PATH

    try:
        creds = Credentials.from_authorized_user_file(token_path, config.GOOGLE_SCOPES)
    except (FileNotFoundError, ValueError):
        creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                if os.path.exists(token_path):
                    os.remove(token_path)
                raise GoogleAuthExpired(
                    "Google refresh token expired or was revoked. Run "
                    "'python3 run_once.py' manually on a machine with a "
                    "browser to log in again."
                ) from exc
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                config.GMAIL_CREDENTIALS_PATH, config.GOOGLE_SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return creds


# ---- Shared API clients -------------------------------------------------
# build() parses a large API description (~25 MB each). One client per name is
# built for the whole process and shared by every thread. httplib2 is not
# thread-safe, so all network calls go through one lock (_LockedHttp); with a
# single user this serialisation is invisible, and memory stays flat on
# Render's 512 MB free tier (a client per worker thread, plus per short-lived
# thread, is what ran it out of memory). The client is rebuilt if its
# credentials stop being valid, so token refresh and GoogleAuthExpired behave
# as before.
_services: dict = {}
_services_lock = threading.Lock()
_http_lock = threading.RLock()


def _locked_http():
    import httplib2

    class _LockedHttp(httplib2.Http):
        def request(self, *args, **kwargs):
            with _http_lock:
                return super().request(*args, **kwargs)

    return _LockedHttp(timeout=60)


def get_service(name: str, version: str):
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    with _services_lock:
        entry = _services.get((name, version))
        if entry is not None and entry[1].valid:
            return entry[0]
        creds = get_credentials()
        service = build(name, version, http=AuthorizedHttp(creds, http=_locked_http()), cache_discovery=False)
        _services[(name, version)] = (service, creds)
        return service
