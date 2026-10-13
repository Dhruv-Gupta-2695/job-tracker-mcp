"""
One-off recovery: re-read the last N days of Gmail and record anything the
normal scan missed or mis-filed (for example Wellfound/LinkedIn applications
that an older version dropped), without creating duplicates.

Safe to run repeatedly: applications are de-duplicated by thread and by
company + position, and outcome emails ("An update from X") just re-apply
the same status. No Telegram messages are sent for recovered items.

Usage (from the project folder, with your .env / token.json in place):
    python3 backfill.py        # last 14 days
    python3 backfill.py 30     # last 30 days
"""
import logging
import sys

from src import automation

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("job-tracker-backfill")


def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    log.info("Backfilling the last %d days...", days)
    result = automation.run_once(lookback_days=days, ignore_seen=True, notify=False)

    for app in result["new_applications"]:
        log.info("Recovered application: %s -- %s", app["company"], app["position"])
    for upd in result["updates"]:
        log.info("Applied update: %s -> %s", upd["company"], upd["status"])
    log.info(
        "Done: %d application(s) recovered, %d update(s) applied.",
        len(result["new_applications"]), len(result["updates"]),
    )


if __name__ == "__main__":
    main()
