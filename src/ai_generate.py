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
    # A hard per-request timeout, so one stuck call can never leave the UI spinning.
    return genai.Client(
        api_key=config.GEMINI_API_KEY,
        http_options=genai.types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
    )


FALLBACK_MODEL = "gemini-flash-lite-latest"
_RETRY_DELAYS = (2,)  # one quick retry on the main model, then the fallback model
REQUEST_TIMEOUT_MS = 20_000  # no single attempt may hang longer than this
_RETRYABLE_CODES = (429, 500, 503, 504)


def _config_for(model: str, max_tokens: int, thinking_off: bool):
    kwargs = {"max_output_tokens": max_tokens}
    # Flash models "think" before answering: slow, and it eats the token budget.
    # Short emails and CVs do not need it.
    if thinking_off and "flash" in model:
        kwargs["thinking_config"] = genai.types.ThinkingConfig(thinking_budget=0)
    return genai.types.GenerateContentConfig(**kwargs)


def _call_model(prompt_text: str, max_tokens: int) -> str:
    """Calls Gemini with a timeout, retrying temporary failures (overload,
    timeouts) once and then falling back to a lighter model. Raises
    RuntimeError with a plain message if it is still unavailable."""
    import time
    from google.genai import errors

    client = _client()
    attempts = [(config.AI_MODEL, 0)] + [(config.AI_MODEL, d) for d in _RETRY_DELAYS]
    if FALLBACK_MODEL != config.AI_MODEL:
        attempts.append((FALLBACK_MODEL, 1))

    last_exc: Exception | None = None
    for model, delay in attempts:
        if delay:
            time.sleep(delay)
        thinking_off = True
        for _ in range(2):  # 2nd pass only if the model rejects the thinking setting
            try:
                response = client.models.generate_content(
                    model=model, contents=prompt_text,
                    config=_config_for(model, max_tokens, thinking_off),
                )
                return (response.text or "").strip()
            except errors.APIError as exc:
                last_exc = exc
                # Some models reject the "thinking off" setting with a vague
                # 400 INVALID_ARGUMENT. Retry the same model once without it.
                if thinking_off and getattr(exc, "code", None) == 400:
                    thinking_off = False
                    continue
                if getattr(exc, "code", None) not in _RETRYABLE_CODES:
                    raise
                break
            except Exception as exc:  # noqa: BLE001 - network timeouts come from httpx, not genai
                last_exc = exc
                break
    raise RuntimeError(
        "Gemini is slow or overloaded right now (Google-side, usually brief). I retried and "
        "tried a lighter model too. Wait a minute and click again."
    ) from last_exc


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

    return _call_model(prompt_text, MAX_OUTPUT_TOKENS)


def generate_text(prompt_text: str, max_tokens: int = 800) -> str:
    """One-off generation from a fully built prompt (used for email replies)."""
    return _call_model(prompt_text, max_tokens)


def tailor_cv(core_cv: str, job_description: str) -> str:
    """Returns the tailored CV as plain text (ready for pdf_export.py)."""
    return _generate("cv_tailor", core_cv, job_description)


def generate_cover_letter(core_cv: str, job_description: str) -> str:
    """Returns the cover letter body as plain text (ready for pdf_export.py)."""
    return _generate("cover_letter", core_cv, job_description)
