"""
Calls the Gemini API to tailor a CV and draft a matching cover letter.

Uses config.GEMINI_API_KEY (from aistudio.google.com -- the free tier needs
no credit card and no billing account, see README's cost breakdown) and
config.AI_MODEL (a Flash model by default, which is what the free tier
covers; bump to a Pro model string in .env any time if you move to a paid
plan and want the quality upgrade).
"""
from __future__ import annotations

from google import genai

from . import config, prompts

MAX_OUTPUT_TOKENS = 2000


def _client() -> genai.Client:
    if not config.GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Get one for free at aistudio.google.com "
            "(no credit card needed for the free tier) and add it to your "
            "environment."
        )
    return genai.Client(api_key=config.GEMINI_API_KEY)


def _generate(prompt_name: str, core_cv: str, job_description: str) -> str:
    client = _client()  # checked first: fail fast on a missing API key, before touching Sheets at all
    template = prompts.get_prompt(prompt_name)

    # Checked explicitly rather than relying on str.format() to raise: a
    # template with a typo'd placeholder (e.g. "{corecv}") does raise
    # KeyError on its own, but a template with NO placeholders at all
    # (e.g. someone deleted "{job_description}" while editing) does NOT --
    # .format() just silently ignores unused kwargs, which would mean the
    # model quietly never sees the job description. Fail loudly instead.
    missing = [p for p in ("{core_cv}", "{job_description}") if p not in template]
    if missing:
        raise ValueError(
            f"The '{prompt_name}' prompt template is missing required "
            f"placeholder(s): {', '.join(missing)}. It must contain both "
            f"{{core_cv}} and {{job_description}} (edit it back in the "
            f"_Prompts tab, or via the webapp's prompt settings)."
        )
    prompt_text = template.format(core_cv=core_cv, job_description=job_description)

    response = client.models.generate_content(
        model=config.AI_MODEL,
        contents=prompt_text,
        config=genai.types.GenerateContentConfig(max_output_tokens=MAX_OUTPUT_TOKENS),
    )
    return (response.text or "").strip()


def tailor_cv(core_cv: str, job_description: str) -> str:
    """Returns the tailored CV as plain text (ready for pdf_export.py)."""
    return _generate("cv_tailor", core_cv, job_description)


def generate_cover_letter(core_cv: str, job_description: str) -> str:
    """Returns the cover letter body as plain text (ready for pdf_export.py)."""
    return _generate("cover_letter", core_cv, job_description)
