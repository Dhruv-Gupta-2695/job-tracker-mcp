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

from . import gmail_client, sheets_client

COLUMNS = [
    "Company", "Position", "Status", "Applied Date", "Sender",
    "Thread ID", "Gmail Link", "Description", "Last Update Date",
    "Last Update Summary", "Telegram Sent", "Stale Nudge Sent",
]

# Derived from COLUMNS rather than hardcoded, so adding/removing a column
# here can never again silently desync from the range actually read/written
# (this exact class of bug -- a stale "A:I" range left over after adding
# two more columns -- broke a live scan once already).
APPLICATIONS_RANGE = f"Applications!A:{chr(ord('A') + len(COLUMNS) - 1)}"

# Google Sheets caps cells at 50,000 characters -- stay comfortably under
# that for full email bodies rather than truncating to a short snippet.
MAX_CELL_CHARS = 45000

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
    values = sheets_client.get_values(APPLICATIONS_RANGE)
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


def add_application(
    company: str, position: str, sender: str, thread_id: str, applied_at: datetime,
    description: str = "",
) -> bool:
    """Add a new row for a freshly detected application confirmation email.
    No-ops if the thread is already tracked, or if the same sender already
    has a row within DEDUPE_WINDOW_DAYS (see note above). Returns True if a
    row was actually added, False if it was skipped as a duplicate --
    callers use this to report accurate counts rather than counting every
    detected confirmation as "added" even when it was deduped away.

    `description` is the full body of the confirmation email -- a "Gmail
    Link" back to the whole thread is derived from thread_id automatically,
    so you can always open the original email (and every reply) directly."""
    row_num, _ = _find_row_by_thread_id(thread_id)
    if row_num is not None:
        return False
    if _find_recent_row_by_sender(sender, applied_at) is not None:
        return False

    sheets_client.append_row(APPLICATIONS_RANGE, [
        company, position, "applied", applied_at.strftime("%Y-%m-%d"),
        sender, thread_id, gmail_client.thread_url(thread_id),
        description[:MAX_CELL_CHARS], "", "", "No", "No",
    ])
    return True


def update_application(
    thread_id: str, status: str, summary: str, updated_at: datetime, telegram_sent: bool,
) -> None:
    """Update the row for an existing thread with a new status and the full
    body of whatever reply triggered it (not just a short snippet)."""
    row_num, row = _find_row_by_thread_id(thread_id)
    if row_num is None:
        return

    row[COLUMNS.index("Status")] = status
    row[COLUMNS.index("Last Update Date")] = updated_at.strftime("%Y-%m-%d %H:%M")
    row[COLUMNS.index("Last Update Summary")] = summary[:MAX_CELL_CHARS]
    row[COLUMNS.index("Telegram Sent")] = "Yes" if telegram_sent else "No"
    sheets_client.update_row("Applications", row_num, row)


def get_stale_applications(threshold_days: int) -> list[dict]:
    """Applications still sitting at "applied" (i.e. no reply of any kind
    yet) whose Applied Date is threshold_days or older, and that haven't
    already had a nudge sent. Each dict includes "_row" (1-indexed sheet
    row, for mark_stale_nudge_sent) alongside the usual COLUMNS fields."""
    status_col = COLUMNS.index("Status")
    date_col = COLUMNS.index("Applied Date")
    nudge_col = COLUMNS.index("Stale Nudge Sent")
    now = datetime.now()

    stale = []
    for i, row in enumerate(_get_rows()):
        if row[status_col].strip().lower() != "applied":
            continue
        if row[nudge_col].strip().lower() == "yes":
            continue
        if not row[date_col]:
            continue
        try:
            applied_date = datetime.strptime(row[date_col], "%Y-%m-%d")
        except ValueError:
            continue
        if (now - applied_date).days >= threshold_days:
            entry = dict(zip(COLUMNS, row))
            entry["_row"] = i + 2
            stale.append(entry)
    return stale


def mark_stale_nudge_sent(row_num: int) -> None:
    """Flip "Stale Nudge Sent" to Yes for one row (by 1-indexed sheet row,
    as returned in get_stale_applications()'s "_row" field) so the same
    application doesn't get nudged again on the next scan."""
    values = sheets_client.get_values(f"Applications!A{row_num}:{chr(ord('A') + len(COLUMNS) - 1)}{row_num}")
    if not values:
        return
    row = values[0] + [""] * (len(COLUMNS) - len(values[0]))
    row[COLUMNS.index("Stale Nudge Sent")] = "Yes"
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
    # RAW input mode: without this, Sheets "helpfully" auto-detects
    # date-shaped strings and reformats them (e.g. dropping zero-padding,
    # swapping the separator), which then fails to parse back on the next
    # run. RAW stores exactly the string given, no reinterpretation.
    values = sheets_client.get_values(sheets_client.STATE_RANGE)
    for i, row in enumerate(values):
        if row and row[0] == key:
            sheets_client.update_row("_State", i + 1, [key, value], value_input_option="RAW")
            return
    sheets_client.append_row(sheets_client.STATE_RANGE, [key, value], value_input_option="RAW")


def get_last_scan_time() -> Optional[datetime]:
    """Stored as a plain Unix timestamp rather than an ISO string -- a
    second layer of defense against Sheets reformatting it, on top of the
    RAW write above. Any unexpected/corrupted value falls back to None
    (triggering a full INITIAL_LOOKBACK_DAYS rescan) rather than crashing
    the whole scan."""
    ts = _load_state().get("last_scan_time")
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts))
    except (ValueError, TypeError):
        return None


def set_last_scan_time(dt: datetime) -> None:
    _save_state_value("last_scan_time", str(dt.timestamp()))


def get_last_seen_message_ids() -> set[str]:
    raw = _load_state().get("seen_message_ids")
    if not raw:
        return set()
    try:
        return set(json.loads(raw))
    except (ValueError, TypeError):
        return set()


def add_seen_message_ids(ids: set[str]) -> None:
    seen = get_last_seen_message_ids()
    seen.update(ids)
    _save_state_value("seen_message_ids", json.dumps(list(seen)[-MAX_SEEN_IDS:]))
