"""
One-off repair: fix Company / Position cells that an older version of the
classifier filled with junk (e.g. "Dhruv, your application was sent to
PeopleGene", "Unknown position", or a job board's name as the company).

For each such row it re-reads the ORIGINAL email (via the Thread ID stored in
the row), runs the current classifier on it, and updates the row only when
the result is better. Nothing else in the row is touched.

Usage:
    python3 repair_positions.py            # dry run: only prints what would change
    python3 repair_positions.py --apply    # actually write the fixes
"""
import logging
import re
import sys
import time

from src import classifier, gmail_client, sheets_client, tracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("job-tracker-repair")

BOARD_NAMES = {"wellfound", "linkedin", "indeed", "glassdoor", "naukri"}


def _row_needs_repair(company: str, position: str) -> bool:
    pos = position.strip().lower()
    comp = company.strip().lower()
    return (
        pos == classifier.UNKNOWN_POSITION.lower()
        or "application was sent to" in pos
        or "was accepted" in pos
        or "successfully submitted" in pos
        or comp in BOARD_NAMES
        or " via " in comp
    )


def repair(service, apply: bool, pause: float = 1.1) -> dict:
    company_col = tracker.COLUMNS.index("Company")
    position_col = tracker.COLUMNS.index("Position")
    thread_col = tracker.COLUMNS.index("Thread ID")

    checked = changed = 0
    for i, row in enumerate(tracker._get_rows()):
        company, position, thread_id = row[company_col], row[position_col], row[thread_col]
        if not thread_id or not _row_needs_repair(company, position):
            continue
        checked += 1
        try:
            messages = gmail_client.fetch_thread_messages(service, thread_id)
        except Exception as exc:  # noqa: BLE001 - one bad thread must not stop the run
            log.warning("Could not read thread for %s: %s", company, exc)
            continue
        if not messages:
            continue

        result = classifier.classify_new_message(messages[0])
        if not result.is_application_confirmation:
            continue

        new_company = result.company or company
        new_position = result.position or position
        # Only ever replace a bad value with a better one, never with another
        # placeholder, and never touch a value that was already fine.
        if new_position == classifier.UNKNOWN_POSITION:
            # Nothing better could be read from the email. Keep a value that
            # isn't junk, but replace pasted subject text with the clean placeholder.
            junk = re.search(r"application was sent to|was accepted|successfully submitted", position, re.IGNORECASE)
            new_position = classifier.UNKNOWN_POSITION if junk else position
        if company.strip().lower() not in BOARD_NAMES and " via " not in company.lower():
            new_company = company
        if (new_company, new_position) == (company, position):
            continue

        log.info("%s: %r / %r  ->  %r / %r", "FIX" if apply else "WOULD FIX",
                 company, position, new_company, new_position)
        changed += 1
        if apply:
            fixed = list(row)
            fixed[company_col], fixed[position_col] = new_company, new_position
            sheets_client.update_row("Applications", i + 2, fixed)
            time.sleep(pause)  # stay under Sheets' 60 writes/minute limit
    tracker._invalidate_rows_cache()
    return {"checked": checked, "changed": changed}


def main() -> None:
    apply = "--apply" in sys.argv
    service = gmail_client.get_gmail_service()
    summary = repair(service, apply)
    log.info("%s: looked at %d row(s) with bad values, %d %s.",
             "Done" if apply else "Dry run finished", summary["checked"], summary["changed"],
             "fixed" if apply else "would change (re-run with --apply to write them)")


if __name__ == "__main__":
    main()
