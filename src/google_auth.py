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


# ---- Cached API clients -------------------------------------------------
# build() parses a large API description each time it is called. Doing that on
# every Sheets/Gmail/Drive call (dozens per page load) ballooned memory on
# Render's 512 MB free tier. Build each client once per thread instead
# (httplib2, which the clients use, is not thread-safe, so not shared across
# threads). The cached client is dropped and rebuilt if its credentials are no
# longer valid, so token refresh and GoogleAuthExpired behave as before.
_local = threading.local()


def get_service(name: str, version: str):
    from googleapiclient.discovery import build
    cache = getattr(_local, "services", None)
    if cache is None:
        cache = _local.services = {}
    entry = cache.get((name, version))
    if entry is not None and entry[1].valid:
        return entry[0]
    creds = get_credentials()
    service = build(name, version, credentials=creds, cache_discovery=False)
    cache[(name, version)] = (service, creds)
    return service
