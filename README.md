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
  upgrade_sheet.py           one-time (safe to re-run): dashboard tab, color-coding, dropdown
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
your inbox), `spreadsheets` (only touches the one Sheet this project creates
for itself), and `gmail.compose` (can only create/edit Gmail Drafts -- it can
never send mail on its own; a draft always requires you to open it and click
Send yourself).

If you already had this project set up before the Sheets migration (or
before the `gmail.compose` scope was added for follow-up drafts), delete
your existing `token.json` once so the next login grants the new scope(s) --
otherwise the corresponding API calls will fail with a permissions error
even though the rest keeps working fine. If you're running on GitHub
Actions, remember to update the `GMAIL_TOKEN_JSON` secret with the new
`token.json` contents afterward.

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

Then polish it -- adds a Dashboard tab (status counts + pie chart, response
rate, average days to first reply, and an applications-by-company
breakdown), freezes the header row, color-codes rows by status, and adds a
dropdown on the Status column. Safe to re-run any time (e.g. after a schema
change, or to pick up the newer Dashboard metrics on a sheet you set up
before they existed):
```bash
python3 upgrade_sheet.py
```
If you have older rows from before "Gmail Link"/"Description" columns
existed, this also migrates the sheet and backfills both for you.

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

## The webapp: CV tailoring, cover letters, live UI

A second, optional piece (`webapp/`): view your tracked applications, upload
or update a "core CV", paste a job description and get an AI-tailored CV +
matching cover letter as PDFs, browse everything you've generated before
(with a similarity check that suggests reusing an earlier version instead
of regenerating from scratch), edit the AI prompts, and trigger a scan on
demand. It's a separate FastAPI app from the Gmail-scanning side -- you can
run one without the other.

### One-time setup

1. Get an API key at [console.anthropic.com](https://console.anthropic.com)
   -- this is separate billing from any claude.ai/Claude Pro/Max
   subscription, they're two different products. With the default Haiku
   model, generation costs roughly $0.01 per tailored CV and $0.007 per
   cover letter, so a few dollars of credit covers hundreds of each. Add it
   as `ANTHROPIC_API_KEY`.
2. Pick a `WEBAPP_PASSWORD`. This repo is public and a free host gives your
   app a public URL, so without a password anyone who finds that URL could
   read your CV, generate documents on your Anthropic bill, and see your
   application history. There's no other login system -- just this one
   shared password (HTTP Basic Auth), and it's required: the app refuses to
   serve any request if it's unset, rather than quietly running open.
3. Re-auth once more locally (delete `token.json`, run `python3
   run_once.py`) to grant the `drive.file` scope alongside `gmail.compose`
   -- if you haven't re-authed yet for the follow-up-drafts feature, do
   both scopes in one pass. Update the `GMAIL_TOKEN_JSON` secret on GitHub
   afterward.
4. Run `python3 upgrade_sheet.py` once more to add the hidden
   `_CVVersions` and `_Prompts` tabs to your Sheet.
5. *(Optional, for the "Run scan now" button)* Create a fine-grained GitHub
   Personal Access Token at
   [github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens)
   -- repository access limited to just this one repo, permission
   "Actions: Read and write". Set `GITHUB_PAT` and `GITHUB_REPO` (e.g.
   `your-username/job-tracker-mcp`). Leave both unset to just hide the
   button; everything else still works.

### Deploy to Render (free)

1. Push this repo's latest commit to GitHub (the same repo the GitHub
   Actions scan already lives in).
2. On [render.com](https://render.com): New -> Blueprint, connect the repo.
   Render reads `render.yaml` and prompts you for each secret value --
   `GMAIL_CREDENTIALS_JSON`/`GMAIL_TOKEN_JSON` are the same file contents
   you already used for the GitHub Actions secrets, the rest as set up
   above.
3. Deploy. You'll get a URL like `https://job-tracker-webapp.onrender.com`
   -- bookmark it.

Free-tier tradeoff: Render spins the service down after 15 minutes with no
traffic, and the next visit takes about a minute to spin back up -- a real
but minor annoyance for $0/month (Render's paid tiers, ~$5-7/month, remove
this if it bothers you).

### How the CV/cover-letter pieces fit together

- **Core CV**: stored as one file in a "Job Tracker CVs" folder in your
  Google Drive, via the `drive.file` scope (which can only ever see files
  this app created -- never your whole Drive). Upload once from the CV
  Manager tab, replace any time.
- **Generation**: paste a job description, and it's checked against every
  job description you've generated for before (`src/similarity.py`, plain
  `difflib` text comparison -- no extra API cost). If it looks similar
  enough, you're shown the earlier CV/cover letter instead of generating a
  new one, with an option to generate fresh anyway. Otherwise, the AI model
  tailors your core CV and drafts a cover letter, both exported as PDFs and
  saved to Drive.
- **History**: every generated version is logged in a hidden
  `_CVVersions` tab in your Sheet -- this is the "database" the similarity
  check reads from.
- **Prompts**: the exact instructions sent to the model for CV tailoring
  and cover letter writing live in a hidden `_Prompts` tab and are editable
  from the Prompts tab in the UI -- change tone, structure, or emphasis any
  time without touching code. A template that drops the required
  `{core_cv}`/`{job_description}` placeholders is rejected with a clear
  error rather than silently sending a broken prompt to the model.

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
- **Gmail Link and Description**: every row has a "Gmail Link" column that
  opens the whole email thread directly (original confirmation plus every
  reply), and a "Description" column with the full body of the confirmation
  email -- not a truncated snippet. "Last Update Summary" likewise holds the
  full body of whatever reply triggered the update, so you never have to
  guess what a short preview was cut off from.
- **Stale-application follow-up nudges**: if an application has sat at
  "applied" with zero reply for `STALE_NUDGE_DAYS` (default 21, set to 0 to
  disable), you get a one-time Telegram nudge, and a polite follow-up email
  is drafted in your Gmail Drafts (addressed to the original sender, in the
  same thread) -- never sent automatically, you review and send it
  yourself. Tracked via the "Stale Nudge Sent" column so it only ever
  happens once per application.

## Notes and limitations

- Runs a straightforward search-since-last-scan against Gmail rather than a
  real-time push subscription (Gmail push requires Google Cloud Pub/Sub and
  domain verification -- overkill for a personal tool).
- `gmail.readonly` scope means this can never send mail, modify labels, or
  delete anything in your inbox. `spreadsheets` scope only ever touches the
  one Sheet this project created.
- Telegram messages are sent as free-form text through your own bot -- no
  templates, no approval queue, and no expiring session to keep alive.
- **Gmail login expires roughly every 7 days if your Google Cloud project
  is still in "Testing" publishing status.** This is a hard Google policy:
  any app left in Testing gets its refresh tokens killed after 7 days, no
  exceptions. **Fix it for good** by publishing the app instead of staying
  in Testing:
  1. [Google Cloud Console](https://console.cloud.google.com/) -> APIs &
     Services -> OAuth consent screen.
  2. Click **Publish App** (moves status from "Testing" to "In production").
  3. Confirm the warning dialog -- for personal/sensitive scopes like
     Gmail, Google does NOT require the full verification review/security
     audit unless you're serving the public at scale. You'll just see an
     "unverified app" warning the next time you log in (click Advanced ->
     Go to [app name] (unsafe) -- this is your own app, so that's fine);
     refresh tokens then stop expiring on the 7-day schedule entirely.
  4. Re-auth one more time after publishing (delete `token.json`, run
     `python3 run_once.py`) so the new token is issued under the new
     status, then update `GMAIL_TOKEN_JSON` wherever it's stored (GitHub
     Actions secret, Render env var).

  If you'd rather not publish and just live with the 7-day cycle: when it
  happens, every runner detects it specifically and sends you a Telegram
  message telling you exactly what to do (re-run `run_once.py` locally,
  update `GMAIL_TOKEN_JSON` on GitHub Actions/Render). Any other unexpected
  scan failure also sends a shorter "check daemon.log" alert the same way.
