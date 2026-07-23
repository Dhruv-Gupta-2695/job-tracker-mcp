"""
Thin wrapper around the Sheets API v4 -- generic get/append/update helpers
that tracker.py builds on. Kept separate from tracker.py so the "how to
talk to Google Sheets" concern stays isolated from "what our data model
looks like".
"""
from __future__ import annotations

from googleapiclient.discovery import build

from . import config, google_auth

APPLICATIONS_RANGE = "Applications!A:I"
STATE_RANGE = "_State!A:B"


def get_sheets_service():
    return build("sheets", "v4", credentials=google_auth.get_credentials())


def get_values(range_name: str) -> list[list[str]]:
    service = get_sheets_service()
    resp = service.spreadsheets().values().get(
        spreadsheetId=config.GOOGLE_SHEET_ID, range=range_name
    ).execute()
    return resp.get("values", [])


def append_row(range_name: str, row: list) -> None:
    service = get_sheets_service()
    service.spreadsheets().values().append(
        spreadsheetId=config.GOOGLE_SHEET_ID,
        range=range_name,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def update_row(sheet_name: str, row_number: int, row: list) -> None:
    """row_number is 1-indexed exactly as it appears in the sheet (so row 1
    is the header row on the Applications tab, but there is no header on
    the _State tab -- see tracker.py for how each range is used)."""
    service = get_sheets_service()
    end_col = chr(ord("A") + len(row) - 1)
    service.spreadsheets().values().update(
        spreadsheetId=config.GOOGLE_SHEET_ID,
        range=f"{sheet_name}!A{row_number}:{end_col}{row_number}",
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()
