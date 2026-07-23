# Job Application Tracker (MCP agent)

Watches your Gmail for job-application confirmation emails, logs each one to
a Google Sheet, then watches those email threads for replies (interview
invites, rejections, offers) and pushes just the update to a Telegram bot.
Runs for free on GitHub Actions, so it works even when your laptop is off.

## How it's put together

```
job-tracker-mcp/
  src/
    config.py               loads .env
    google_auth.py            shared Google OAuth (Gmail + Sheets, one login)
    gmail_client.py           Gmail: fetching/parsing messages
    sheets_client.py          thin Sheets API v4 wrapper
    classifier.py             heuristics: is this an application? what kind of update?
    tracker.py                reads/writes the Google Sheet (Applications + _State tabs)
    telegram_notify.py        sends Telegram messages via the Bot API
    automation.py             ties the above together: scan + check-updates
  .github/workflows/scan.yml  GitHub Actions: runs the scan on a schedule, for free
  mcp_server.py              MCP server: lets Claude (or any MCP host) run scans on demand
  run_daemon.py              standalone loop: polls continuously on a fixed interval
  run_once.py                single scan-and-check pass -- used by both cron and GitHub Actions
  create_google_sheet.py     one-time: creates the Sheet and prints its ID
  migrate_existing_data.py   one-time: ports old job_applications.xlsx rows into the Sheet
  get_telegram_chat_id.py    one-time: finds your Telegram chat ID
  test_telegram.py           sends a one-off test message, for sanity-checking setup
  requirements.txt
  .env.example
```

There are four ways to actually run a scan -- pick whichever fits, or mix:

1. **GitHub Actions** (`.github/workflows/scan.yml`) -- runs on GitHub's
   servers on a schedule (default 12pm and 7pm IST), completely free, and
   works even if your laptop is off or asleep. This is the recommended
   setup -- see "Deploy for free with GitHub Actions" below.
2. **MCP server** (`mcp_server.py`) -- add it to Claude Desktop/Code's config
   and ask things like "scan my inbox for new applications" or "any
   updates?" in chat. Only runs when you ask.
3. **Scheduled via cron** (`run_once.py`) -- the same script GitHub Actions
   uses, but triggered by your own Mac's cron instead. Useful for testing
   before moving to GitHub Actions, or as a local alternative to it.
4. **Background daemon** (`run_daemon.py`) -- runs continuously on a fixed
   timer (default every 15 minutes) on whatever machine you start it on.

All four read/write the same Google Sheet, so it's fine to mix and match --
just avoid running two of them at the exact same moment, since they don't
coordinate with each other.

## Setup

### 1. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Google Cloud project (Gmail + Sheets)

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and
   create a new project (or reuse one).
2. Enable both the **Gmail API** and the **Google Sheets API** (APIs &
   Services -> Library -> search each one, click Enable).
3. Configure the **OAuth consent screen** (External is fine; add your own
   Gmail address as a test user).
4. Create credentials: APIs & Services -> Credentials -> Create Credentials
   -> OAuth client ID -> Application type **Desktop app**.
5. Download the JSON and save it as `credentials.json` in this project's
   root folder.
6. The first time you run `run_once.py` (or any of the scripts below), a
   browser window opens asking you to log in and grant access. After that,
   `token.json` caches the session.

Scopes used: `gmail.readonly` (can never send, delete, or modify anything in
your inbox) and `spreadsheets` (only touches the one Sheet this project
creates for itself).

If you already had this project set up before the Sheets migration, delete
your existing `token.json` once so the next login grants the new
`spreadsheets` scope alongside the Gmail one -- otherwise Sheets calls will
fail with a permissions error even though Gmail still works fine.

### 3. Create the Google Sheet

```bash
python3 create_google_sheet.py
```

Prints a URL (bookmark it -- this is your live tracker) and a spreadsheet
ID. Add the ID to `.env` as `GOOGLE_SHEET_ID`.

If you already had a `job_applications.xlsx` from before this migration,
port its rows over:
```bash
python3 migrate_existing_data.py
```

### 4. Telegram bot

1. Open Telegram and message **@BotFather**.
2. Send `/newbot` and follow the prompts (choose a display name and a
   username ending in "bot"). BotFather replies with a token that looks like
   `123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.
3. Open your new bot (BotFather gives you a t.me link) and send it any
   message, e.g. "hi". Telegram will not let a bot message you first, so this
   step is required before you can receive anything from it.
4. Run the chat-ID helper to find your chat ID:
   ```bash
   python3 get_telegram_chat_id.py <your_bot_token>
   ```
   It prints a `chat_id` -- copy that number.
5. Sanity-check it: `python3 test_telegram.py` should send you a test message.

No business verification, no approval wait, no templates. It just works.

### 5. Configure

```bash
cp .env.example .env
```

Fill in `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `GOOGLE_SHEET_ID`.

### 6. First manual run

```bash
python3 run_once.py
```

This triggers the Google OAuth browser login (once) and does a first real
scan. Check the Sheet URL from step 3 to see what got logged.

## Deploy for free with GitHub Actions

This is the recommended way to run it long-term -- no laptop uptime
required, no hosting cost.

1. **Create a GitHub repo** (github.com/new -- private is fine, free tier
   easily covers this: ~2 runs/day at under a minute each is nowhere near
   the 2000 free minutes/month private repos get, and public repos have no
   Actions minute limit at all).

2. **Push this project to it** (from the project folder):
   ```bash
   git init
   git add .
   git commit -m "Job application tracker"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git push -u origin main
   ```
   `.gitignore` already excludes `credentials.json`, `token.json`, `.env`,
   and `job_applications.xlsx` -- none of your secrets or local files get
   pushed, only source code.

3. **Add repository secrets** (on GitHub: Settings -> Secrets and variables
   -> Actions -> New repository secret). Add each of these:
   - `GMAIL_CREDENTIALS_JSON` -- paste the entire contents of your local
     `credentials.json` file.
   - `GMAIL_TOKEN_JSON` -- paste the entire contents of your local
     `token.json` file (make sure this was generated *after* you added the
     Sheets scope in step 2 above).
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GOOGLE_SHEET_ID` -- same
     values as in your local `.env`.

4. **Test it manually first**: on GitHub, go to the Actions tab -> "Job
   Tracker Scan" workflow -> "Run workflow" button. This runs it immediately
   without waiting for the schedule, so you can confirm the secrets are
   right before trusting it to run unattended.

5. Once that succeeds, it runs automatically at 12:00pm and 7:00pm IST every
   day (the cron schedule in `.github/workflows/scan.yml` is in UTC --
   already converted; edit it if you're in a different timezone).

6. **Turn off your local cron job** now that GitHub Actions has taken over,
   so the two don't both try to scan/write to the Sheet at the same time:
   ```bash
   crontab -e
   ```
   Delete the two `run_once.py` lines, save and exit.

**On the 7-day Gmail login expiry (see below):** it still applies inside
GitHub Actions, since the underlying cause is Google's policy, not where
the code runs. When it happens, you'll get the same Telegram alert -- the
fix is to run `python3 run_once.py` locally once to re-auth, then update
the `GMAIL_TOKEN_JSON` secret on GitHub with the new `token.json` contents.

## How detection works (and where to tune it)

`src/classifier.py` uses keyword/domain heuristics, not ML:

- **New application detection**: looks for phrases like "application
  received" / "thank you for applying" combined with either a known ATS
  sending domain (Greenhouse, Lever, Workday, iCIMS, LinkedIn, BrassRing,
  Ultipro, Darwinbox, etc.) or the word "application" in the email. Edit
  `ATS_DOMAINS` and `CONFIRMATION_PHRASES` to match what your real emails
  look like.
- **Update classification**: once a thread is tracked, any new reply is
  scanned for offer / rejection / interview keywords (`OFFER_PHRASES`,
  `REJECTION_PHRASES`, `INTERVIEW_PHRASES`); anything else is logged as a
  generic "update".
- **Company/position extraction**: pulled from the sender's display name
  (falling back to the domain if the display name is just a raw email
  address) and a couple of sanity-checked regex patterns against the
  subject/body. It will not be perfect for every ATS template -- you can
  hand-edit rows directly in the Sheet any time; the tracker only ever
  reads the Thread ID column to match rows, so editing other cells is
  always safe.
- **Duplicate detection**: if the same sender emails again within 14 days
  in a different thread (common for "confirm your identity" or portal-invite
  follow-ups), it's treated as the same application rather than a new row.
  Tune `DEDUPE_WINDOW_DAYS` in `tracker.py` if this ever merges two
  genuinely different applications to the same company.

## Notes and limitations

- Runs a straightforward search-since-last-scan against Gmail rather than a
  real-time push subscription (Gmail push requires Google Cloud Pub/Sub and
  domain verification -- overkill for a personal tool).
- `gmail.readonly` scope means this can never send mail, modify labels, or
  delete anything in your inbox. `spreadsheets` scope only ever touches the
  one Sheet this project created.
- Telegram messages are sent as free-form text through your own bot -- no
  templates, no approval queue, and no expiring session to keep alive.
- **Gmail login expires roughly every 7 days.** Since the Google Cloud
  project stays in "Testing" publishing status (verifying it for
  production is unnecessary overhead for a personal tool), Google expires
  the cached refresh token on that schedule -- this applies the same way
  whether you're running locally or on GitHub Actions. When it happens,
  every runner detects it specifically and sends you a Telegram message
  telling you exactly what to do (re-run `run_once.py` locally, and update
  the `GMAIL_TOKEN_JSON` secret if you're on GitHub Actions). Any other
  unexpected scan failure also sends a shorter "check daemon.log" alert the
  same way.
