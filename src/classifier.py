"""
Heuristics for figuring out, from subject/sender/body text alone:
  1. Is this email a "your application was received" confirmation?
  2. If it's a reply in an already-tracked thread, what kind of update is it
     (interview, rejection, offer, generic)?
  3. What company and job title does it look like it's about?

These are regex/keyword heuristics, not ML. They will not be perfect --
tune ATS_DOMAINS and the keyword lists below as you see what your real
application emails look like.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .gmail_client import EmailMessage

# Applicant tracking systems / job boards that send application confirmations
ATS_DOMAINS = [
    "greenhouse.io", "lever.co", "myworkday.com", "workday.com", "icims.com",
    "smartrecruiters.com", "taleo.net", "jobvite.com", "ashbyhq.com",
    "breezy.hr", "bamboohr.com", "successfactors.com", "workable.com",
    "workablemail.com", "linkedin.com", "indeed.com", "wellfound.com",
    "recruitee.com", "recruitee-mail.com", "brassring.com", "ultipro.com",
    "darwinbox.in", "darwinbox.com",
]

# Words that show up as regex captures but are never actually a job title --
# used to reject weak position matches rather than accept the first thing
# that technically matched.
_JUNK_POSITION_WORDS = {
    "following", "role", "this", "position", "us", "you", "our", "the",
    "that", "these", "those", "applied", "application", "team", "us.",
}

CONFIRMATION_PHRASES = [
    "application received", "thank you for applying", "thanks for applying",
    "we have received your application", "your application to",
    "application confirmation", "we've received your application",
    "thank you for your interest in", "application was submitted",
]

REJECTION_PHRASES = [
    "unfortunately", "not moving forward", "other candidates",
    "regret to inform", "decided not to proceed", "will not be moving forward",
    "pursue other candidates", "not selected",
]

INTERVIEW_PHRASES = [
    "interview", "schedule a call", "schedule a time", "next steps",
    "move forward", "phone screen", "technical assessment",
    "coding challenge", "online assessment", "would like to speak",
]

OFFER_PHRASES = [
    "pleased to offer", "job offer", "offer letter", "congratulations",
    "excited to offer", "extend an offer",
]

STATUS_ORDER = ["applied", "interview", "offer", "rejected"]


@dataclass
class Classification:
    is_application_confirmation: bool
    company: str
    position: str


@dataclass
class UpdateClassification:
    status: str  # one of "interview", "offer", "rejected", "update"


def _text_of(msg: EmailMessage) -> str:
    return f"{msg.subject}\n{msg.snippet}\n{msg.body}".lower()


def _sender_domain(sender: str) -> str:
    match = re.search(r"@([\w.-]+)", sender)
    return match.group(1).lower() if match else ""


def _guess_company(msg: EmailMessage) -> str:
    domain = _sender_domain(msg.sender)
    name_match = re.match(r'"?([^"<]+)"?\s*<', msg.sender)
    display_name = name_match.group(1).strip() if name_match else ""

    # Some ATS senders repeat the raw email address as the display name
    # (e.g. `"jobs@brassring.com" <jobs@brassring.com>`) -- that is useless
    # as a company name, so treat it the same as having no display name.
    display_name_is_email = "@" in display_name

    if display_name and not display_name_is_email:
        return display_name

    # No usable display name -- try to pull a company out of the subject
    # line before falling back to the sending domain.
    subject_match = re.search(
        r"(?:application to|applying to|application for) "
        r"([A-Z][\w&.,'\-]*(?:\s+[A-Z][\w&.,'\-]*){0,4})",
        msg.subject,
    )
    if subject_match:
        return subject_match.group(1).strip()

    if domain:
        parts = domain.split(".")
        # Strip generic subdomains like "trm.brassring.com" -> "brassring"
        root = parts[-2] if len(parts) >= 2 else parts[0]
        return root.title()
    return "Unknown"


def _guess_position(msg: EmailMessage) -> str:
    # Ordered most-specific first, since the generic "application to/for X"
    # pattern would otherwise grab a company name out of the subject line
    # before more precise job-title patterns get a chance to match the body.
    patterns = [
        r"applying (?:to|for) the (.+?) (?:position|role)",
        r"for the (.+?) (?:position|role)",
        r"your interest in the (.+?) (?:position|role)",
        r"application (?:to|for) (.+?)(?:\.|\n|$)",
    ]
    text = f"{msg.subject}\n{msg.body}"
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        candidate = match.group(1).strip(" .,-")
        words = candidate.lower().split()
        too_short = len(candidate) < 4
        too_long = len(words) > 10
        all_junk = len(words) <= 2 and all(w in _JUNK_POSITION_WORDS for w in words)
        if not (too_short or too_long or all_junk):
            return candidate[:100]
    return msg.subject[:100]


def classify_new_message(msg: EmailMessage) -> Classification:
    """Decide whether an email looks like a fresh job-application receipt."""
    text = _text_of(msg)
    domain = _sender_domain(msg.sender)

    from_known_ats = any(domain.endswith(ats) for ats in ATS_DOMAINS)
    has_confirmation_phrase = any(phrase in text for phrase in CONFIRMATION_PHRASES)

    is_confirmation = has_confirmation_phrase and (from_known_ats or "applying" in text or "application" in text)

    return Classification(
        is_application_confirmation=is_confirmation,
        company=_guess_company(msg) if is_confirmation else "",
        position=_guess_position(msg) if is_confirmation else "",
    )


def classify_update(msg: EmailMessage) -> UpdateClassification:
    """Given a reply in a thread we're already tracking, guess what kind of
    update it is so the Excel status column and Telegram message make sense."""
    text = _text_of(msg)

    if any(phrase in text for phrase in OFFER_PHRASES):
        return UpdateClassification(status="offer")
    if any(phrase in text for phrase in REJECTION_PHRASES):
        return UpdateClassification(status="rejected")
    if any(phrase in text for phrase in INTERVIEW_PHRASES):
        return UpdateClassification(status="interview")
    return UpdateClassification(status="update")
