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
CV_JSON_TOKENS = 6000
_client_cache = None


def _client() -> genai.Client:
    if not config.GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Get one for free at aistudio.google.com "
            "(no credit card needed for the free tier) and add it to your "
            "environment."
        )
    # One shared client (it is thread-safe) with a hard per-request timeout, so a
    # stuck call can never leave the UI spinning and each call does not allocate
    # a fresh HTTP client.
    global _client_cache
    if _client_cache is None or _client_cache[0] != config.GEMINI_API_KEY:
        _client_cache = (config.GEMINI_API_KEY, genai.Client(
            api_key=config.GEMINI_API_KEY,
            http_options=genai.types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        ))
    return _client_cache[1]


FALLBACK_MODEL = "gemini-flash-lite-latest"
_RETRY_DELAYS = (2,)  # one quick retry on the main model, then the fallback model
REQUEST_TIMEOUT_MS = 45_000  # no single attempt may hang longer than this
_RETRYABLE_CODES = (429, 500, 503, 504)


def _config_for(model: str, max_tokens: int, thinking_off: bool, json_mode: bool = False):
    kwargs = {"max_output_tokens": max_tokens}
    if json_mode:
        kwargs["response_mime_type"] = "application/json"
    # Flash models "think" before answering: slow, and it eats the token budget.
    # Short emails and CVs do not need it.
    if thinking_off and "flash" in model:
        kwargs["thinking_config"] = genai.types.ThinkingConfig(thinking_budget=0)
    return genai.types.GenerateContentConfig(**kwargs)


def _call_model(prompt_text: str, max_tokens: int, json_mode: bool = False) -> str:
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
                    config=_config_for(model, max_tokens, thinking_off, json_mode),
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
    """Legacy plain-text tailoring (kept for the prompt editor); the portal now uses tailor_cv_struct."""
    return _generate("cv_tailor", core_cv, job_description)


def parse_cv_struct(cv_text: str, links: list[str]) -> dict:
    """One-time: turns the core CV text into the structured model (see cv_struct.py)."""
    from . import cv_struct
    _client()  # fail fast on a missing key
    prompt = cv_struct.PARSE_PROMPT.replace("{links}", "\n".join(links) or "(none)").replace("{cv_text}", cv_text)
    last = None
    for _ in range(2):
        try:
            cv = cv_struct.normalize(cv_struct.parse_json(_call_model(prompt, CV_JSON_TOKENS, json_mode=True)))
            cv_struct.check_parse_fidelity(cv, cv_text)
            _clean_links(cv, links)
            return cv
        except (ValueError, KeyError) as exc:
            last = exc
    raise RuntimeError(f"Could not read your CV's structure: {last}")


def _clean_links(cv: dict, links: list[str]) -> None:
    """Only URLs that really exist in the uploaded file may appear."""
    if not links:
        return
    ok = set(links)
    for c in cv["contact"]:
        if c.get("url") and c["url"] not in ok:
            c["url"] = ""
    for sec in cv["sections"]:
        for it in sec["items"]:
            if it["type"] == "bullet" and it["link_url"] not in ok:
                it["link_url"] = ""
                it["link_label"] = ""


def tailor_cv_struct(core_struct: dict, job_description: str) -> tuple[dict, dict]:
    """Returns (final_cv, stats). Layout/structure always comes from core_struct;
    only validated wording changes are taken from the AI."""
    import json
    from . import cv_struct
    _client()
    template = prompts.get_prompt("cv_tailor_json") or cv_struct.TAILOR_PROMPT
    if "{core_cv_json}" not in template or "{job_description}" not in template:
        template = cv_struct.TAILOR_PROMPT
    prompt = template.replace("{core_cv_json}", json.dumps(core_struct, ensure_ascii=False)).replace("{job_description}", job_description)
    try:
        tailored = cv_struct.parse_json(_call_model(prompt, CV_JSON_TOKENS, json_mode=True))
    except ValueError:
        tailored = {}
    return cv_struct.merge_tailored(core_struct, tailored)


def generate_cover_letter(core_cv: str, job_description: str, company: str = "", position: str = "") -> str:
    """Returns the cover letter BODY paragraphs as plain text (the PDF adds header, greeting and sign-off)."""
    _client()
    template = prompts.get_prompt("cover_letter_v2")
    if "{core_cv}" not in template or "{job_description}" not in template:
        template = prompts.DEFAULT_PROMPTS["cover_letter_v2"]
    prompt = (template.replace("{company}", company or "the company").replace("{position}", position or "the role")
              .replace("{core_cv}", core_cv).replace("{job_description}", job_description))
    return _call_model(prompt, MAX_OUTPUT_TOKENS)
