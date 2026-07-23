"""
Runs a single scan-and-check pass, then exits. This is meant to be triggered
by cron (or launchd/Task Scheduler) at fixed times of day, e.g. noon and
7pm, rather than left running continuously like run_daemon.py.

Usage: python3 run_once.py
"""
import logging

from src import automation, config, telegram_notify
from src.gmail_client import GmailAuthExpired

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("job-tracker-run-once")


def _alert(message: str) -> None:
    """Best-effort Telegram alert -- if Telegram itself is unreachable,
    just log it rather than masking the original error with a new one."""
    try:
        telegram_notify.send_telegram_alert(message)
    except Exception:
        log.exception("Also failed to send the Telegram alert about the error below")


def main() -> None:
    log.info("Running scan (tracker: %s)", config.GOOGLE_SHEET_URL or config.GOOGLE_SHEET_ID)
    try:
        result = automation.run_once()
        if result["new_applications"]:
            log.info("New applications: %s", result["new_applications"])
        if result["updates"]:
            log.info("Updates sent: %s", result["updates"])
        if not result["new_applications"] and not result["updates"]:
            log.info("Nothing new this pass.")
    except GmailAuthExpired:
        log.exception("Gmail login expired")
        _alert(
            "Your Gmail login expired (this happens automatically about "
            "every 7 days while the Google app is in testing mode). Run "
            "'python3 run_once.py' on your Mac once to log back in -- "
            "scheduled runs will resume normally after that."
        )
        raise
    except Exception:
        log.exception("Error during scan")
        _alert("A scan failed with an unexpected error. Check daemon.log for details.")
        raise


if __name__ == "__main__":
    main()
