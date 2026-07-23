"""
One-time (but safe to re-run) upgrade pass over your live Google Sheet:

1. Schema migration -- if the Applications tab still has the old layout
   (before "Gmail Link" and "Description" columns existed), inserts them in
   the right place, then backfills every existing row: "Gmail Link" is
   derived instantly from the Thread ID already in the sheet, and
   "Description" is backfilled by re-fetching each thread's original
   message from Gmail (one API call per row -- fine for a one-time pass).

2. Polish -- freezes the header row, color-codes each row by Status
   (applied/interview/offer/rejected/update) via conditional formatting,
   and adds a dropdown on the Status column so manual edits stay
   consistent with what the classifier uses.

3. Dashboard -- adds a "Dashboard" tab (skipped if it already exists) with
   live status counts and a pie chart, pulled from the Applications data.

Usage: python3 upgrade_sheet.py
"""
from googleapiclient.discovery import build

from src import config, gmail_client, google_auth
from src.tracker import COLUMNS

STATUS_OPTIONS = ["applied", "interview", "offer", "rejected", "update"]
STATUS_COLORS = {
    "applied": {"red": 0.93, "green": 0.93, "blue": 0.93},   # light gray
    "interview": {"red": 1.0, "green": 0.95, "blue": 0.70},  # light yellow
    "offer": {"red": 0.78, "green": 0.94, "blue": 0.78},     # light green
    "rejected": {"red": 0.98, "green": 0.80, "blue": 0.80},  # light red
    "update": {"red": 0.80, "green": 0.88, "blue": 0.98},    # light blue
}
DASHBOARD_SHEET_ID = 999001  # fixed custom id so we can reference it within one batchUpdate


def migrate_schema(sheets, sheet_id, applications_gid, header_row):
    """Insert Gmail Link + Description columns if they aren't there yet,
    then backfill every existing row."""
    if "Gmail Link" in header_row:
        print("Schema already up to date -- skipping migration.")
        return

    print("Old schema detected -- inserting Gmail Link + Description columns...")
    thread_col_idx = 5  # column F, 0-indexed -- unchanged position in both schemas

    sheets.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": [{
        "insertDimension": {
            "range": {
                "sheetId": applications_gid, "dimension": "COLUMNS",
                "startIndex": thread_col_idx + 1, "endIndex": thread_col_idx + 3,
            },
            "inheritFromBefore": False,
        }
    }]}).execute()

    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id, range="Applications!G1:H1", valueInputOption="RAW",
        body={"values": [["Gmail Link", "Description"]]},
    ).execute()

    rows = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range="Applications!A:K"
    ).execute().get("values", [])[1:]

    if not rows:
        print("No existing rows to backfill.")
        return

    gmail_service = gmail_client.get_gmail_service()
    links, descriptions = [], []
    for row in rows:
        thread_id = row[thread_col_idx] if len(row) > thread_col_idx else ""
        links.append([gmail_client.thread_url(thread_id) if thread_id else ""])
        description = ""
        if thread_id:
            try:
                messages = gmail_client.fetch_thread_messages(gmail_service, thread_id)
                if messages:
                    description = (messages[0].body or messages[0].snippet)[:45000]
            except Exception as exc:  # noqa: BLE001
                print(f"  Could not backfill description for thread {thread_id}: {exc}")
        descriptions.append([description])

    last_row = len(rows) + 1
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id, range=f"Applications!G2:G{last_row}",
        valueInputOption="RAW", body={"values": links},
    ).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id, range=f"Applications!H2:H{last_row}",
        valueInputOption="RAW", body={"values": descriptions},
    ).execute()
    print(f"Backfilled Gmail Link + Description for {len(rows)} existing row(s).")


def polish_applications_tab(sheets, sheet_id, applications_gid, existing_sheets):
    requests = [{
        "updateSheetProperties": {
            "properties": {"sheetId": applications_gid, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount",
        }
    }]

    status_col = COLUMNS.index("Status")
    requests.append({
        "setDataValidation": {
            "range": {
                "sheetId": applications_gid, "startRowIndex": 1,
                "startColumnIndex": status_col, "endColumnIndex": status_col + 1,
            },
            "rule": {
                "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": s} for s in STATUS_OPTIONS]},
                "showCustomUi": True,
                "strict": False,
            },
        }
    })

    # Clear old conditional format rules on this sheet before adding fresh
    # ones, so re-running this script doesn't pile up duplicates.
    for sheet in existing_sheets:
        if sheet["properties"]["sheetId"] == applications_gid:
            for _ in range(len(sheet.get("conditionalFormats", []))):
                requests.append({"deleteConditionalFormatRule": {"sheetId": applications_gid, "index": 0}})

    status_col_letter = chr(ord("A") + status_col)
    for status, color in STATUS_COLORS.items():
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": applications_gid, "startRowIndex": 1,
                        "startColumnIndex": 0, "endColumnIndex": len(COLUMNS),
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{"userEnteredValue": f'=${status_col_letter}2="{status}"'}],
                        },
                        "format": {"backgroundColor": color},
                    },
                },
                "index": 0,
            }
        })

    sheets.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": requests}).execute()
    print("Applications tab polished: header frozen, Status dropdown added, rows color-coded.")


def add_dashboard(sheets, sheet_id, sheets_by_title):
    if "Dashboard" in sheets_by_title:
        print("Dashboard tab already exists -- refreshing its formulas.")
        dashboard_gid = sheets_by_title["Dashboard"]
    else:
        resp = sheets.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": [{
            "addSheet": {"properties": {"sheetId": DASHBOARD_SHEET_ID, "title": "Dashboard", "index": 0}}
        }]}).execute()
        dashboard_gid = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
        print("Created Dashboard tab.")

    status_col_letter = chr(ord("A") + COLUMNS.index("Status"))
    values = [
        ["Job Application Dashboard"],
        [""],
        ["Status", "Count"],
        ["applied", f'=COUNTIF(Applications!{status_col_letter}:{status_col_letter},"applied")'],
        ["interview", f'=COUNTIF(Applications!{status_col_letter}:{status_col_letter},"interview")'],
        ["offer", f'=COUNTIF(Applications!{status_col_letter}:{status_col_letter},"offer")'],
        ["rejected", f'=COUNTIF(Applications!{status_col_letter}:{status_col_letter},"rejected")'],
        ["update", f'=COUNTIF(Applications!{status_col_letter}:{status_col_letter},"update")'],
        [""],
        ["Total applications", "=COUNTA(Applications!A2:A)"],
        ["Last scan", '=IFERROR(VLOOKUP("last_scan_time",_State!A:B,2,0),"never")'],
    ]
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id, range="Dashboard!A1", valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()

    sheets.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": [
        {"repeatCell": {
            "range": {"sheetId": dashboard_gid, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 14}}},
            "fields": "userEnteredFormat.textFormat",
        }},
        {"repeatCell": {
            "range": {"sheetId": dashboard_gid, "startRowIndex": 2, "endRowIndex": 3},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat",
        }},
        {"addChart": {
            "chart": {
                "spec": {
                    "title": "Applications by status",
                    "pieChart": {
                        "legendPosition": "RIGHT_LEGEND",
                        "domain": {"sourceRange": {"sources": [{
                            "sheetId": dashboard_gid, "startRowIndex": 2, "endRowIndex": 8,
                            "startColumnIndex": 0, "endColumnIndex": 1,
                        }]}},
                        "series": {"sourceRange": {"sources": [{
                            "sheetId": dashboard_gid, "startRowIndex": 2, "endRowIndex": 8,
                            "startColumnIndex": 1, "endColumnIndex": 2,
                        }]}},
                    },
                },
                "position": {"overlayPosition": {
                    "anchorCell": {"sheetId": dashboard_gid, "rowIndex": 11, "columnIndex": 0},
                }},
            }
        }},
    ]}).execute()
    print("Dashboard tab populated with status counts and a pie chart.")


def main() -> None:
    creds = google_auth.get_credentials()
    sheets = build("sheets", "v4", credentials=creds)
    sheet_id = config.GOOGLE_SHEET_ID

    spreadsheet = sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheets_by_title = {s["properties"]["title"]: s["properties"]["sheetId"] for s in spreadsheet["sheets"]}
    applications_gid = sheets_by_title["Applications"]

    header_row = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range="Applications!1:1"
    ).execute().get("values", [[]])[0]

    migrate_schema(sheets, sheet_id, applications_gid, header_row)
    polish_applications_tab(sheets, sheet_id, applications_gid, spreadsheet["sheets"])
    add_dashboard(sheets, sheet_id, sheets_by_title)

    print(f"\nDone. View it at: {config.GOOGLE_SHEET_URL}")


if __name__ == "__main__":
    main()
