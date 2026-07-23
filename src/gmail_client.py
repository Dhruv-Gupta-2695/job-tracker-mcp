"""
Thin wrapper around the Gmail API: fetching/parsing messages. Auth itself
lives in google_auth.py and is shared with the Sheets client, since one
login now grants both gmail.readonly and spreadsheets access.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import datetime
from email.mime.text import MIMEText

from googleapiclient.discovery import build

from . import google_auth

# Kept as an alias so existing `from src.gmail_client import GmailAuthExpired`
# imports (run_once.py, run_daemon.py) keep working unchanged.
GmailAuthExpired = google_auth.GoogleAuthExpired


@dataclass
class EmailMessage:
    message_id: str
    thread_id: str
    sender: str
    subject: str
    snippet: str
    body: str
    received_at: datetime


def get_gmail_service():
    """Return an authenticated Gmail API service object. Raises
    GmailAuthExpired if the cached refresh token has expired/been revoked --
    callers running unattended (cron, GitHub Actions, the daemon) should
    catch this specifically rather than let the process hang trying to open
    a browser that isn't there."""
    return build("gmail", "v1", credentials=google_auth.get_credentials())


def _extract_body(payload: dict) -> str:
    """Pull plain-text (falling back to stripped HTML) out of a Gmail
    message payload, walking multipart trees as needed."""
    def decode(data: str) -> str:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="ignore")

    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return decode(payload["body"]["data"])

    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        html = decode(payload["body"]["data"])
        return re.sub("<[^<]+?>", " ", html)

    for part in payload.get("parts", []) or []:
        text = _extract_body(part)
        if text:
            return text
    return ""


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def thread_url(thread_id: str) -> str:
    """A direct Gmail web link that opens the whole thread (original
    confirmation email plus every reply) in the browser."""
    return f"https://mail.google.com/mail/u/0/#all/{thread_id}"


def extract_email_address(sender: str) -> str:
    """Pull just the email address out of a raw From header, e.g.
    '"Acme Recruiting" <jobs@acme.com>' -> 'jobs@acme.com'. Falls back to
    the raw string if no angle-bracket address is found."""
    match = re.search(r"<([^<>]+)>", sender)
    return match.group(1).strip() if match else sender.strip()


def create_draft(service, to_address: str, subject: str, body_text: str, thread_id: str = "") -> str:
    """Create a Gmail Draft (never sends anything -- the user has to open it
    and click Send themselves). Requires the gmail.compose scope. Returns
    the new draft's id."""
    message = MIMEText(body_text)
    message["to"] = to_address
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    body: dict = {"message": {"raw": raw}}
    if thread_id:
        body["message"]["threadId"] = thread_id

    draft = service.users().drafts().create(userId="me", body=body).execute()
    return draft["id"]


def fetch_messages_since(service, after: datetime, extra_query: str = "") -> list[EmailMessage]:
    """List + fetch every message received after `after`. `extra_query` lets
    callers narrow the Gmail search (e.g. restrict to a thread)."""
    query = f"after:{int(after.timestamp())} {extra_query}".strip()
    results = []
    page_token = None

    while True:
        resp = service.users().messages().list(
            userId="me", q=query, pageToken=page_token, maxResults=100
        ).execute()
        for meta in resp.get("messages", []):
            msg = service.users().messages().get(
                userId="me", id=meta["id"], format="full"
            ).execute()
            headers = msg["payload"].get("headers", [])
            received_ms = int(msg.get("internalDate", "0"))
            results.append(
                EmailMessage(
                    message_id=msg["id"],
                    thread_id=msg["threadId"],
                    sender=_header(headers, "From"),
                    subject=_header(headers, "Subject"),
                    snippet=msg.get("snippet", ""),
                    body=_extract_body(msg["payload"]),
                    received_at=datetime.fromtimestamp(received_ms / 1000),
                )
            )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return results


def fetch_thread_messages(service, thread_id: str) -> list[EmailMessage]:
    """Fetch every message in a given thread, oldest first."""
    thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    out = []
    for msg in thread.get("messages", []):
        headers = msg["payload"].get("headers", [])
        received_ms = int(msg.get("internalDate", "0"))
        out.append(
            EmailMessage(
                message_id=msg["id"],
                thread_id=msg["threadId"],
                sender=_header(headers, "From"),
                subject=_header(headers, "Subject"),
                snippet=msg.get("snippet", ""),
                body=_extract_body(msg["payload"]),
                received_at=datetime.fromtimestamp(received_ms / 1000),
            )
        )
    out.sort(key=lambda m: m.received_at)
    return out
