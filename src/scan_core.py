"""
Stateless email-to-applications logic for the multi-user portal. It ports the rules
the single-user tracker uses (classifier, status protection, de-duplication) but works
on plain dicts instead of a Google Sheet: the caller passes the user's existing
applications and gets back only the records that are new or changed.
"""
from __future__ import annotations

import re
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


_NO_REPLY = re.compile(r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer-daemon|postmaster|bounce|notifications?@|alerts?@|newsletter)", re.I)
_GENERIC_DOMAINS = {"gmail", "googlemail", "outlook", "hotmail", "yahoo", "icloud", "live", "proton", "protonmail", "rediffmail"}
_STOP = {"the", "and", "for", "with", "inc", "ltd", "llc", "pvt", "private", "limited", "technologies", "technology", "solutions", "india", "group", "corp", "company", "jobs", "careers", "hr", "team"}


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _domain_label(sender: str) -> str:
    """'hr@mail.jiohotstar.com' -> 'jiohotstar' (the employer part of the sender's domain)."""
    d = classifier._sender_domain(sender or "")
    parts = d.split(".")
    if len(parts) < 2:
        return ""
    # skip country-style second-level parts such as co.in / com.au
    core = parts[-3] if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "ac"} else parts[-2]
    return core


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in _STOP}


def _company_matches_sender(company: str, sender: str) -> bool:
    label = _domain_label(sender)
    if not label or label in _GENERIC_DOMAINS or len(label) < 3:
        return False
    c = _alnum(company)
    return bool(c) and (label == c or (len(c) >= 4 and (c in label or (len(label) >= 5 and label in c))))


def _match_by_sender_company(apps: list[dict], msg) -> tuple[dict | None, bool]:
    """A reply from an employer's own mail (a NEW thread, so the thread id does not link it).
    Returns (application, needs_review). With one open application at that company it is attached
    straight away; with several roles the best word match on the role wins and the row is flagged."""
    sender = msg.sender or ""
    if _NO_REPLY.search(sender) or classifier.is_shared_board_sender(sender):
        return None, False
    same = [a for a in apps if a.get("sender") == sender and sender]
    cands = same or [a for a in apps if _company_matches_sender(a.get("company", ""), sender)]
    if not cands:
        return None, False
    if len(cands) == 1:
        return cands[0], False
    text_words = _words(f"{msg.subject} {msg.snippet} {(msg.body or '')[:1500]}")
    def score(a):
        return (len(_words(a.get("position", "")) & text_words), a.get("appliedDate", ""))
    best = max(cands, key=score)
    return best, True


def _apply_update(app: dict, status: str, summary: str, when: datetime) -> None:
    current = (app.get("status") or "").strip().lower()
    if status.strip().lower() == "update" and current in PROTECTED_STATUSES:
        status = current  # a vague later email never wipes out a real outcome
    app["status"] = status
    app["lastUpdate"] = when.strftime("%Y-%m-%d %H:%M")
    app["lastSummary"] = summary[:MAX_TEXT]
    app["unseen"] = True   # the portal highlights rows with a reply you have not opened yet


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
            review = False
            if target is None:
                target, review = _match_by_sender_company(apps, msg)
            if target is not None:
                res = classifier.classify_update(msg)
                _apply_update(target, res.status, msg.body or msg.snippet, msg.received_at)
                if review:
                    target["matchNote"] = "Matched by company: this company has more than one role, check it is the right one."
                changed[target["threadId"]] = target
            continue  # an outcome email with no matching application is dropped, never a new row

        res = classifier.classify_new_message(msg)
        if not res.is_application_confirmation:
            # An HR reply from the employer's own address, in a new thread: link it to the
            # application it belongs to instead of dropping it.
            target, review = _match_by_sender_company(apps, msg)
            if target is not None:
                upd = classifier.classify_update(msg)
                _apply_update(target, upd.status, msg.body or msg.snippet, msg.received_at)
                target["matchNote"] = "Matched by company: this company has more than one role, check it is the right one." if review else ""
                changed[target["threadId"]] = target
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
