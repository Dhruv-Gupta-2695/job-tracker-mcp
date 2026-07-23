"""
Calls the Anthropic API to tailor a CV and draft a matching cover letter.

Uses config.ANTHROPIC_API_KEY (from console.anthropic.com -- separate
billing from any claude.ai subscription, see README's cost breakdown) and
config.AI_MODEL (Haiku by default; bump to a Sonnet model string in .env
any time, without touching code, once you've tested and want the quality
upgrade over cost).
"""
from __future__ import annotations

from anthropic import Anthropic

from . import config, prompts

MAX_OUTPUT_TOKENS = 2000


def _client() -> Anthropic:
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Get one at console.anthropic.com "
            "(separate billing from any claude.ai/Claude Pro/Max subscription) "
            "and add it to your environment."
        )
    return Anthropic(api_key=config.ANTHROPIC_API_KEY)


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

    response = client.messages.create(
        model=config.AI_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[{"role": "user", "content": prompt_text}],
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()


def tailor_cv(core_cv: str, job_description: str) -> str:
    """Returns the tailored CV as plain text (ready for pdf_export.py)."""
    return _generate("cv_tailor", core_cv, job_description)


def generate_cover_letter(core_cv: str, job_description: str) -> str:
    """Returns the cover letter body as plain text (ready for pdf_export.py)."""
    return _generate("cover_letter", core_cv, job_description)
