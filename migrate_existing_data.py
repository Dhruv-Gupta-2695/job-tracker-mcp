"""
One-time helper: copies rows from the old local job_applications.xlsx into
the new Google Sheet, so you don't lose the applications already tracked
before switching over. Safe to run more than once -- it goes through the
normal tracker.add_application() dedupe logic, so re-running just no-ops
on rows already present.

Usage: python3 migrate_existing_data.py
"""
from datetime import datetime

from openpyxl import load_workbook

from src import config, tracker

OLD_EXCEL_PATH = "job_applications.xlsx"


def main() -> None:
    wb = load_workbook(OLD_EXCEL_PATH)
    ws = wb["Applications"]
    migrated = 0

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(row):
            continue
        company, position, status, applied_date, sender, thread_id = row[0], row[1], row[2], row[3], row[4], row[5]
        if not thread_id:
            continue
        applied_at = datetime.strptime(applied_date, "%Y-%m-%d") if applied_date else datetime.now()
        tracker.add_application(company or "", position or "", sender or "", thread_id, applied_at)
        migrated += 1

    print(f"Migrated {migrated} row(s) into the Google Sheet.")
    print(f"View it at: {config.GOOGLE_SHEET_URL}")


if __name__ == "__main__":
    main()
