"""
Renders a structured CV (see cv_struct.py) to a PDF that reproduces the
design of the user's core CV: Carlito fonts, navy headings with a rule,
right-aligned dates, bulleted entries, clickable links. Fits on one A4 page by
shrinking spacing/fonts slightly (down to ~88%); if it still does not fit it
flows onto a second page. Pure fpdf2 + bundled fonts, no system libraries.
"""
from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
NAVY = (31, 58, 95)
GREY = (85, 85, 85)
BLACK = (0, 0, 0)

PAGE_W, PAGE_H = 595.28, 841.89
LEFT, RIGHT = 35.1, 560.25
BULLET_X, TEXT_X = 40.1, 50.1
BOTTOM = PAGE_H - 14
SCALES = (1.0, 0.97, 0.94, 0.91, 0.88)


class _Overflow(Exception):
    pass


class _Doc:
    def __init__(self, k: float, allow_two_pages: bool):
        self.k = k
        self.allow2 = allow_two_pages
        self.pdf = FPDF(unit="pt", format="A4")
        self.pdf.set_auto_page_break(False)
        self.pdf.set_margins(0, 0, 0)
        for style, fname in (("", "Regular"), ("B", "Bold"), ("I", "Italic"), ("BI", "BoldItalic")):
            self.pdf.add_font("Carlito", style, str(FONT_DIR / f"Carlito-{fname}.ttf"))
        self.pdf.add_page()
        self.y = 26.0 * k
        self.body = 9.5 * k
        self.pitch = 12.45 * k

    # ---- low level -------------------------------------------------------
    def _font(self, style: str, size: float):
        self.pdf.set_font("Carlito", style, size)

    def _width(self, text: str, style: str, size: float) -> float:
        self._font(style, size)
        return self.pdf.get_string_width(text)

    def _ensure(self, height: float):
        if self.y + height <= BOTTOM:
            return
        if not self.allow2 or self.pdf.page >= 2:
            raise _Overflow()
        self.pdf.add_page()
        self.y = 35.0

    def _draw(self, x, y, text, style, size, color, url=None):
        self._font(style, size)
        self.pdf.set_text_color(*color)
        base = y + 0.952 * size
        self.pdf.text(x, base, text)
        w = self.pdf.get_string_width(text)
        if url:
            self.pdf.set_draw_color(*color)
            self.pdf.set_line_width(0.5 * self.k)
            self.pdf.line(x, base + 1.2, x + w, base + 1.2)
            self.pdf.link(x, y, w, size * 1.2, url)
        return w

    def flow(self, segs, x0, x1, first_x=None):
        """Word-wraps styled segments [(text, style, color, url)]. Returns nothing;
        advances self.y by the lines used."""
        size = self.body
        words = []  # (text, style, color, url)
        for text, style, color, url in segs:
            parts = [text] if url else text.split(" ")
            for i, p in enumerate(parts):
                tok = p + (" " if i < len(parts) - 1 else "")
                if tok:
                    words.append((tok, style, color, url))
        # merge pieces that were split mid-token across segments is not needed: segments end at spaces
        x = first_x if first_x is not None else x0
        self._ensure(self.pitch)
        line_y = self.y
        for tok, style, color, url in words:
            w = self._width(tok, style, size)
            vis = self._width(tok.rstrip(" "), style, size)
            if x + vis > x1 + 0.01 and x > (first_x if first_x is not None else x0) + 0.01 and tok.strip():
                self.y += self.pitch
                self._ensure(self.pitch)
                line_y = self.y
                x = x0
            if tok.strip():
                self._draw(x, line_y, tok.rstrip(" "), style, size, color, url)
            x += w
        self.y += self.pitch

    # ---- blocks ----------------------------------------------------------
    def centered(self, segs, size, y):
        total = sum(self._width(t, s, size) for t, s, _, _ in segs)
        x = (PAGE_W - total) / 2
        for t, s, c, u in segs:
            x += self._draw(x, y, t, s, size, c, u)

    def header(self, cv):
        k = self.k
        self.centered([(cv["name"], "B", NAVY, None)], 16 * k, self.y)
        self.y += 20.7 * k
        if cv.get("headline"):
            self.centered([(cv["headline"].replace("|", " | ").replace("  ", " ").replace(" | ", "  |  "), "B", GREY, None)], 10 * k, self.y)
        self.y += 14.0 * k
        segs = []
        for i, c in enumerate(cv.get("contact", [])):
            if i:
                segs.append(("  |  ", "", GREY, None))
            if c.get("url"):
                segs.append((c["text"], "", NAVY, c["url"]))
            else:
                segs.append((c["text"], "", BLACK, None))
        if segs:
            self.centered(segs, self.body, self.y)
        self.y += 17.3 * k

    def section(self, title):
        k = self.k
        self._ensure(32 * k)
        size = 11 * k
        self._draw(LEFT, self.y, title.upper(), "B", size, NAVY)
        rule_y = self.y + 14.8 * k
        self.pdf.set_draw_color(*NAVY)
        self.pdf.set_line_width(0.9 * k)
        self.pdf.line(LEFT, rule_y, RIGHT + 0.0, rule_y)
        self.y += 18.7 * k

    def right_text(self, text, y):
        w = self._width(text, "I", self.body)
        self._draw(RIGHT - w, y, text, "I", self.body, GREY)

    def _bullet_mark(self, y):
        self._draw(BULLET_X, y, "•", "", self.body, BLACK)

    def bullets_role(self, it, plain_org=False):
        self._ensure(self.pitch + 6)
        segs = [(it["title"], "B", BLACK, None)]
        if it.get("org"):
            segs.append((" — " + it["org"], "" if plain_org else "B", BLACK if plain_org else NAVY, None))
        if it.get("note"):
            segs.append(("  (" + it["note"] + ")", "I", GREY, None))
        y0 = self.y
        self.flow(segs, LEFT, RIGHT - 95 * self.k)
        if it.get("right"):
            self.right_text(it["right"], y0)
        for b in it.get("bullets", []):
            self._ensure(self.pitch)
            self._bullet_mark(self.y)
            self.flow([(b, "", BLACK, None)], TEXT_X, RIGHT)
        self.y += 2.0 * self.k

    def paragraph(self, text):
        self.flow([(text, "", BLACK, None)], LEFT, RIGHT)

    def labeled(self, it):
        segs = [(it["lead"] + " ", "B", BLACK, None), (it["text"], "", BLACK, None)]
        self.flow(segs, LEFT, RIGHT)

    def bullet(self, it):
        self._ensure(self.pitch)
        self._bullet_mark(self.y)
        segs = [(it["lead"], "B", BLACK, None)]
        text_after = it.get("text", "")
        if it.get("tagline"):
            segs.append((" — " + it["tagline"] + " ", "", BLACK, None))
            if it.get("link_url"):
                segs.append((" ", "", BLACK, None))
        else:
            segs[0] = (it["lead"] + " ", "B", BLACK, None)
        if it.get("link_url"):
            segs.append((it.get("link_label") or "Link", "", NAVY, it["link_url"]))
            segs.append((": ", "", BLACK, None))
        segs.append((text_after, "", BLACK, None))
        self.flow(segs, TEXT_X, RIGHT)

    def render(self, cv):
        self.header(cv)
        for sec in cv["sections"]:
            self.section(sec["title"])
            for it in sec["items"]:
                t = it["type"]
                if t == "paragraph":
                    self.paragraph(it["text"])
                elif t == "role":
                    self.bullets_role(it)
                elif t == "edu":
                    self.bullets_role(it, plain_org=True)
                elif t == "bullet":
                    self.bullet(it)
                elif t == "labeled":
                    self.labeled(it)
            self.y += 4.0 * self.k - 0.0
        return self


def render_cv(cv: dict) -> tuple[bytes, int]:
    """Returns (pdf_bytes, page_count). One page whenever it can fit, else two."""
    for k in SCALES:
        try:
            doc = _Doc(k, allow_two_pages=False).render(cv)
            return bytes(doc.pdf.output()), 1
        except _Overflow:
            continue
    doc = _Doc(1.0, allow_two_pages=True).render(cv)
    return bytes(doc.pdf.output()), doc.pdf.page


# ------------------------------------------------------------ cover letter --

CL_LEFT, CL_RIGHT = 55.1, 540.25
LETTER_FILLER = ("hi", "hello", "dear", "regards", "best regards", "sincerely", "kind regards", "warm regards", "thanks")


def clean_letter_body(text: str, name: str = "") -> list[str]:
    """Model output -> list of body paragraphs (drops any greeting/sign-off the model added)."""
    text = text.replace("—", ",").replace("–", "-").replace("**", "")
    paras = [" ".join(p.split()) for p in text.replace("\r", "").split("\n\n")]
    out = []
    for p in paras:
        low = p.lower().strip(" ,.!")
        if not p or low in LETTER_FILLER or low == name.lower():
            continue
        if low.startswith(("dear ", "hi ", "hello ")) and len(p) < 40:
            continue
        # a trailing "Regards,\nName" glued into the last paragraph
        for closer in ("Best regards,", "Regards,", "Sincerely,", "Kind regards,"):
            if closer in p:
                p = p.split(closer)[0].strip()
        if p:
            out.append(p)
    return out


def render_cover_letter(name: str, contact: list[dict], paragraphs: list[str], date_str: str) -> tuple[bytes, int]:
    """Cover letter in the user's own layout: navy name, grey contact line with links, a navy rule,
    date, 'Hi,', short paragraphs, 'Regards,' and bold name. Carlito 11pt."""
    doc = _Doc(1.0, allow_two_pages=True)
    doc.body, doc.pitch = 11.0, 14.7
    doc.y = 50.0
    doc._draw(CL_LEFT, doc.y, name.upper(), "B", 16, NAVY)
    doc.y = 71.3
    x = CL_LEFT
    for i, c in enumerate(contact):
        if i:
            x += doc._draw(x, doc.y, "  |  ", "", 10, GREY)
        if c.get("url"):
            x += doc._draw(x, doc.y, c["text"], "", 10, NAVY, c["url"])
        else:
            x += doc._draw(x, doc.y, c["text"], "", 10, GREY)
    doc.pdf.set_draw_color(*NAVY)
    doc.pdf.set_line_width(0.9)
    doc.pdf.line(CL_LEFT, 88.1, CL_RIGHT, 88.1)
    doc.y = 105.3
    gap = 10.7

    def para(text, bold=False):
        doc.flow([(text, "B" if bold else "", BLACK, None)], CL_LEFT, CL_RIGHT)
        doc.y += gap

    para(date_str)
    para("Hi,")
    for p in paragraphs:
        para(p)
    doc.pdf.set_font("Carlito", "", 11)
    doc.flow([("Regards,", "", BLACK, None)], CL_LEFT, CL_RIGHT)
    doc.y += 4.6 - 0.0
    doc.flow([(name, "B", BLACK, None)], CL_LEFT, CL_RIGHT)
    return bytes(doc.pdf.output()), doc.pdf.page
