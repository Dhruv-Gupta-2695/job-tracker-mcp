"""
MCP server: exposes the job-tracker automation as tools any MCP host (Claude
Desktop, Claude Code, etc.) can call on demand -- "scan my inbox now", "any
updates?", "show me the tracker".

This does NOT run continuously by itself; an MCP host only calls a tool when
you ask it to in chat. If you want this checking Gmail automatically in the
background regardless of whether Claude is open, use GitHub Actions or
run_daemon.py instead (or run several -- they all share the same Google
Sheet safely as long as you don't run them at the exact same instant).

Add to your MCP host config, e.g. Claude Desktop's claude_desktop_config.json:

  {
    "mcpServers": {
      "job-tracker": {
        "command": "python",
        "args": ["/absolute/path/to/job-tracker-mcp/mcp_server.py"]
      }
    }
  }
"""
from mcp.server.fastmcp import FastMCP

from src import automation, tracker

mcp = FastMCP("job-tracker")


@mcp.tool()
def scan_new_applications() -> str:
    """Scan Gmail for new job-application confirmation emails and add each
    one as a row in the Google Sheet tracker. Safe to call repeatedly."""
    added = automation.scan_new_applications()
    if not added:
        return "No new application confirmation emails found."
    lines = [f"- {a['company']}: {a['position']}" for a in added]
    return f"Added {len(added)} new application(s):\n" + "\n".join(lines)


@mcp.tool()
def check_for_updates() -> str:
    """Check every tracked application's email thread for new replies
    (interview invites, rejections, offers, etc). Updates the Google Sheet
    tracker and sends a Telegram message for each update found."""
    updates = automation.check_thread_updates()
    if not updates:
        return "No new updates on any tracked application."
    lines = [
        f"- {u['company']}: {u['status']} "
        f"({'Telegram sent' if u['telegram_sent'] else 'Telegram send FAILED'})"
        for u in updates
    ]
    return f"Found {len(updates)} update(s):\n" + "\n".join(lines)


@mcp.tool()
def run_full_check() -> str:
    """Run both scan_new_applications and check_for_updates in one call --
    the same thing the background daemon does on its own schedule."""
    result = automation.run_once()
    parts = []
    if result["new_applications"]:
        parts.append(f"{len(result['new_applications'])} new application(s) added")
    if result["updates"]:
        parts.append(f"{len(result['updates'])} update(s) found and sent to Telegram")
    return "; ".join(parts) if parts else "Nothing new: no new applications or updates."


@mcp.tool()
def list_tracked_applications() -> str:
    """Return every application currently in the Google Sheet tracker with
    its latest status."""
    apps = tracker.list_applications()
    if not apps:
        return "Tracker is empty -- run scan_new_applications first."
    lines = [
        f"- {a['Company']} | {a['Position']} | status: {a['Status']} | "
        f"applied: {a['Applied Date']}"
        for a in apps
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
