# Privacy Policy

This is a personal, single-user tool. It is not a commercial product and
does not collect, sell, or share data with any third party.

## What it accesses

Using your own Google account, this tool reads and writes only:

- **Gmail** (`gmail.readonly`, `gmail.compose`): reads emails to detect
  job-application confirmations and replies, and creates draft follow-up
  emails. It can never send mail on its own, delete anything, or modify
  labels.
- **Google Sheets** (`spreadsheets`): reads and writes only the one Google
  Sheet this tool creates for itself, to track your job applications.
- **Google Drive** (`drive.file`): reads and writes only files this tool
  itself creates (your uploaded CV and AI-generated CVs/cover letters) --
  it never sees any other file in your Drive.

## Where your data goes

Everything stays within your own Google account (Gmail, Sheets, Drive) and,
if you use the optional AI features, your core CV and the job descriptions
you paste are sent to Google's Gemini API using your own API key, solely to
generate tailored CVs and cover letters on your request. No data is sent
anywhere else, and no data is retained by the developer of this tool. If
you're using Gemini's free tier, Google's own terms allow that data to be
used to improve their products (this does not apply if you're on a paid
Gemini plan instead) -- see Google's AI/API terms for the current details.

## Who this applies to

This tool is run by and for a single individual, using their own Google
Cloud project and their own credentials. It is not distributed to or used
by the public.
