"""
One-time import of your Google Sheet applications into your account in the new portal (Firestore),
so nothing is lost when the scheduled scan switches over. It also copies the Sheet's scan state
(last scan time and processed message ids) so the first Firestore scan does not re-read or re-announce old mail.

Safe to repeat: applications are written by thread id, so running it again only refreshes them.

Usage (run on your Mac, in the project folder, with your usual .env / token.json):
  export FIREBASE_OWNER_UID=...                       # your Firebase user id
  export FIREBASE_SERVICE_ACCOUNT_FILE=/path/key.json # the downloaded service-account key
  python3 import_sheet_to_firestore.py            # preview only
  python3 import_sheet_to_firestore.py --apply    # write to Firestore
"""
from __future__ import annotations

import sys
from datetime import timezone

from src import tracker


def to_record(row: dict) -> dict | None:
    thread_id = (row.get("Thread ID") or "").strip()
    if not thread_id:
        return None
    return {
        "threadId": thread_id,
        "company": row.get("Company", ""),
        "position": row.get("Position", ""),
        "status": (row.get("Status") or "applied").strip().lower() or "applied",
        "appliedDate": row.get("Applied Date", ""),
        "sender": row.get("Sender", ""),
        "gmailLink": row.get("Gmail Link", "") or f"https://mail.google.com/mail/u/0/#all/{thread_id}",
        "description": (row.get("Description") or "")[:6000],
        "lastUpdate": row.get("Last Update Date", ""),
        "lastSummary": (row.get("Last Update Summary") or "")[:6000],
        "nudgeSent": (row.get("Stale Nudge Sent") or "").strip().lower() == "yes",
        "firstMessageId": "",
    }


def main() -> None:
    apply = "--apply" in sys.argv
    records = [r for r in (to_record(a) for a in tracker.list_applications()) if r]
    seen_ids = sorted(tracker.get_last_seen_message_ids())
    last_scan = tracker.get_last_scan_time()
    print(f"Sheet applications found: {len(records)}")
    print(f"Processed message ids to copy: {len(seen_ids)}")
    print(f"Last scan time in the Sheet: {last_scan}")
    by_status: dict[str, int] = {}
    for r in records:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print("By status:", by_status)
    if not apply:
        print("\nPreview only. Run again with --apply to write these to Firestore.")
        return

    from src.firestore_store import FirestoreStore
    store = FirestoreStore()
    store.save_apps(records)
    when = last_scan.astimezone(timezone.utc) if last_scan else None  # naive times are this computer's local time
    meta = store.load_meta()
    store.save_meta(when.isoformat() if when else meta["lastScanAt"], list(meta["seenIds"]) + seen_ids)
    print(f"\nImported {len(records)} applications and {len(seen_ids)} processed ids into Firestore.")


if __name__ == "__main__":
    main()
