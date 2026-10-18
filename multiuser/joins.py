"""
Anonymous join counter with Telegram alerts, so the owner knows how close the app is to
Google's 100-user limit for unverified apps.

Two counters: people who signed in, and people who connected Gmail (the one Google's limit
applies to). Each person is counted once, by a salted hash of their user id, kept in a small
JSON file on the server's disk. No names or emails are stored or sent to Telegram.
If the Telegram settings are missing the counting still works and the alert is skipped.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading

import requests

STATS_PATH = os.environ.get("JOIN_STATS_PATH", "/data/joins.json")
LIMIT = int(os.environ.get("JOIN_LIMIT", "100") or 100)
SALT = os.environ.get("JOIN_SALT", "nextstep")
MILESTONES = (50, 75, 90, 99, 100)   # warned on the Gmail counter, which is the one Google caps
KINDS = ("signin", "gmail")
_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(STATS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return {k: list(data.get(k, [])) for k in KINDS}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(STATS_PATH) or ".", exist_ok=True)
    tmp = STATS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, STATS_PATH)   # never leaves a half-written file


def _tag(uid: str) -> str:
    return hashlib.sha256((SALT + uid).encode()).hexdigest()[:20]


def message_for(kind: str, counts: dict) -> str:
    gmail, signin = counts["gmail"], counts["signin"]
    if kind == "gmail":
        text = f"NextStep: another person connected Gmail. {gmail} of {LIMIT} Gmail slots used ({signin} have signed in)."
        if gmail in MILESTONES:
            left = LIMIT - gmail
            text += (f"\n\nHeads up: {left} slot{'s' if left != 1 else ''} left before Google's unverified-app limit. "
                     "Time to plan: apply for verification or pause sign-ups." if left > 0 else
                     "\n\nThe limit is reached. New people may be blocked from connecting Gmail.")
        return text
    return f"NextStep: someone new signed in. {signin} have signed in, {gmail} of {LIMIT} Gmail slots used."


def _send(text: str) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat, "text": text}, timeout=10).raise_for_status()


def record(uid: str, kind: str) -> dict | None:
    """Counts this person once per kind. Returns the new counts if they were new, else None."""
    if kind not in KINDS or not uid:
        return None
    tag = _tag(uid)
    with _lock:
        data = _load()
        if tag in data[kind]:
            return None
        data[kind].append(tag)
        _save(data)
        counts = {k: len(v) for k, v in data.items()}
    try:
        _send(message_for(kind, counts))
    except Exception as exc:  # noqa: BLE001 - an alert failing must never break the app; log the type only
        print(f"[joins] telegram alert failed: {type(exc).__name__}", flush=True)
    return counts
