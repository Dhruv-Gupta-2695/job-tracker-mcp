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
                    summary=msg.snippet or msg.body,
                )
                telegram_sent = True
            except Exception as exc:  # noqa: BLE001 - surfaced to caller/log
                print(f"[telegram_notify] failed to send for {app['Company']}: {exc}")

            tracker.update_application(
                thread_id=thread_id,
                status=result.status,
                summary=msg.snippet or msg.body,
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


def run_once() -> dict:
    """Convenience entry point: does both steps, returns a summary."""
    new_apps = scan_new_applications()
    updates = check_thread_updates()
    return {"new_applications": new_apps, "updates": updates}
