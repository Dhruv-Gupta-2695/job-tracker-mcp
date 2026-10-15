"""
Reads/writes the hidden "_Prompts" tab -- editable templates for AI CV
tailoring and cover letter generation ("dynamic prompts"). upgrade_sheet.py
seeds this tab with DEFAULT_PROMPTS the first time it's run; after that,
your edits (made from the webapp UI, or directly in the Sheet) are never
overwritten by re-running it.

Templates use plain str.format() placeholders: {core_cv} and
{job_description}. If you edit a template in the Sheet, keep both
placeholders in it or generation will fail with a KeyError -- that's on
purpose, so a broken template fails loudly rather than silently sending an
incomplete prompt to the model.
"""
from __future__ import annotations

from typing import Optional

from . import cv_struct, sheets_client

RANGE = "_Prompts!A:B"

DEFAULT_PROMPTS = {
    "cv_tailor": (
        "You are an expert resume writer. Rewrite the following CORE CV so it is "
        "tightly tailored to the JOB DESCRIPTION below, while staying strictly "
        "truthful to the original content (never invent employers, titles, dates, "
        "or skills that are not in the core CV). Reorder and rephrase bullet points "
        "to foreground the most relevant experience, mirror keywords/terminology "
        "from the job description where genuinely applicable, and keep roughly the "
        "same overall length and section structure as the original. Output ONLY "
        "the tailored CV text, no commentary.\n\n"
        "CORE CV:\n{core_cv}\n\nJOB DESCRIPTION:\n{job_description}"
    ),
    # JSON-aware CV prompt (the portal's generator). {core_cv_json} and {job_description} required.
    "cv_tailor_json": cv_struct.TAILOR_PROMPT,
    "cover_letter_v2": (
        "Write the BODY of a short cover letter for the candidate below, applying to the role in the "
        "JOB DESCRIPTION. Company: {company}. Role: {position}.\n\n"
        "Format: exactly 4 short paragraphs separated by a blank line, 130 to 180 words in total. "
        "No greeting, no sign-off, no name, no headings, no bullets (these are added automatically).\n"
        "Paragraph 1: say you are applying for the role at the company, then one specific sentence showing "
        "you understood what the company does or the problem this role works on.\n"
        "Paragraph 2: the most relevant real experience from the CV, with its real figures, in plain sentences.\n"
        "Paragraph 3: one relevant project or achievement from the CV and what it showed.\n"
        "Paragraph 4: one honest line about a tool or skill in the job description that the CV does not show "
        "(for example: I have not used X yet, but ...), then one short line on availability and being glad to talk.\n\n"
        "Style: simple, direct, first person, human. Short sentences. No buzzwords or filler (passionate, thrive, "
        "fast-paced, leverage, synergy, excited to, strong interest). No dashes of any kind between clauses.\n"
        "Truth rules: use only facts and figures that are in the CV. Never invent employers, numbers, tools or "
        "claims. If nothing in the CV is relevant to a point, keep it general rather than make something up.\n"
        "Output only the 4 paragraphs.\n\n"
        "CORE CV:\n{core_cv}\n\nJOB DESCRIPTION:\n{job_description}"
    ),
    "cover_letter": (
        "You are an expert cover letter writer. Using the CORE CV and JOB "
        "DESCRIPTION below, write a concise, warm, professional cover letter "
        "(3-4 short paragraphs) that connects the candidate's real experience to "
        "this specific role, without inventing anything not in the CV. Avoid "
        "generic filler phrases. Output ONLY the letter body text, ending with a "
        "simple closing such as \"Best regards,\".\n\n"
        "CORE CV:\n{core_cv}\n\nJOB DESCRIPTION:\n{job_description}"
    ),
}


def get_prompt(name: str) -> str:
    """Returns the current template for "cv_tailor" or "cover_letter". Falls
    back to DEFAULT_PROMPTS if the _Prompts tab doesn't exist yet (e.g.
    upgrade_sheet.py hasn't been run) or the row was somehow deleted."""
    values = sheets_client.get_values(RANGE)
    for row in values[1:] if values else []:
        if len(row) >= 2 and row[0] == name:
            return row[1]
    return DEFAULT_PROMPTS.get(name, "")


def set_prompt(name: str, template: str) -> None:
    """Update one prompt's template in place (adds the row if it's somehow
    missing, e.g. a custom prompt name not in DEFAULT_PROMPTS)."""
    values = sheets_client.get_values(RANGE)
    rows = values[1:] if values else []
    for i, row in enumerate(rows):
        if row and row[0] == name:
            sheets_client.update_row("_Prompts", i + 2, [name, template])
            return
    sheets_client.append_row(RANGE, [name, template])


def list_prompts() -> dict[str, str]:
    values = sheets_client.get_values(RANGE)
    rows = values[1:] if values else []
    prompts = {row[0]: row[1] for row in rows if len(row) >= 2}
    for name, default in DEFAULT_PROMPTS.items():
        prompts.setdefault(name, default)
    return prompts
