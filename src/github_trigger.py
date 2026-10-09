"""
Triggers the "Job Tracker Scan" GitHub Actions workflow on demand (the same
workflow that already runs on its own twice a day) via GitHub's REST API --
this is what powers the "Run scan now" button in the webapp UI.

Requires a fine-grained GitHub Personal Access Token scoped to this one
repo, with Actions set to Read and write (config.GITHUB_PAT). This project
never creates, stores, or handles that token for you beyond reading it from
your own environment -- you generate it yourself in GitHub's settings.
"""
from __future__ import annotations

import requests

from . import config


def trigger_scan(ref: str = "main") -> None:
    """Raises RuntimeError with a clear message if not configured, or if
    GitHub's API rejects the request (bad/expired token, wrong repo name,
    workflow file renamed, etc.) -- callers should surface that message
    directly rather than a raw traceback."""
    if not config.GITHUB_PAT or not config.GITHUB_REPO:
        raise RuntimeError(
            "GITHUB_PAT and GITHUB_REPO must both be set to use the "
            "run-scan-now button. See the README for how to create a "
            "fine-grained token (Actions: Read and write, scoped to just "
            "this repo)."
        )
    url = (
        f"https://api.github.com/repos/{config.GITHUB_REPO}/actions/workflows/"
        f"{config.GITHUB_WORKFLOW_FILE}/dispatches"
    )
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {config.GITHUB_PAT}",
            "Accept": "application/vnd.github+json",
        },
        json={"ref": ref},
        timeout=15,
    )
    # GitHub's workflow_dispatch endpoint returns 204 No Content on success.
    if resp.status_code != 204:
        raise RuntimeError(
            f"GitHub API error triggering the scan workflow "
            f"(HTTP {resp.status_code}): {resp.text[:300]}"
        )


def minutes_since_last_run() -> float | None:
    """Minutes since the most recent run of the scan workflow was created
    (any trigger: scheduled, manual, or API). None if there are no runs."""
    from datetime import datetime, timezone

    url = (
        f"https://api.github.com/repos/{config.GITHUB_REPO}/actions/workflows/"
        f"{config.GITHUB_WORKFLOW_FILE}/runs"
    )
    resp = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {config.GITHUB_PAT}",
            "Accept": "application/vnd.github+json",
        },
        params={"per_page": 1},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"GitHub API error reading recent runs (HTTP {resp.status_code}): {resp.text[:300]}"
        )
    runs = resp.json().get("workflow_runs", [])
    if not runs:
        return None
    created = datetime.fromisoformat(runs[0]["created_at"].replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - created).total_seconds() / 60


def trigger_scan_if_stale(max_age_minutes: float, ref: str = "main") -> bool:
    """Backup-trigger helper: only fires the workflow if no run has been
    created in the last `max_age_minutes`. Returns True if it triggered one,
    False if it skipped because a recent run already exists -- so a primary
    and a backup scheduler can both call this without double-running."""
    age = minutes_since_last_run()
    if age is not None and age < max_age_minutes:
        return False
    trigger_scan(ref)
    return True
