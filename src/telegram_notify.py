"""
Sends Telegram messages via the plain Bot API (a simple HTTPS POST, no SDK
needed).

Setup: message @BotFather on Telegram, send /newbot, follow the prompts to
get a bot token. Telegram will not let a bot message you first, so you must
also send any message (e.g. "hi") to your new bot from your own account
once -- then run get_telegram_chat_id.py to find the chat ID to put in .env.
"""
from __future__ import annotations

import requests

from . import config

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def send_telegram_update(company: str, position: str, status: str, summary: str) -> str:
    """Send a short Telegram message about one application update.
    Returns the Telegram message_id (as a string)."""
    status_labels = {
        "interview": "Interview / next steps",
        "offer": "Offer!",
        "rejected": "Rejected",
        "update": "Update",
    }
    label = status_labels.get(status, "Update")

    text = (
        f"*{label}* - {company}\n"
        f"{position}\n\n"
        f"{summary.strip()[:300]}"
    )

    url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN, method="sendMessage")
    resp = requests.post(
        url,
        data={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        },
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")
    return str(result["result"]["message_id"])


def send_telegram_alert(message: str) -> str:
    """Send a plain operational alert (not tied to a specific application) --
    used for things like "your Gmail login expired" or "a scan failed"."""
    url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN, method="sendMessage")
    resp = requests.post(
        url,
        data={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": f"Job tracker alert:\n{message}",
        },
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")
    return str(result["result"]["message_id"])
