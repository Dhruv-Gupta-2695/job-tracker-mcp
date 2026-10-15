"""
Scheduled scan for the portal owner (run by GitHub Actions at the cron times, same as before).
It reads your Gmail with the existing token, finds new applications and status changes with the
same rules the portal uses (src/scan_core.py), saves them to your account in Firestore, and sends
the same Telegram messages as the old scan: one per status change, a summary every run, reminders
for applications that have sat at 'applied', and an alert if the Gmail login expired.

Usage: python3 run_scan_firestore.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from src import config, scan_core, telegram_notify
from src.firestore_store import FirestoreStore
from src.gmail_client import GmailAuthExpired

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("job-tracker-firestore-scan")


def _alert(message: str) -> None:
    try:
        telegram_notify.send_telegram_alert(message)
    except Exception:
        log.exception("Also failed to send the Telegram alert about the error below")


def stale_applications(apps: list[dict], days: int, today: datetime) -> list[dict]:
    """Applications still at 'applied' for `days` or more and not yet reminded about."""
    if days <= 0:
        return []
    out = []
    for a in apps:
        if (a.get("status") or "").lower() != "applied" or a.get("nudgeSent"):
            continue
        try:
            applied = datetime.strptime(a.get("appliedDate", ""), "%Y-%m-%d")
        except ValueError:
            continue
        if (today - applied).days >= days:
            out.append(a)
    return out


def run(store: FirestoreStore, messages: list, user_email: str, notify=telegram_notify, now: datetime | None = None) -> dict:
    """Core of one pass, separated from Gmail so it can be tested. Returns the summary lists."""
    now = now or datetime.now()
    meta = store.load_meta()
    existing = store.load_apps()
    known = {a["threadId"]: a for a in existing if a.get("threadId")}
    seen = set(meta["seenIds"])

    out = scan_core.process_messages(messages, existing, seen, user_email)
    new_apps, updates = [], []
    for a in out["upserts"]:
        if a["threadId"] not in known:
            new_apps.append({"company": a["company"], "position": a["position"]})
        if a["threadId"] in known or a["status"] != "applied":
            updates.append(a)  # a status change, including a reply that arrived in the same scan as the receipt

    update_summaries = []
    for a in updates:
        sent = False
        try:
            notify.send_telegram_update(company=a["company"], position=a["position"],
                                        status=a["status"], summary=a.get("lastSummary", ""))
            sent = True
        except Exception:
            log.exception("Telegram update failed for %s", a["company"])
        update_summaries.append({"company": a["company"], "status": a["status"], "telegram_sent": sent})

    # Reminders for applications that went quiet (no draft is created any more).
    current = {**known, **{a["threadId"]: a for a in out["upserts"]}}
    nudges = []
    for a in stale_applications(list(current.values()), config.STALE_NUDGE_DAYS, now):
        days = (now - datetime.strptime(a["appliedDate"], "%Y-%m-%d")).days
        try:
            notify.send_stale_nudge(a["company"], a["position"], days, False)
        except Exception:
            log.exception("Telegram reminder failed for %s", a["company"])
        a["nudgeSent"] = True  # set even if sending failed: never nag twice
        if a["threadId"] not in {u["threadId"] for u in out["upserts"]}:
            out["upserts"].append(a)
        nudges.append({"company": a["company"], "position": a["position"]})

    store.save_apps(out["upserts"])
    store.save_meta(datetime.now(timezone.utc).isoformat(), list(seen) + out["seen"])
    return {"new_applications": new_apps, "updates": update_summaries, "stale_nudges": nudges}


def main() -> None:
    from src import gmail_client
    store = FirestoreStore()
    try:
        service = gmail_client.get_gmail_service()
        profile = service.users().getProfile(userId="me").execute()
        meta = store.load_meta()
        if meta["lastScanAt"]:
            since = datetime.fromisoformat(meta["lastScanAt"].replace("Z", "+00:00"))
            since = datetime.fromtimestamp(since.timestamp()) - timedelta(hours=1)
        else:
            since = datetime.now() - timedelta(days=config.INITIAL_LOOKBACK_DAYS)
        messages = gmail_client.fetch_messages_since(service, since)
        result = run(store, messages, profile.get("emailAddress", ""))
        log.info("New: %s | Updates: %s | Reminders: %s", result["new_applications"], result["updates"], result["stale_nudges"])
        try:
            telegram_notify.send_scan_summary(result["new_applications"], result["updates"], result["stale_nudges"])
        except Exception:
            log.exception("Scan finished fine, but the Telegram summary failed to send")
    except GmailAuthExpired:
        log.exception("Gmail login expired")
        _alert("Your Gmail login expired. Run 'python3 run_once.py' on your Mac once to log back in; scheduled runs resume after that.")
        raise
    except Exception:
        log.exception("Error during scan")
        _alert("A scan failed with an unexpected error. Check the GitHub Actions log.")
        raise


if __name__ == "__main__":
    main()
