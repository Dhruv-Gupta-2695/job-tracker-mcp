"""
Stateless email-to-applications logic for the multi-user portal. It ports the rules
the single-user tracker uses (classifier, status protection, de-duplication) but works
on plain dicts instead of a Google Sheet: the caller passes the user's existing
applications and gets back only the records that are new or changed.
"""
from __future__ import annotations

from datetime import datetime

from . import classifier

DEDUPE_WINDOW_DAYS = 14
PROTECTED_STATUSES = {"interview", "offer", "rejected"}
MAX_TEXT = 6000


def _gmail_link(thread_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{thread_id}"


def _days_apart(a: str, b: datetime) -> float | None:
    try:
        return abs((b - datetime.strptime(a, "%Y-%m-%d")).days)
    except (ValueError, TypeError):
        return None


def _find_by_company(apps: list[dict], hint: str) -> dict | None:
    h = hint.strip().lower()
    if len(h) < 3:
        return None
    by_company = by_desc = None
    for app in apps:
        company = (app.get("company") or "").strip().lower()
        if company and (h == company or h in company or (len(company) >= 4 and company in h)):
            by_company = app
        elif h in (app.get("description") or "").lower():
            by_desc = app
    return by_company or by_desc


def _apply_update(app: dict, status: str, summary: str, when: datetime) -> None:
    current = (app.get("status") or "").strip().lower()
    if status.strip().lower() == "update" and current in PROTECTED_STATUSES:
        status = current  # a vague later email never wipes out a real outcome
    app["status"] = status
    app["lastUpdate"] = when.strftime("%Y-%m-%d %H:%M")
    app["lastSummary"] = summary[:MAX_TEXT]


def _is_duplicate(apps: list[dict], company: str, position: str, sender: str, applied_at: datetime) -> bool:
    shared = classifier.is_shared_board_sender(sender)
    for app in apps:
        gap = _days_apart(app.get("appliedDate", ""), applied_at)
        if gap is None or gap > DEDUPE_WINDOW_DAYS:
            continue
        if shared:
            if (app.get("company", "").strip().lower() == company.strip().lower()
                    and app.get("position", "").strip().lower() == position.strip().lower()):
                return True
        elif app.get("sender") == sender:
            return True
    return False


def process_messages(messages: list, existing: list[dict], seen_ids: set[str], user_email: str = "") -> dict:
    """messages: gmail_client.EmailMessage objects. existing: the user's application dicts
    (keys: threadId, company, position, status, appliedDate, sender, description, ...).
    Returns {"upserts": [app dicts new or changed], "new": n, "updated": n, "seen": [message ids]}."""
    apps = [dict(a) for a in existing]
    by_thread = {a["threadId"]: a for a in apps if a.get("threadId")}
    changed: dict[str, dict] = {}
    new_count = 0
    own = (user_email or "").lower()
    seen_now: list[str] = []

    for msg in sorted(messages, key=lambda m: m.received_at):
        if msg.message_id in seen_ids:
            continue
        seen_now.append(msg.message_id)
        if own and own in (msg.sender or "").lower():
            continue  # the user's own outgoing mail

        app = by_thread.get(msg.thread_id)
        if app is not None:
            # A reply in an already-tracked thread. A repeat receipt is not an outcome.
            if classifier.classify_new_message(msg).is_application_confirmation:
                continue
            res = classifier.classify_update(msg)
            _apply_update(app, res.status, msg.body or msg.snippet, msg.received_at)
            changed[app["threadId"]] = app
            continue

        hint = None
        if classifier.is_status_update_email(msg):
            hint = classifier.company_hint_from_update(msg)
        elif classifier.is_stage_progress_email(msg):
            hint = classifier._guess_company(msg)
        if hint is not None:
            target = _find_by_company(apps, hint) if hint and hint != "Unknown" else None
            if target is not None:
                res = classifier.classify_update(msg)
                _apply_update(target, res.status, msg.body or msg.snippet, msg.received_at)
                changed[target["threadId"]] = target
            continue  # an outcome email with no matching application is dropped, never a new row

        res = classifier.classify_new_message(msg)
        if not res.is_application_confirmation:
            continue
        if _is_duplicate(apps, res.company, res.position, msg.sender, msg.received_at):
            continue
        record = {
            "threadId": msg.thread_id, "company": res.company, "position": res.position,
            "status": "applied", "appliedDate": msg.received_at.strftime("%Y-%m-%d"),
            "sender": msg.sender, "gmailLink": _gmail_link(msg.thread_id),
            "description": (msg.body or msg.snippet)[:MAX_TEXT], "lastUpdate": "", "lastSummary": "",
            "firstMessageId": msg.message_id,
        }
        apps.append(record)
        by_thread[msg.thread_id] = record
        changed[msg.thread_id] = record
        new_count += 1

    return {
        "upserts": list(changed.values()), "new": new_count,
        "updated": len(changed) - new_count, "seen": seen_now,
    }
