"""
Structured CV model. The CV is parsed ONCE (at upload) into a fixed JSON shape;
tailoring then only rewrites the wording of allowed text fields. Everything that
defines the layout (sections, entries, titles, companies, dates, links, number of
bullets, order) is copied from the original, never from the AI, so the tailored
CV always has the same format and structure and cannot invent employers/dates.

Item types inside a section:
  paragraph {text}
  role      {title, org, note, right, bullets[]}   (experience entry; right = dates)
  edu       {title, org, note, right, bullets[]}   (same, org shown plain)
  bullet    {lead, tagline, link_label, link_url, text}
  labeled   {lead, text}                            (bold label then text, e.g. skills)
"""
from __future__ import annotations

import hashlib
import io
import json
import re

ITEM_TYPES = {"paragraph", "role", "edu", "bullet", "labeled"}

# Fields the AI may reword, per item type.
EDITABLE = {
    "paragraph": ("text",),
    "bullet": ("text",),
    "labeled": ("text",),
    "role": ("bullets",),
    "edu": (),
}
# Tailored text may be at most this much longer than the original (keeps 1 page).
MAX_GROWTH = 1.12
MAX_GROWTH_EXTRA = 25  # characters of slack on very short fields


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:16]


# ---------------------------------------------------------------- links ----

def extract_links(pdf_bytes: bytes) -> list[str]:
    """Hyperlink targets of a PDF in reading order (top to bottom, left to right)."""
    from pypdf import PdfReader
    found = []
    reader = PdfReader(io.BytesIO(pdf_bytes))
    for pno, page in enumerate(reader.pages):
        for annot in page.get("/Annots") or []:
            try:
                a = annot.get_object()
                if a.get("/Subtype") != "/Link":
                    continue
                action = a.get("/A")
                uri = action.get_object().get("/URI") if action else None
                if not uri:
                    continue
                x0, y0, x1, y1 = [float(v) for v in a["/Rect"]]
                found.append((pno, -round(max(y0, y1) / 4), min(x0, x1), str(uri)))
            except Exception:  # noqa: BLE001 - a malformed annotation must not break the upload
                continue
    found.sort()
    out, seen = [], set()
    for _, _, _, uri in found:
        # Link annotations are often split in two pieces per link: keep one per consecutive repeat.
        if out and out[-1] == uri:
            continue
        out.append(uri)
    return out


# --------------------------------------------------------------- prompts ---

SHAPE = """{
 "name": "FULL NAME",
 "headline": "tagline under the name or empty",
 "contact": [{"text": "Mumbai, India"}, {"text": "LinkedIn", "url": "https://..."}],
 "sections": [
  {"title": "Section title", "items": [
    {"type": "paragraph", "text": "..."},
    {"type": "role", "title": "Job title", "org": "Company", "note": "short parenthetical or empty", "right": "Aug 2024 - Apr 2026", "bullets": ["...", "..."]},
    {"type": "bullet", "lead": "Bold lead-in", "tagline": "text after the dash or empty", "link_label": "Live app or empty", "link_url": "https://... or empty", "text": "..."},
    {"type": "labeled", "lead": "Skills label:", "text": "..."},
    {"type": "edu", "title": "Degree", "org": "Institution", "note": "", "right": "2013 - 2017", "bullets": []}
  ]}
 ]
}"""

PARSE_PROMPT = (
    "Convert the CV below into JSON with EXACTLY this shape (no extra keys):\n" + SHAPE + "\n\n"
    "Rules: copy every word exactly as written, do not summarise, reword, reorder or drop anything. "
    "Keep sections and entries in their original order. Use type 'role' for experience entries, 'edu' for "
    "education entries, 'bullet' for project-style bullets, 'labeled' for 'Label: text' lines (skills, "
    "one-line intros), 'paragraph' for plain paragraphs. Dates go in 'right'. A hyperlink's visible text "
    "goes in link_label / contact text and its address in link_url / url, taken from the LINKS list in "
    "reading order (the first link-looking text gets the first unused link, and so on); never invent a URL. "
    "Output only the JSON.\n\nLINKS (reading order):\n{links}\n\nCV TEXT:\n{cv_text}"
)

TAILOR_PROMPT = (
    "You are an expert resume writer. Below is a candidate's CV as JSON and a JOB DESCRIPTION. "
    "Return the SAME JSON with only the wording tailored to the job.\n\n"
    "Hard rules:\n"
    "- Keep every key, section, item, order, title, org, dates, links and the number of bullets exactly as is. "
    "Only reword these fields: paragraph.text, bullet.text, labeled.text, and each string in role.bullets.\n"
    "- Never invent employers, titles, dates, tools, skills, numbers or achievements that are not in the CV. "
    "Keep every figure exactly as given.\n"
    "- Mirror the job description's keywords only where they are truthfully supported by the CV.\n"
    "- Foreground the most relevant points inside each field (lead with the relevant part). "
    "For skills lines you may reorder the existing skills so the most relevant come first, but do not add new ones.\n"
    "- Keep each rewritten field about the same length as the original (never more than 10% longer), "
    "so the CV stays on one page.\n"
    "- Output only the JSON.\n\n"
    "CV JSON:\n{core_cv_json}\n\nJOB DESCRIPTION:\n{job_description}"
)


def parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The AI did not return JSON.")
    return json.loads(raw[start:end + 1])


# ------------------------------------------------------------- validation --

def _s(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def normalize(cv: dict) -> dict:
    """Coerces an AI-produced CV into the exact schema (raises ValueError if unusable)."""
    if not isinstance(cv, dict) or not _s(cv.get("name")):
        raise ValueError("CV has no name.")
    out = {"name": _s(cv["name"]), "headline": _s(cv.get("headline")), "contact": [], "sections": []}
    for c in cv.get("contact") or []:
        if isinstance(c, dict) and _s(c.get("text")):
            out["contact"].append({"text": _s(c["text"]), "url": _s(c.get("url"))})
    for sec in cv.get("sections") or []:
        if not isinstance(sec, dict) or not _s(sec.get("title")):
            continue
        items = []
        for it in sec.get("items") or []:
            if not isinstance(it, dict) or it.get("type") not in ITEM_TYPES:
                continue
            t = it["type"]
            if t == "paragraph":
                items.append({"type": t, "text": _s(it.get("text"))})
            elif t in ("role", "edu"):
                items.append({
                    "type": t, "title": _s(it.get("title")), "org": _s(it.get("org")),
                    "note": _s(it.get("note")), "right": _s(it.get("right")),
                    "bullets": [_s(b) for b in (it.get("bullets") or []) if _s(b)],
                })
            elif t == "bullet":
                items.append({
                    "type": t, "lead": _s(it.get("lead")), "tagline": _s(it.get("tagline")),
                    "link_label": _s(it.get("link_label")), "link_url": _s(it.get("link_url")),
                    "text": _s(it.get("text")),
                })
            else:
                items.append({"type": t, "lead": _s(it.get("lead")), "text": _s(it.get("text"))})
        if items:
            out["sections"].append({"title": _s(sec["title"]), "items": items})
    if not out["sections"]:
        raise ValueError("CV has no sections.")
    return out


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _all_strings(cv: dict):
    yield cv["name"]
    yield cv["headline"]
    for c in cv["contact"]:
        yield c["text"]
    for sec in cv["sections"]:
        yield sec["title"]
        for it in sec["items"]:
            for k, v in it.items():
                if k in ("type", "link_url"):
                    continue
                if isinstance(v, list):
                    yield from v
                else:
                    yield v


def check_parse_fidelity(cv: dict, source_text: str) -> None:
    """The parsed CV must contain (almost) exactly the words of the source, so a
    lossy or hallucinated parse is rejected instead of silently used."""
    src = _words(source_text)
    got = _words(" ".join(_all_strings(cv)))
    if not src or not got:
        raise ValueError("Could not read the CV structure.")
    src_set, got_set = set(src), set(got)
    missing = len([w for w in src if w not in got_set]) / len(src)
    extra = len([w for w in got if w not in src_set]) / len(got)
    if missing > 0.06 or extra > 0.06:
        raise ValueError("The AI could not reproduce your CV faithfully (parts were lost or changed). Try again.")


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d[\d,.]*", text))


def _grow_ok(old: str, new: str) -> bool:
    return len(new) <= len(old) * MAX_GROWTH + MAX_GROWTH_EXTRA


def _field_ok(old: str, new: str, allowed_numbers: set[str]) -> bool:
    if not new or not _grow_ok(old, new):
        return False
    return _numbers(new) <= allowed_numbers


def _skills_ok(old: str, new: str) -> bool:
    """Skills lines: same skills, any order; nothing added."""
    olds = {t.strip().lower() for t in old.split(",") if t.strip()}
    news = [t.strip().lower() for t in new.split(",") if t.strip()]
    return bool(news) and set(news) <= olds and len(news) >= max(1, len(olds) - 1) and _grow_ok(old, new)


def merge_tailored(original: dict, tailored: dict) -> tuple[dict, dict]:
    """Returns (final_cv, stats). The final CV is a copy of `original` in which only
    editable fields that pass the checks are replaced by the tailored wording."""
    final = json.loads(json.dumps(original))
    stats = {"changed": 0, "kept_original": 0}
    try:
        tailored = normalize(tailored)
    except ValueError:
        return final, {"changed": 0, "kept_original": -1}
    allowed_numbers = _numbers(" ".join(_all_strings(original)))

    t_sections = {s["title"].strip().lower(): s for s in tailored["sections"]}
    for sec in final["sections"]:
        ts = t_sections.get(sec["title"].strip().lower())
        if not ts or len(ts["items"]) != len(sec["items"]):
            continue
        for it, tit in zip(sec["items"], ts["items"]):
            if tit["type"] != it["type"]:
                continue
            for field in EDITABLE[it["type"]]:
                if field == "bullets":
                    if len(tit["bullets"]) != len(it["bullets"]):
                        stats["kept_original"] += len(it["bullets"])
                        continue
                    for i, (old, new) in enumerate(zip(it["bullets"], tit["bullets"])):
                        if new != old and _field_ok(old, new, allowed_numbers):
                            it["bullets"][i] = new
                            stats["changed"] += 1
                        elif new != old:
                            stats["kept_original"] += 1
                else:
                    old, new = it[field], tit[field]
                    if new == old:
                        continue
                    ok = _skills_ok(old, new) if it["type"] == "labeled" and "," in old else _field_ok(old, new, allowed_numbers)
                    if ok:
                        it[field] = new
                        stats["changed"] += 1
                    else:
                        stats["kept_original"] += 1
    return final, stats


# ------------------------------------------------------------ plain text ---

def to_text(cv: dict) -> str:
    """Readable plain-text form (shown in the UI and used for similarity)."""
    lines = [cv["name"], cv["headline"], " | ".join(c["text"] for c in cv["contact"]), ""]
    for sec in cv["sections"]:
        lines.append(sec["title"].upper())
        for it in sec["items"]:
            t = it["type"]
            if t == "paragraph":
                lines.append(it["text"])
            elif t in ("role", "edu"):
                head = it["title"] + (f" - {it['org']}" if it["org"] else "") + (f" ({it['note']})" if it["note"] else "")
                lines.append(f"{head}    {it['right']}".rstrip())
                lines += [f"- {b}" for b in it["bullets"]]
            elif t == "bullet":
                tag = f" - {it['tagline']}" if it["tagline"] else ""
                lines.append(f"- {it['lead']}{tag}: {it['text']}" if tag else f"- {it['lead']} {it['text']}")
            else:
                lines.append(f"{it['lead']} {it['text']}")
        lines.append("")
    return "\n".join(lines).strip()
