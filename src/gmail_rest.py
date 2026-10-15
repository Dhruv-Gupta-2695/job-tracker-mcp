"""
Reads Gmail with a short-lived OAuth access token over plain REST (no stored credentials,
no heavy API client). Used by the multi-user portal: the token comes from the user's own
browser for one scan and is dropped afterwards.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

from .gmail_client import EmailMessage, _extract_body, _header

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_MESSAGES = 600


class GmailBusyError(RuntimeError):
    """Gmail is rate-limiting this connection; waiting a little and retrying works."""


class GmailTokenError(RuntimeError):
    """The access token was rejected (expired, revoked or missing the Gmail permission)."""


def _is_rate_limited(resp: requests.Response) -> bool:
    return resp.status_code == 429 or (resp.status_code == 403 and "ratelimit" in resp.text.lower().replace(" ", ""))


def _check(resp: requests.Response) -> None:
    if _is_rate_limited(resp):
        raise GmailBusyError("Gmail is asking us to slow down.")
    if resp.status_code in (401, 403):
        raise GmailTokenError("Google rejected the Gmail connection. Connect Gmail again.")
    resp.raise_for_status()


def _fetch_one(session: requests.Session, headers: dict, message_id: str) -> EmailMessage | None:
    import time
    resp = None
    for attempt in range(4):
        resp = session.get(f"{BASE}/messages/{message_id}", params={"format": "full"}, headers=headers, timeout=20)
        # Gmail answers 429 (too many requests) or a 403 "rateLimitExceeded" when asked too fast: wait and retry.
        throttled = _is_rate_limited(resp) or resp.status_code >= 500
        if not throttled:
            break
        time.sleep(1.5 * (attempt + 1))
    if resp.status_code == 404:
        return None
    _check(resp)
    msg = resp.json()
    hdrs = msg["payload"].get("headers", [])
    return EmailMessage(
        message_id=msg["id"], thread_id=msg["threadId"],
        sender=_header(hdrs, "From"), subject=_header(hdrs, "Subject"),
        snippet=msg.get("snippet", ""), body=_extract_body(msg["payload"]),
        received_at=datetime.fromtimestamp(int(msg.get("internalDate", "0")) / 1000),
    )


def list_message_ids(access_token: str, after: datetime, skip_ids: set[str] | None = None) -> list[str]:
    """Ids of messages received after `after`, oldest first (Gmail ids grow with time), minus skip_ids."""
    headers = {"Authorization": f"Bearer {access_token}"}
    skip_ids = skip_ids or set()
    ids: list[str] = []
    with requests.Session() as session:
        token = None
        while len(ids) < MAX_MESSAGES:
            params = {"q": f"after:{int(after.timestamp())}", "maxResults": 100}
            if token:
                params["pageToken"] = token
            resp = session.get(f"{BASE}/messages", params=params, headers=headers, timeout=20)
            _check(resp)
            data = resp.json()
            ids += [m["id"] for m in data.get("messages", []) if m["id"] not in skip_ids]
            token = data.get("nextPageToken")
            if not token:
                break
    return list(reversed(ids[:MAX_MESSAGES]))


def fetch_messages(access_token: str, ids: list[str]) -> list[EmailMessage]:
    headers = {"Authorization": f"Bearer {access_token}"}
    with requests.Session() as session, ThreadPoolExecutor(max_workers=4) as pool:
        fetched = list(pool.map(lambda i: _fetch_one(session, headers, i), ids))
    return [m for m in fetched if m is not None]


def fetch_messages_since(access_token: str, after: datetime, skip_ids: set[str] | None = None) -> list[EmailMessage]:
    headers = {"Authorization": f"Bearer {access_token}"}
    skip_ids = skip_ids or set()
    ids: list[str] = []
    with requests.Session() as session:
        token = None
        while len(ids) < MAX_MESSAGES:
            params = {"q": f"after:{int(after.timestamp())}", "maxResults": 100}
            if token:
                params["pageToken"] = token
            resp = session.get(f"{BASE}/messages", params=params, headers=headers, timeout=20)
            _check(resp)
            data = resp.json()
            ids += [m["id"] for m in data.get("messages", []) if m["id"] not in skip_ids]
            token = data.get("nextPageToken")
            if not token:
                break
        ids = ids[:MAX_MESSAGES]
        with ThreadPoolExecutor(max_workers=4) as pool:
            fetched = list(pool.map(lambda i: _fetch_one(session, headers, i), ids))
    return [m for m in fetched if m is not None]
