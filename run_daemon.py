"""
Standalone background runner -- no Claude/MCP host required. Run this with
`python run_daemon.py` (or as a system service / cron @reboot job) and it
will keep scanning Gmail and sending Telegram updates on its own, on the
interval set by POLL_INTERVAL_MINUTES in .env.

This is the piece that gives you a fully autonomous tracker: leave it
running on a machine that's always on (or a small always-on server / VPS /
Raspberry Pi) and it works even if you never open Claude.
"""
import logging
import time
from datetime import datetime

from src import automation, config, telegram_notify
from src.gmail_client import GmailAuthExpired

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("job-tracker-daemon")


def _alert(message: str) -> None:
    try:
        telegram_notify.send_telegram_alert(message)
    except Exception:
        log.exception("Also failed to send the Telegram alert about the error below")


def main() -> None:
    log.info(
        "Starting job-tracker daemon (poll every %s min, tracker: %s)",
        config.POLL_INTERVAL_MINUTES, config.GOOGLE_SHEET_URL or config.GOOGLE_SHEET_ID,
    )
    while True:
        try:
            result = automation.run_once()
            if result["new_applications"]:
                log.info("New applications: %s", result["new_applications"])
            if result["updates"]:
                log.info("Updates sent: %s", result["updates"])
            if not result["new_applications"] and not result["updates"]:
                log.info("Nothing new this pass.")
        except GmailAuthExpired:
            log.exception("Gmail login expired -- stopping the daemon")
            _alert(
                "Your Gmail login expired (this happens automatically about "
                "every 7 days while the Google app is in testing mode). Run "
                "'python3 run_once.py' on your Mac once to log back in, then "
                "restart run_daemon.py."
            )
            raise
        except Exception:
            log.exception("Error during scan -- will retry next interval")

        time.sleep(config.POLL_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()
