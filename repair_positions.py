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
        or re.search(r"\b(hiring|recruiting|recruitment|talent|careers?|hr)(\s+team)?$", comp)
        or re.search(r"\b(received|thank you)\b", pos)
        or "view job" in pos
        or any(p in pos for p in classifier.STAGE_PROGRESS_PHRASES)
        or (len(comp) > 3 and comp in pos)
    )


def _recompute_status(messages) -> str:
    """Strongest outcome among the real replies (receipts are ignored)."""
    best = "applied"
    rank = {"applied": 0, "update": 1, "interview": 2, "rejected": 3, "offer": 4}
    for msg in messages[1:]:
        if classifier.classify_new_message(msg).is_application_confirmation:
            continue
        status = classifier.classify_update(msg).status
        if rank.get(status, 0) > rank[best]:
            best = status
    return best


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
            # The row was created from a stage-progress email ("you're through
            # to the assignment stage"): keep the company, blank the pasted
            # subject, and record the progress as the status.
            if classifier.is_stage_progress_email(messages[0]):
                status_col = tracker.COLUMNS.index("Status")
                old_status = row[status_col]
                new_status = "interview" if old_status in ("applied", "update") else old_status
                new_position = classifier.UNKNOWN_POSITION
                if (company, new_position, new_status) != (company, position, old_status):
                    log.info("%s: %r / %r / %s  ->  %r / %r / %s", "FIX" if apply else "WOULD FIX",
                             company, position, old_status, company, new_position, new_status)
                    changed += 1
                    if apply:
                        fixed = list(row)
                        fixed[position_col], fixed[status_col] = new_position, new_status
                        sheets_client.update_row("Applications", i + 2, fixed)
                        time.sleep(pause)
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
        if (company.strip().lower() not in BOARD_NAMES and " via " not in company.lower()
                and new_company.strip().lower() == company.strip().lower()):
            new_company = company
        status_col = tracker.COLUMNS.index("Status")
        old_status = row[status_col]
        new_status = old_status
        # Only touch the status if it is "interview"/"update" that could have
        # come from receipt boilerplate; never downgrade offer/rejected.
        if old_status in ("interview", "update"):
            new_status = _recompute_status(messages)
        if (new_company, new_position, new_status) == (company, position, old_status):
            continue

        log.info("%s: %r / %r / %s  ->  %r / %r / %s", "FIX" if apply else "WOULD FIX",
                 company, position, old_status, new_company, new_position, new_status)
        changed += 1
        if apply:
            fixed = list(row)
            fixed[company_col], fixed[position_col] = new_company, new_position
            fixed[status_col] = new_status
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
