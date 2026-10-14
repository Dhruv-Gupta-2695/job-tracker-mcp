"""
Thin wrapper around the Sheets API v4 -- generic get/append/update helpers
that tracker.py builds on. Kept separate from tracker.py so the "how to
talk to Google Sheets" concern stays isolated from "what our data model
looks like".
"""
from __future__ import annotations

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import config, google_auth

STATE_RANGE = "_State!A:B"


def get_sheets_service():
    return google_auth.get_service("sheets", "v4")


# Hidden helper tabs the webapp needs. If one is missing (for example
# upgrade_sheet.py was never run on this Sheet) it is created on first use,
# with the same header/seed rows upgrade_sheet.py would have written.
def _seed_rows(title: str):
    if title == "_Prompts":
        from .prompts import DEFAULT_PROMPTS
        return [["Prompt Name", "Template"]] + [[n, t] for n, t in DEFAULT_PROMPTS.items()]
    if title == "_CVVersions":
        from .cv_versions import HEADER
        return [HEADER]
    return None


def _tab_of(range_or_sheet: str) -> str:
    return range_or_sheet.split("!")[0].strip("'")


def _is_missing_tab(exc: Exception, title: str) -> bool:
    return isinstance(exc, HttpError) and "unable to parse range" in str(exc).lower() and _seed_rows(title) is not None


def ensure_tab(title: str) -> None:
    """Create a known hidden helper tab (and its header/seed rows) if absent."""
    rows = _seed_rows(title)
    if rows is None:
        raise ValueError(f"Don't know how to create a tab called {title!r}")
    service = get_sheets_service()
    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=config.GOOGLE_SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": title, "hidden": True}}}]},
        ).execute()
    except HttpError as exc:
        if "already exists" not in str(exc).lower():
            raise
        return  # someone created it just now; leave its contents alone
    service.spreadsheets().values().update(
        spreadsheetId=config.GOOGLE_SHEET_ID, range=f"{title}!A1", valueInputOption="RAW",
        body={"values": rows},
    ).execute()
    print(f"[sheets] created missing hidden tab {title}", flush=True)


def get_values(range_name: str) -> list[list[str]]:
    service = get_sheets_service()
    try:
        resp = service.spreadsheets().values().get(
            spreadsheetId=config.GOOGLE_SHEET_ID, range=range_name
        ).execute()
    except HttpError as exc:
        title = _tab_of(range_name)
        if not _is_missing_tab(exc, title):
            raise
        ensure_tab(title)
        resp = service.spreadsheets().values().get(
            spreadsheetId=config.GOOGLE_SHEET_ID, range=range_name
        ).execute()
    return resp.get("values", [])


def append_row(range_name: str, row: list, value_input_option: str = "USER_ENTERED") -> None:
    """value_input_option="RAW" stores exactly the string given, with no
    date/number auto-detection -- required for anything you need to parse
    back byte-exact later (e.g. tracker.py's _State bookkeeping). Leave the
    default USER_ENTERED for human-facing data like the Applications tab,
    where e.g. auto-formatting the Gmail Link as a clickable link is a
    feature, not a bug."""
    service = get_sheets_service()

    def do_append():
        service.spreadsheets().values().append(
            spreadsheetId=config.GOOGLE_SHEET_ID,
            range=range_name,
            valueInputOption=value_input_option,
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()

    try:
        do_append()
    except HttpError as exc:
        title = _tab_of(range_name)
        if not _is_missing_tab(exc, title):
            raise
        ensure_tab(title)
        do_append()


def update_row(sheet_name: str, row_number: int, row: list, value_input_option: str = "USER_ENTERED") -> None:
    """row_number is 1-indexed exactly as it appears in the sheet (so row 1
    is the header row on the Applications tab, but there is no header on
    the _State tab -- see tracker.py for how each range is used)."""
    service = get_sheets_service()
    end_col = chr(ord("A") + len(row) - 1)
    service.spreadsheets().values().update(
        spreadsheetId=config.GOOGLE_SHEET_ID,
        range=f"{sheet_name}!A{row_number}:{end_col}{row_number}",
        valueInputOption=value_input_option,
        body={"values": [row]},
    ).execute()
