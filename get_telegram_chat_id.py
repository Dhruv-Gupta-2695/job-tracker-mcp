"""
One-time helper: run this after you've created your bot with @BotFather and
sent it at least one message from your own Telegram account. It prints your
chat ID so you can paste it into .env as TELEGRAM_CHAT_ID.

Usage:
    python get_telegram_chat_id.py <bot_token>

(You can also set TELEGRAM_BOT_TOKEN in .env first and just run it with no
arguments.)
"""
import sys

import requests

from src import config


def main() -> None:
    token = sys.argv[1] if len(sys.argv) > 1 else config.TELEGRAM_BOT_TOKEN
    if not token:
        print("Pass your bot token as an argument, or set TELEGRAM_BOT_TOKEN in .env first.")
        sys.exit(1)

    resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("ok"):
        print("Telegram API error:", data)
        sys.exit(1)

    updates = data.get("result", [])
    if not updates:
        print(
            "No messages found yet. Open your bot in Telegram and send it any "
            "message (e.g. 'hi'), then run this script again."
        )
        return

    seen = set()
    for update in updates:
        message = update.get("message", {})
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        if chat_id is None or chat_id in seen:
            continue
        seen.add(chat_id)
        name = chat.get("username") or chat.get("first_name") or "unknown"
        print(f"chat_id: {chat_id}   (from: {name})")

    print("\nCopy the chat_id above into .env as TELEGRAM_CHAT_ID.")


if __name__ == "__main__":
    main()
