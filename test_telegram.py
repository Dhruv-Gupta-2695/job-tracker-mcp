"""
Quick sanity check: sends a single test message to your Telegram bot using
the credentials in .env, so you can confirm the token/chat ID actually work
without waiting for a real Gmail scan to trigger one.

Usage: python3 test_telegram.py
"""
from src import telegram_notify

if __name__ == "__main__":
    message_id = telegram_notify.send_telegram_alert(
        "This is a test message from your job tracker bot. "
        "If you can see this, Telegram is wired up correctly!"
    )
    print(f"Sent successfully. Telegram message_id: {message_id}")
