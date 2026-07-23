"""
Reads/writes the hidden "_CVVersions" tab: one row per AI-tailored CV +
cover letter ever generated. This is the CV "database" the similarity
matcher (src/similarity.py) reads from to suggest reusing an earlier
version when a new job description looks like one you've already tailored
for.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from . import sheets_client

RANGE = "_CVVersions!A:I"
HEADER = [
    "ID", "Created At", "Company", "Position", "Job Description Snippet",
    "Tailored CV Drive Link", "Cover Letter Drive Link",
    "Tailored CV File ID", "Cover Letter File ID",
]

# Keep enough of the job description to make similarity matching meaningful
# without letting one giant paste bloat the sheet cell past its limit.
MAX_SNIPPET_CHARS = 4000


def add_version(
    company: str, position: str, job_description: str,
    cv_link: str, cover_letter_link: str, cv_file_id: str, cover_letter_file_id: str,
) -> str:
    """Records one generated CV+cover-letter pair. Returns a short id you
    can reference elsewhere (not currently used as a lookup key by
    anything, but handy for logs/debugging). The file ids (not just the
    Drive links) are stored too, so the webapp can proxy-download either
    PDF through its own Google auth rather than requiring the browser to be
    signed into the right Google account."""
    version_id = uuid.uuid4().hex[:12]
    sheets_client.append_row(RANGE, [
        version_id, datetime.now().strftime("%Y-%m-%d %H:%M"), company, position,
        job_description[:MAX_SNIPPET_CHARS], cv_link, cover_letter_link,
        cv_file_id, cover_letter_file_id,
    ])
    return version_id


def list_versions() -> list[dict]:
    """Every generated version, oldest first, as dicts keyed by HEADER.
    Returns [] rather than raising if the _CVVersions tab doesn't exist yet
    (e.g. upgrade_sheet.py hasn't been run) -- callers should treat that the
    same as "no history yet", not as an error."""
    try:
        values = sheets_client.get_values(RANGE)
    except Exception:  # noqa: BLE001 - tab genuinely may not exist yet
        return []
    rows = values[1:] if values else []
    return [
        dict(zip(HEADER, row + [""] * (len(HEADER) - len(row))))
        for row in rows if any(row)
    ]
