"""
Central configuration loaded from environment variables (see .env.example).

Nothing in this file should contain real secrets. Copy .env.example to .env
and fill in your own values; python-dotenv loads it automatically.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root regardless of current working directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _get(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


# --- Google (Gmail + Sheets share one login/token pair) ---
GMAIL_CREDENTIALS_PATH = _get("GMAIL_CREDENTIALS_PATH", str(PROJECT_ROOT / "credentials.json"))
GMAIL_TOKEN_PATH = _get("GMAIL_TOKEN_PATH", str(PROJECT_ROOT / "token.json"))
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    # gmail.compose: lets this create/edit Gmail Drafts only -- it can never
    # send mail on its own, delete anything, or read mail beyond what
    # gmail.readonly already grants. Used for the auto-drafted follow-up
    # emails feature (drafts always require you to click Send yourself).
    "https://www.googleapis.com/auth/gmail.compose",
    # drive.file: can only see/touch files this app itself created -- never
    # your whole Drive. Used to store your core CV and every generated
    # tailored CV/cover letter PDF (see webapp/cv_store.py).
    "https://www.googleapis.com/auth/drive.file",
]

# --- Telegram ---
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")  # from @BotFather
TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID")  # your own chat id, see get_telegram_chat_id.py

# --- Tracker (Google Sheet -- see create_google_sheet.py) ---
GOOGLE_SHEET_ID = _get("GOOGLE_SHEET_ID")
GOOGLE_SHEET_URL = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/edit" if GOOGLE_SHEET_ID else ""

# --- Polling ---
POLL_INTERVAL_MINUTES = int(_get("POLL_INTERVAL_MINUTES", "15"))

# How many days back to look the very first time the tool runs
INITIAL_LOOKBACK_DAYS = int(_get("INITIAL_LOOKBACK_DAYS", "30"))

# Send a Telegram nudge if an application has sat at "applied" (no reply of
# any kind) for at least this many days. Set to 0 to disable.
STALE_NUDGE_DAYS = int(_get("STALE_NUDGE_DAYS", "21"))

# --- webapp/ (CV tailoring, cover letters, live UI) ---
# From console.anthropic.com -- separate billing from any claude.ai
# subscription. Required only to run webapp/, not the Gmail-scanning side.
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
# Cheapest model by default (see README's cost breakdown); bump to a Sonnet
# model string here any time without touching code, once you've tested and
# want the quality upgrade.
AI_MODEL = _get("AI_MODEL", "claude-haiku-4-5-20251001")

# Fine-grained GitHub Personal Access Token (repo scope: Actions
# read/write) -- powers the "run scan now" button in the webapp UI. Leave
# unset to just hide that button; nothing else depends on it.
GITHUB_PAT = _get("GITHUB_PAT")
GITHUB_REPO = _get("GITHUB_REPO")  # "your-username/job-tracker-mcp"
GITHUB_WORKFLOW_FILE = _get("GITHUB_WORKFLOW_FILE", "scan.yml")

# Simple shared-secret login for the webapp (it has no other auth) -- pick
# your own value, required to use webapp/ at all once set.
WEBAPP_PASSWORD = _get("WEBAPP_PASSWORD")

# Just used to personalize PDF titles/filenames ("<name> - CV.pdf") -- not
# used for anything else.
CANDIDATE_NAME = _get("CANDIDATE_NAME", "Candidate")
