"""
Reads/writes the "Applications" tab of your Google Sheet, plus a hidden
"_State" tab that stores scan bookkeeping (replaces the old local
state.json -- needed because this now also runs on GitHub Actions, where
the filesystem is wiped clean between runs, so nothing local can persist).

Every public function here keeps the exact same name/signature it had when
this was backed by openpyxl, so automation.py and mcp_server.py did not
need to change at all for this migration.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from . import sheets_client

COLUMNS = [
    "Company", "Position", "Status", "Applied Date", "Sender",
    "Thread ID", "Last Update Date", "Last Update Summary", "Telegram Sent",
]

# If the same sender emails again within this many days but in a different
# thread (common for "please verify your identity" or portal-invite
# follow-ups that ATS systems send as a separate thread), treat it as the
# same application rather than logging a second row. Tune this if it
# merges two genuinely different applications to the same company, or
# splits one application because the follow-up came in later than this.
DEDUPE_WINDOW_DAYS = 14

# Keep the _State sheet's seen_message_ids cell comfortably under Google
# Sheets' 50,000-character-per-cell limit.
MAX_SEEN_IDS = 2000


def _get_rows() -> list[list[str]]:
    """All data rows from the Applications tab (header excluded), each
    padded out to len(COLUMNS) so index lookups never go out of range."""
    values = sheets_client.get_values(sheets_client.APPLICATIONS_RANGE)
    rows = values[1:] if values else []
    return [row + [""] * (len(COLUMNS) - len(row)) for row in rows]


def _find_row_by_thread_id(thread_id: str) -> tuple[Optional[int], Optional[list]]:
    """Returns (sheet_row_number, row_values) -- sheet_row_number accounts
    for the header row, so it's directly usable with sheets_client.update_row."""
    thread_col = COLUMNS.index("Thread ID")
    for i, row in enumerate(_get_rows()):
        if row[thread_col] == thread_id:
            return i + 2, row  # +2: 1-indexed rows, plus the header row
    return None, None


def _find_recent_row_by_sender(sender: str, applied_at: datetime) -> Optional[int]:
    """Find a row from the same sender within DEDUPE_WINDOW_DAYS, even if
    it's a different email thread -- catches follow-ups (identity checks,
    portal invites) that ATS systems send as a separate thread rather than
    a reply, which would otherwise show up as a duplicate application."""
    sender_col = COLUMNS.index("Sender")
    date_col = COLUMNS.index("Applied Date")
    for i, row in enumerate(_get_rows()):
        if row[sender_col] != sender or not row[date_col]:
            continue
        try:
            existing_date = datetime.strptime(row[date_col], "%Y-%m-%d")
        except ValueError:
            continue
        if abs((applied_at - existing_date).days) <= DEDUPE_WINDOW_DAYS:
            return i + 2
    return None


def add_application(company: str, position: str, sender: str, thread_id: str, applied_at: datetime) -> bool:
    """Add a new row for a freshly detected application confirmation email.
    No-ops if the thread is already tracked, or if the same sender already
    has a row within DEDUPE_WINDOW_DAYS (see note above). Returns True if a
    row was actually added, False if it was skipped as a duplicate --
    callers use this to report accurate counts rather than counting every
    detected confirmation as "added" even when it was deduped away."""
    row_num, _ = _find_row_by_thread_id(thread_id)
    if row_num is not None:
        return False
    if _find_recent_row_by_sender(sender, applied_at) is not None:
        return False

    sheets_client.append_row(sheets_client.APPLICATIONS_RANGE, [
        company, position, "applied", applied_at.strftime("%Y-%m-%d"),
        sender, thread_id, "", "", "No",
    ])
    return True


def update_application(thread_id: str, status: str, summary: str, updated_at: datetime, telegram_sent: bool) -> None:
    """Update the row for an existing thread with a new status/summary."""
    row_num, row = _find_row_by_thread_id(thread_id)
    if row_num is None:
        return

    row[COLUMNS.index("Status")] = status
    row[COLUMNS.index("Last Update Date")] = updated_at.strftime("%Y-%m-%d %H:%M")
    row[COLUMNS.index("Last Update Summary")] = summary[:500]
    row[COLUMNS.index("Telegram Sent")] = "Yes" if telegram_sent else "No"
    sheets_client.update_row("Applications", row_num, row)


def get_tracked_thread_ids() -> set[str]:
    thread_col = COLUMNS.index("Thread ID")
    return {row[thread_col] for row in _get_rows() if row[thread_col]}


def list_applications() -> list[dict]:
    return [dict(zip(COLUMNS, row)) for row in _get_rows() if any(row)]


# --- _State tab: replaces the old local state.json ---

def _load_state() -> dict:
    values = sheets_client.get_values(sheets_client.STATE_RANGE)
    return {row[0]: row[1] for row in values if len(row) >= 2}


def _save_state_value(key: str, value: str) -> None:
    values = sheets_client.get_values(sheets_client.STATE_RANGE)
    for i, row in enumerate(values):
        if row and row[0] == key:
            sheets_client.update_row("_State", i + 1, [key, value])
            return
    sheets_client.append_row(sheets_client.STATE_RANGE, [key, value])


def get_last_scan_time() -> Optional[datetime]:
    ts = _load_state().get("last_scan_time")
    return datetime.fromisoformat(ts) if ts else None


def set_last_scan_time(dt: datetime) -> None:
    _save_state_value("last_scan_time", dt.isoformat())


def get_last_seen_message_ids() -> set[str]:
    raw = _load_state().get("seen_message_ids")
    return set(json.loads(raw)) if raw else set()


def add_seen_message_ids(ids: set[str]) -> None:
    seen = get_last_seen_message_ids()
    seen.update(ids)
    _save_state_value("seen_message_ids", json.dumps(list(seen)[-MAX_SEEN_IDS:]))
