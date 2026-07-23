"""
The two operations everything else is built around:

  scan_new_applications() - look for brand-new "application received" emails
                             and add a row per company/position to the Excel
                             tracker.

  check_thread_updates()  - for every thread already in the tracker, look for
                             new replies (interview invites, rejections,
                             offers, anything) and if found: update the Excel
                             row + send a Telegram message with just that
                             update.

Both are safe to call repeatedly (idempotent): re-processing an email that
was already seen is a no-op thanks to state.json's seen_message_ids and the
Excel thread-ID de-dupe in tracker.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import classifier, config, gmail_client, telegram_notify, tracker


def scan_new_applications() -> list[dict]:
    """Returns a list of {"company", "position"} dicts for every new
    application confirmation found this run."""
    service = gmail_client.get_gmail_service()

    last_scan = tracker.get_last_scan_time()
    since = last_scan or (datetime.now() - timedelta(days=config.INITIAL_LOOKBACK_DAYS))
    seen_ids = tracker.get_last_seen_message_ids()

    messages = gmail_client.fetch_messages_since(service, since)
    new_ids = set()
    added = []

    for msg in messages:
        new_ids.add(msg.message_id)
        if msg.message_id in seen_ids:
            continue

        result = classifier.classify_new_message(msg)
        if result.is_application_confirmation:
            was_added = tracker.add_application(
                company=result.company,
                position=result.position,
                sender=msg.sender,
                thread_id=msg.thread_id,
                applied_at=msg.received_at,
                description=msg.body or msg.snippet,
            )
            if was_added:
                added.append({"company": result.company, "position": result.position})

    tracker.add_seen_message_ids(new_ids)
    tracker.set_last_scan_time(datetime.now())
    return added


def check_thread_updates() -> list[dict]:
    """Returns a list of {"company", "status", "telegram_sent"} dicts for
    every update found and (attempted to be) pushed to Telegram."""
    service = gmail_client.get_gmail_service()
    seen_ids = tracker.get_last_seen_message_ids()
    applications = {app["Thread ID"]: app for app in tracker.list_applications()}

    updates = []
    newly_seen = set()

    for thread_id, app in applications.items():
        thread_messages = gmail_client.fetch_thread_messages(service, thread_id)
        # Skip the very first message in the thread -- that's the
        # confirmation email we already recorded.
        for msg in thread_messages[1:]:
            newly_seen.add(msg.message_id)
            if msg.message_id in seen_ids:
                continue

            result = classifier.classify_update(msg)
            telegram_sent = False
            try:
                telegram_notify.send_telegram_update(
                    company=app["Company"],
                    position=app["Position"],
                    status=result.status,
                    summary=msg.body or msg.snippet,
                )
                telegram_sent = True
            except Exception as exc:  # noqa: BLE001 - surfaced to caller/log
                print(f"[telegram_notify] failed to send for {app['Company']}: {exc}")

            tracker.update_application(
                thread_id=thread_id,
                status=result.status,
                summary=msg.body or msg.snippet,
                updated_at=msg.received_at,
                telegram_sent=telegram_sent,
            )
            updates.append({
                "company": app["Company"],
                "status": result.status,
                "telegram_sent": telegram_sent,
            })

    tracker.add_seen_message_ids(newly_seen)
    return updates


def _followup_subject(position: str) -> str:
    return f"Following up on my application for {position}"


def _followup_body(company: str, position: str, applied_date: str) -> str:
    return (
        f"Hello,\n\n"
        f"I wanted to follow up on my application for the {position} role at {company}, "
        f"submitted on {applied_date}. I remain very interested in the opportunity and "
        f"would appreciate any update you're able to share on its status.\n\n"
        f"Thank you for your time and consideration.\n\n"
        f"Best regards"
    )


def check_stale_applications() -> list[dict]:
    """For anything that's sat at "applied" with zero reply for
    config.STALE_NUDGE_DAYS or more: draft a polite follow-up email in Gmail
    Drafts (never sent automatically -- you review and hit Send yourself)
    and send a Telegram nudge. No-ops entirely if STALE_NUDGE_DAYS is 0.

    Each application is only ever processed once (tracked via the "Stale
    Nudge Sent" column), and that flag gets set even if a step below fails --
    on purpose, since retrying draft creation on the next run would create a
    second duplicate draft, which is worse than an occasional missed nudge
    for a personal tool like this. Failures are printed so they show up in
    GitHub Actions / daemon logs."""
    if config.STALE_NUDGE_DAYS <= 0:
        return []

    gmail_service = None
    nudged = []
    for app in tracker.get_stale_applications(config.STALE_NUDGE_DAYS):
        applied_date = datetime.strptime(app["Applied Date"], "%Y-%m-%d")
        days_stale = (datetime.now() - applied_date).days

        draft_created = False
        try:
            if gmail_service is None:
                gmail_service = gmail_client.get_gmail_service()
            gmail_client.create_draft(
                gmail_service,
                to_address=gmail_client.extract_email_address(app["Sender"]),
                subject=_followup_subject(app["Position"]),
                body_text=_followup_body(app["Company"], app["Position"], app["Applied Date"]),
                thread_id=app["Thread ID"],
            )
            draft_created = True
        except Exception as exc:  # noqa: BLE001 - don't let one bad draft kill the run
            print(f"[gmail_client] failed to create follow-up draft for {app['Company']}: {exc}")

        try:
            telegram_notify.send_stale_nudge(
                company=app["Company"], position=app["Position"],
                days_stale=days_stale, draft_created=draft_created,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[telegram_notify] failed to send stale nudge for {app['Company']}: {exc}")

        tracker.mark_stale_nudge_sent(app["_row"])
        nudged.append({
            "company": app["Company"], "position": app["Position"],
            "days_stale": days_stale, "draft_created": draft_created,
        })
    return nudged


def run_once() -> dict:
    """Convenience entry point: does all three steps, returns a summary."""
    new_apps = scan_new_applications()
    updates = check_thread_updates()
    nudges = check_stale_applications()
    return {"new_applications": new_apps, "updates": updates, "stale_nudges": nudges}
