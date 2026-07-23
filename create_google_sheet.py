"""
One-time setup: creates a new Google Sheet with the two tabs this project
needs -- "Applications" (with headers, the one you'll actually look at)
and a hidden "_State" tab (internal bookkeeping, replaces the old local
state.json) -- and prints the resulting spreadsheet ID + URL.

Run this once locally (needs a browser for the Google login). Then put the
printed ID into .env as GOOGLE_SHEET_ID, and later into your GitHub repo
secret of the same name.

Usage: python3 create_google_sheet.py
"""
from googleapiclient.discovery import build

from src import google_auth
from src.tracker import COLUMNS


def main() -> None:
    creds = google_auth.get_credentials()
    service = build("sheets", "v4", credentials=creds)

    spreadsheet = service.spreadsheets().create(body={
        "properties": {"title": "Job Application Tracker"},
        "sheets": [
            {"properties": {"title": "Applications"}},
            {"properties": {"title": "_State"}},
        ],
    }).execute()

    sheet_id = spreadsheet["spreadsheetId"]
    url = spreadsheet["spreadsheetUrl"]

    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range="Applications!A1",
        valueInputOption="RAW",
        body={"values": [COLUMNS]},
    ).execute()

    # Bold the header row for readability.
    applications_gid = spreadsheet["sheets"][0]["properties"]["sheetId"]
    service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [{
            "repeatCell": {
                "range": {"sheetId": applications_gid, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        }]},
    ).execute()

    print(f"Created spreadsheet: {url}")
    print(f"Spreadsheet ID: {sheet_id}")
    print()
    print("Add this to your .env:")
    print(f"GOOGLE_SHEET_ID={sheet_id}")


if __name__ == "__main__":
    main()
