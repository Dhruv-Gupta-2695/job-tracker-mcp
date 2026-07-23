"""
Lightweight, zero-cost text similarity for the CV "database" feature: when
you paste a new job description, this checks it against every job
description you've already generated a tailored CV for (see
cv_versions.py) and surfaces the closest match if it's similar enough to be
worth reusing instead of generating from scratch. Pure difflib (stdlib) --
no extra API calls, no extra cost, nothing to configure.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from . import cv_versions

# Below this, two job descriptions are considered unrelated. This is a
# blunt instrument (character-sequence overlap, not semantic understanding)
# -- tune upward for stricter/fewer suggestions, downward for looser/more.
SIMILARITY_THRESHOLD = 0.55


@dataclass
class SimilarityMatch:
    version: dict
    score: float


def find_similar(job_description: str, threshold: float = SIMILARITY_THRESHOLD) -> Optional[SimilarityMatch]:
    """Returns the single best-matching earlier version if its similarity
    score clears `threshold`, else None. Compares against each version's
    stored snippet (job_description text, capped at
    cv_versions.MAX_SNIPPET_CHARS), so very long postings are compared on
    their opening portion only -- usually enough to tell "same-ish role"
    apart from "totally different role"."""
    best: Optional[SimilarityMatch] = None
    for version in cv_versions.list_versions():
        snippet = version.get("Job Description Snippet", "")
        if not snippet:
            continue
        score = SequenceMatcher(None, job_description, snippet).ratio()
        if score >= threshold and (best is None or score > best.score):
            best = SimilarityMatch(version=version, score=score)
    return best
