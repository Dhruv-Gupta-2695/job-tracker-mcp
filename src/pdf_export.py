"""
Renders tailored CV / cover letter text to a clean, simple PDF. Uses fpdf2 --
pure Python, no system libraries (no Cairo/Pango/wkhtmltopdf) required, so it
installs cleanly on free hosts like Render without extra buildpacks.

Note on Unicode: fpdf2's built-in core fonts (Helvetica/Times/Courier) only
support latin-1, not full Unicode -- and AI-generated text often contains
typographic characters (em dashes, curly quotes, bullets) that aren't in
latin-1. Rather than bundle a TTF font file just to sidestep this,
_sanitize_for_pdf() maps the common cases to plain ASCII and safely drops
anything else left over, so PDF generation can never crash on unexpected
characters.
"""
from __future__ import annotations

from fpdf import FPDF

MARGIN_MM = 18

_UNICODE_REPLACEMENTS = {
    "‘": "'", "’": "'",   # curly single quotes
    "“": '"', "”": '"',  # curly double quotes
    "–": "-", "—": "-",  # en dash, em dash
    "…": "...",               # ellipsis
    "•": "-",                 # bullet
    " ": " ",                 # non-breaking space
}


def _sanitize_for_pdf(text: str) -> str:
    for unicode_char, ascii_equivalent in _UNICODE_REPLACEMENTS.items():
        text = text.replace(unicode_char, ascii_equivalent)
    # Anything still outside latin-1 (emoji, other scripts, etc.) is dropped
    # rather than left to crash PDF rendering.
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _build_pdf(title: str, body_text: str) -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_margins(MARGIN_MM, MARGIN_MM, MARGIN_MM)
    pdf.set_auto_page_break(auto=True, margin=MARGIN_MM)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 10, _sanitize_for_pdf(title))
    pdf.ln(4)

    pdf.set_font("Helvetica", "", 11)
    for paragraph in body_text.split("\n\n"):
        pdf.multi_cell(0, 6, _sanitize_for_pdf(paragraph.strip()))
        pdf.ln(3)

    return bytes(pdf.output())


def cv_to_pdf(name: str, cv_text: str) -> bytes:
    return _build_pdf(f"{name} - CV", cv_text)


def cover_letter_to_pdf(name: str, company: str, cover_letter_text: str) -> bytes:
    return _build_pdf(f"{name} - Cover Letter ({company})", cover_letter_text)
