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
    "successfully submitted", "application has been submitted",
    "application submitted", "your application was sent",
]

# Job boards that relay many different employers' applications through ONE
# sender address (every Wellfound email comes from team@hi.wellfound.com,
# every LinkedIn one from jobs-noreply@linkedin.com). The sender therefore
# says nothing about which employer an email is about.
SHARED_BOARD_DOMAINS = (
    "wellfound.com", "linkedin.com", "indeed.com", "glassdoor.com",
    "naukri.com", "instahyre.com", "cutshort.io", "hirist.com",
    "foundit.in", "monster.com", "ziprecruiter.com", "otta.com",
)

# Strong, unambiguous offer wording only -- "congratulations" alone is far
# too common in confirmation emails to treat as a status signal here.
_STRONG_OFFER_PHRASES = [
    "pleased to offer", "job offer", "offer letter", "excited to offer", "extend an offer",
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

# Mid-process progress emails ("you're through to the assignment stage").
# These are updates on an application, not a fresh application receipt, so
# they must not create a new "applied" row on their own.
STAGE_PROGRESS_PHRASES = [
    "you're through to", "you are through to", "you've been shortlisted",
    "you have been shortlisted", "shortlisted for", "advance to the next",
    "moved to the next", "assignment stage", "next round",
]

# Subjects too generic to be a job title -- better to say "Unknown position"
# than to store these as if they were one.
_GENERIC_SUBJECTS = {
    "job application", "application", "your application", "application received",
    "application confirmation", "thank you for applying", "thanks for applying",
}

UNKNOWN_POSITION = "Unknown position"


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


def is_shared_board_sender(sender: str) -> bool:
    domain = _sender_domain(sender)
    return any(domain == d or domain.endswith("." + d) for d in SHARED_BOARD_DOMAINS)


def is_status_update_email(msg: EmailMessage) -> bool:
    """True for an outcome/progress email about an application (rejection,
    offer, "An update from X") as opposed to a fresh application receipt.
    Rejection wording is only looked for near the top of the email, since
    confirmations sometimes carry disclaimers further down."""
    subject = msg.subject.lower()
    if re.match(r"\s*(?:re:\s*)?(?:an\s+)?update\s+(?:from|on|about|regarding)\b", subject):
        return True
    head = f"{msg.subject}\n{msg.snippet}\n{msg.body[:600]}".lower()
    if any(p in head for p in REJECTION_PHRASES):
        return True
    return any(p in head for p in _STRONG_OFFER_PHRASES)


def company_hint_from_update(msg: EmailMessage) -> str:
    """Best guess at which employer an update email is about, so it can be
    matched to an already-tracked application even though job boards send
    updates as a brand-new thread, not a reply to the original."""
    subject = msg.subject.strip()
    match = re.search(r"update\s+(?:from|on|about|regarding)\s+(.+?)\s*$", subject, re.IGNORECASE)
    if match:
        hint = re.sub(r"\s+application$", "", match.group(1).strip(), flags=re.IGNORECASE)
        if hint and not hint.lower().startswith(("your", "my", "the application")):
            return hint
    body = re.sub(r"\s+", " ", msg.body)
    match = re.search(r"(?:position|role) at ([A-Z][^.,\n]{1,60}?)[.,]", body)
    if match:
        return match.group(1).strip()
    return ""


_BOARD_SUBJECT_COMPANY = [
    r"^\s*application to (.+?) successfully submitted",
    r"application was sent to (.+?)\s*$",
    r"^\s*(.+?) was accepted!?\s*$",
]


def _company_from_board_subject(subject: str) -> str:
    for pattern in _BOARD_SUBJECT_COMPANY:
        match = re.search(pattern, subject, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _position_before_company(body: str, company: str) -> str:
    """Wellfound-style layout: the job title sits on the line just above
    "<Company> · <location>". Works for plain text and for HTML that was
    flattened to spaces, because the title can't contain sentence-ending
    punctuation and so always starts right after the previous sentence."""
    pattern = r"(?:^|[.!?]\s+|\n\s*)([^.!?\n]{3,80}?)\s*\n?\s*" + re.escape(company) + r"\s*[·•|]"
    match = re.search(pattern, body)
    return match.group(1).strip() if match else ""


def _position_after_sent_heading(body: str, company: str) -> str:
    """LinkedIn layout:
        Your application was sent to <Company>
        <Job title>                      (often followed by a tracking URL)
        <Company> · <City> (On-site)
        Applied on <date>
    Takes whatever sits between the heading and the second mention of the
    company, minus URLs. Works whether the email arrives as plain text or as
    HTML flattened to spaces."""
    co = re.escape(company)
    match = re.search(
        r"application was sent to\s+" + co + r"[:\s]+(.+?)\s+(?:" + co + r"\s*(?:[·•|]|&middot;|&#183;)|applied on\b)",
        body, re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    text = re.sub(r"https?://\S+", " ", match.group(1))
    text = re.sub(r"\s+", " ", text).strip(" :-")
    return text if 3 <= len(text) <= 100 else ""


def _looks_like_placeholder(text: str) -> bool:
    """Unfilled template text such as "[Position Name]" or "{{role}}"."""
    return bool(re.search(r"[\[\]{}<>]", text))


def _company_from_subject(subject: str) -> str:
    """Job-board relays (Wellfound etc.) put the real employer in the subject
    ("Drona Pay was accepted!") while the sender is a recruiter's name."""
    match = re.match(r"^\s*(.+?)\s+was accepted!?\s*$", subject, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _guess_company(msg: EmailMessage) -> str:
    domain = _sender_domain(msg.sender)
    name_match = re.match(r'"?([^"<]+)"?\s*<', msg.sender)
    display_name = name_match.group(1).strip() if name_match else ""

    # Some ATS senders repeat the raw email address as the display name
    # (e.g. `"jobs@brassring.com" <jobs@brassring.com>`) -- that is useless
    # as a company name, so treat it the same as having no display name.
    display_name_is_email = "@" in display_name

    # Job boards send every employer's emails from one address, so the
    # sender is useless -- the employer is named in the subject instead.
    if is_shared_board_sender(msg.sender):
        from_board_subject = _company_from_board_subject(msg.subject)
        if from_board_subject:
            return from_board_subject

    # "Satish Kashyap via Wellfound" is a person relaying through a job
    # board, not the employer. Prefer the employer named in the subject; if
    # there isn't one, at least drop the "via ..." tail.
    if " via " in display_name.lower():
        from_subject = _company_from_subject(msg.subject)
        if from_subject:
            return from_subject
        display_name = re.split(r"\s+via\s+", display_name, flags=re.IGNORECASE)[0].strip()

    if display_name and not display_name_is_email and not _looks_like_placeholder(display_name):
        # "Teachmint Hiring team" -> "Teachmint"
        trimmed = re.sub(
            r"\s+(?:hiring|recruiting|recruitment|talent(?: acquisition)?|careers?|hr|people)"
            r"(?:\s+(?:team|department|ops))?\s*$",
            "", display_name, flags=re.IGNORECASE,
        ).strip()
        return trimmed or display_name

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
    def acceptable(candidate: str) -> bool:
        words = candidate.lower().split()
        too_short = len(candidate) < 4
        too_long = len(words) > 10
        all_junk = len(words) <= 2 and all(w in _JUNK_POSITION_WORDS for w in words)
        status_text = bool(re.search(r"successfully|submitted|received|accepted", candidate, re.IGNORECASE))
        return not (too_short or too_long or all_junk or status_text or _looks_like_placeholder(candidate))

    # Job-board layout (Wellfound): title sits just above "<Company> · <city>".
    if is_shared_board_sender(msg.sender):
        board_company = _company_from_board_subject(msg.subject)
        if board_company:
            for finder in (_position_after_sent_heading, _position_before_company):
                candidate = finder(msg.body, board_company)
                if candidate and acceptable(candidate):
                    return candidate[:100]

    patterns = [
        r"for the (?:role|position) of (.+?)(?:\s+(?:through|at|with|via|on|in)\b|[.,\n]|$)",
        r"applying (?:to|for) the (.+?) (?:position|role)",
        r"application (?:to|for) the (.+?) (?:position|role)",
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
        if acceptable(candidate):
            return candidate[:100]

    # Nothing matched cleanly. Falling back to the raw subject is only
    # useful when it reads like a job title -- not for "X was accepted!",
    # generic "Job Application", unfilled "[Position Name]" placeholders, or
    # progress messages.
    subject = msg.subject.strip()
    lowered = subject.lower()
    if (
        not subject
        or _looks_like_placeholder(subject)
        or lowered.strip(" !.") in _GENERIC_SUBJECTS
        or re.search(r"was accepted|successfully submitted|application was sent to", lowered)
        or any(p in lowered for p in STAGE_PROGRESS_PHRASES)
        or not acceptable(subject)
    ):
        return UNKNOWN_POSITION
    return subject[:100]


def classify_new_message(msg: EmailMessage) -> Classification:
    """Decide whether an email looks like a fresh job-application receipt."""
    text = _text_of(msg)
    domain = _sender_domain(msg.sender)

    from_known_ats = any(domain.endswith(ats) for ats in ATS_DOMAINS)
    has_confirmation_phrase = any(phrase in text for phrase in CONFIRMATION_PHRASES)

    is_confirmation = has_confirmation_phrase and (from_known_ats or "applying" in text or "application" in text)

    # A progress message ("you're through to the assignment stage") often
    # also contains "thank you for your interest in", but it is an update on
    # an existing application -- never a new "applied" row.
    if any(p in text for p in STAGE_PROGRESS_PHRASES):
        is_confirmation = False

    # Likewise a rejection / offer / "An update from X" email is an outcome,
    # not a new application -- even though it often says "your application
    # to" and so also matches the confirmation phrases.
    if is_status_update_email(msg):
        is_confirmation = False

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
