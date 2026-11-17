#!/usr/bin/env python3
"""Build CVs and cover letters as black text A4 PDFs.

  .venv/bin/python build_docs.py cv     content.json  out.pdf
  .venv/bin/python build_docs.py letter content.json  out.pdf

Use the venv, not bare python3: the Homebrew Python is externally managed and
has no reportlab.

CV JSON:     {name, tagline, contact[], summary, sections[]}
             section = {type: "experience"|"skills", title, items[]}
             experience item = {role, org, meta, bullets[]}   bullets may use <b>Label:</b>
             skills item     = ["Label", "value, value, value"]
LETTER JSON: {sender[], date, recipient[], subject, salutation, paragraphs[], closing, signature}

Enforced: black only, no tables, qpdf inflate above 8 KB. Em/en dashes and double
hyphens are rewritten (a hyphen in a range, a comma elsewhere). Nothing refuses a build.
"""

import json
import math
import os
import re
import subprocess
import sys

from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, HRFlowable, PageTemplate,
                                Paragraph, SimpleDocTemplate, Spacer)

BLACK = "#000000"

# A single-page CV that fills less than this fraction of the frame reads as a
# thin candidate with half a page of white space (21 September 2026: the thin
# CVs sat at 0.68 to 0.80, the fullest at 0.94). Since November 2026 this is a
# warning, not a refusal: a student with one internship, or anyone whose
# profile genuinely has nothing more to add, still gets a CV, and the writer
# decides whether there is real content left to add. Nothing in this file
# stops a build any more.
CV_FILL_FLOOR = 0.90


def _p(name, font, size, lead, **kw):
    return ParagraphStyle(name, fontName=font, fontSize=size, leading=lead,
                          textColor=BLACK, **kw)


CV = {
    "name":    _p("name", "Helvetica-Bold", 17, 20, spaceAfter=1),
    "tagline": _p("tagline", "Helvetica", 10.5, 13, spaceAfter=3),
    "contact": _p("contact", "Helvetica", 8.7, 11),
    "section": _p("section", "Helvetica-Bold", 10.5, 12.5, spaceBefore=8, spaceAfter=2),
    "role":    _p("role", "Helvetica-Bold", 9.6, 12, spaceBefore=4),
    "meta":    _p("meta", "Helvetica-Oblique", 8.5, 11, spaceAfter=1),
    "bullet":  _p("bullet", "Helvetica", 8.9, 11.6, leftIndent=9, bulletIndent=0,
                  spaceAfter=1.6, alignment=TA_JUSTIFY),
    "body":    _p("body", "Helvetica", 8.9, 11.6, spaceAfter=2, alignment=TA_JUSTIFY),
    "skill":   _p("skill", "Helvetica", 8.9, 11.4, leftIndent=9, firstLineIndent=-9,
                  spaceAfter=1.6),
}

LT = {
    "name":    _p("lname", "Helvetica-Bold", 13, 16),
    "meta":    _p("lmeta", "Helvetica", 8.8, 11.5),
    "subject": _p("lsubj", "Helvetica-Bold", 9.5, 12.5),
    "body":    _p("lbody", "Helvetica", 9.3, 13.2, spaceAfter=7, alignment=TA_JUSTIFY),
}


# Dashes read as machine-written on a CV, so none reach the page. This used to
# refuse the build; now it rewrites them the way a careful writer would and
# says what it changed: a range of numbers or dates gets a hyphen ("2019-2021"),
# any other dash becomes a comma.
_RANGE = re.compile(r"(?<=[0-9])\s*(?:—|–|--)\s*(?=[0-9])")     # 2019 – 2021
_JOINED = re.compile(r"(?<=\w)(?:—|–|--)(?=\w)")                  # Q1–Q3, Berlin–Munich
_DASH = re.compile(r"\s*(?:—|–|--)\s*")


def _undash(text):
    text = _JOINED.sub("-", _RANGE.sub("-", text))
    return _DASH.sub(", ", text)


_CV0, _LT0 = dict(CV), dict(LT)


# Helvetica, the built-in PDF font, only has Western European letters: a
# Polish, Turkish, Czech, Greek, Cyrillic or Arabic name, employer or city came
# out as black boxes, with no error (17 November 2026). A CV with only those
# Western letters keeps Helvetica exactly as before. Any other letter switches
# the whole document to a system font that has them: Arial on macOS and
# Windows, DejaVu or Liberation Sans on Linux.
_UNICODE_FONTS = [
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
     "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
     "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"),
    tuple(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", f)
          for f in ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf")),
    tuple("/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % x
          for x in ("", "-Bold", "-Oblique", "-BoldOblique")),
    tuple("/usr/share/fonts/truetype/liberation/LiberationSans-%s.ttf" % x
          for x in ("Regular", "Bold", "Italic", "BoldItalic")),
    # Chinese, Japanese, Korean and Devanagari: one file, no bold or italic.
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf",) * 4,
    tuple(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "ARIALUNI.TTF") for _ in range(4)),
]
_RTL = re.compile("[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufeff]")
# Scripts whose letters join or reorder (Devanagari, Bengali, Tamil, Thai...):
# the letters show, but not joined as the script needs.
_SHAPED = re.compile("[\u0900-\u0dff\u0e00-\u0e7f\u1000-\u109f]")
_BASE = {"Helvetica": "", "Helvetica-Bold": "-Bold", "Helvetica-Oblique": "-Italic"}


def _all_text(payload):
    return json.dumps(payload, ensure_ascii=False)


def _outside_latin1(text):
    out = []
    for ch in text:
        try:
            ch.encode("cp1252")
        except UnicodeEncodeError:
            if ch not in out:
                out.append(ch)
    return out


def _unicode_font(chars):
    """Register the first system font family that has every one of chars (or,
    failing that, the one that has the most). Returns (family, letters it
    still lacks), or (None, chars) when there is no such font at all."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.fonts import addMapping
    best = None
    for n, paths in enumerate(_UNICODE_FONTS):
        if not all(os.path.isfile(x) for x in paths):
            continue
        try:
            faces = [TTFont("CVSans%d%s" % (n, suffix), path)
                     for suffix, path in zip(("", "-Bold", "-Italic", "-BoldItalic"), paths)]
        except Exception:                                    # noqa: BLE001
            continue
        have = faces[0].face.charToGlyph
        lack = [ch for ch in chars if ord(ch) not in have and not ch.isspace()]
        if best is None or len(lack) < len(best[2]):
            best = (n, faces, lack)
        if not lack:
            break
    if best is None:
        return None, list(chars)
    n, faces, lack = best
    family = "CVSans%d" % n
    for f in faces:
        pdfmetrics.registerFont(f)
    addMapping(family, 0, 0, family)
    addMapping(family, 1, 0, family + "-Bold")
    addMapping(family, 0, 1, family + "-Italic")
    addMapping(family, 1, 1, family + "-BoldItalic")
    return family, lack


def fonts_for(payload):
    """Point every style at a font that has every letter in the payload.
    Never refuses: when no such font is found, it says which letters will not
    show, so they can be written another way."""
    missing = _outside_latin1(_all_text(payload))
    family, lack = _unicode_font(missing) if missing else (None, [])
    for table, orig in ((CV, _CV0), (LT, _LT0)):
        for k, st in orig.items():
            if family:
                st = ParagraphStyle(st.name + "-u", parent=st,
                                    fontName=family + _BASE.get(st.fontName, ""),
                                    bulletFontName=family)
            table[k] = st
    if lack:
        print("note: no font on this computer has these letters, so they will not show: %s. "
              "Write them in Latin letters (a name as it is spelled in a passport), or "
              "install a font that has them (Noto Sans)." % "".join(lack[:20]))
    if _RTL.search(_all_text(payload)):
        print("note: right-to-left script (Arabic, Hebrew, Persian) is not laid out right to "
              "left in these PDFs. Write the documents in a left-to-right language, or have "
              "the person check them.")
    if _SHAPED.search(_all_text(payload)):
        print("note: Indic or Southeast Asian script shows its letters but not joined as the "
              "script needs. Write names in Latin letters too, or have the person check them.")
    return family


def check(payload):
    """Rewrite any dash in the payload's text, in place. Returns how many."""
    changed = [0]

    def walk(v):
        if isinstance(v, str):
            new = _undash(v)
            if new != v:
                changed[0] += 1
            return new
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v
    fixed = walk(payload)
    payload.clear()
    payload.update(fixed)
    if changed[0]:
        print("note: rewrote dashes in %d line(s): ranges use a hyphen, the rest a comma"
              % changed[0])
    return changed[0]


def _cv_story(c):
    """The CV's flowables, built fresh each call so one can be measured and a
    second handed to doc.build without reusing consumed flowables."""
    s = [Paragraph(c["name"], CV["name"])]
    if c.get("tagline"):
        s.append(Paragraph(c["tagline"], CV["tagline"]))
    for line in c.get("contact", []):
        s.append(Paragraph(line, CV["contact"]))
    s += [Spacer(1, 5), HRFlowable(width="100%", thickness=0.6, color=BLACK, spaceAfter=3)]
    if c.get("summary"):
        s.append(Paragraph(c["summary"], CV["body"]))

    for sec in c["sections"]:
        s.append(Paragraph(sec["title"].upper(), CV["section"]))
        s.append(HRFlowable(width="100%", thickness=0.5, color=BLACK, spaceAfter=3))
        if sec["type"] == "skills":
            for label, value in sec["items"]:
                s.append(Paragraph("<b>%s:</b> %s" % (label, value), CV["skill"]))
        else:
            for it in sec["items"]:
                head = it["role"] + (", " + it["org"] if it.get("org") else "")
                s.append(Paragraph(head, CV["role"]))
                if it.get("meta"):
                    s.append(Paragraph(it["meta"], CV["meta"]))
                for b in it.get("bullets", []):
                    s.append(Paragraph(b, CV["bullet"], bulletText="•"))
    return s


def _page_fill(story, avail_w, avail_h):
    """Estimate how many pages the story needs and how full the page is.

    Sums each flowable's wrapped height plus its style's spaceBefore/spaceAfter,
    the same spacing the frame applies. Returns (pages, fill_ratio) where
    fill_ratio is content height over one frame height. For a single-page CV that
    is the fraction of the page filled; a story that needs two pages is full by
    definition and reports its first page as full."""
    total = 0.0
    for f in story:
        st = getattr(f, "style", None)
        total += (getattr(st, "spaceBefore", 0) or 0)
        try:
            _, h = f.wrap(avail_w, avail_h)
        except Exception:
            h = getattr(f, "height", 0) or 0
        total += h
        total += (getattr(st, "spaceAfter", 0) or getattr(f, "spaceAfter", 0) or 0)
    pages = max(1, math.ceil(total / avail_h - 1e-6))
    fill = 1.0 if pages >= 2 else total / avail_h
    return pages, fill


def build_cv(c, out):
    check(c)
    fonts_for(c)
    doc = BaseDocTemplate(out, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                          topMargin=12 * mm, bottomMargin=12 * mm,
                          title="%s CV" % c["name"], author=c["name"])
    doc.addPageTemplates([PageTemplate(id="cv", frames=[
        Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")])])

    pages, fill = _page_fill(_cv_story(c), doc.width, doc.height)
    if pages == 1 and fill < CV_FILL_FLOOR and not os.environ.get("ALLOW_SHORT_CV"):
        print(
            "note: the CV fills about %d%% of one A4 page. If the profile has more real "
            "content that fits this posting (another role or result, a project, "
            "certifications, education detail, a skills line), add it and build again. "
            "If it has nothing more, keep it as it is and flag what is thin."
            % round(fill * 100))
    print("pages: %d" % pages)

    doc.build(_cv_story(c))


def build_letter(c, out):
    check(c)
    fonts_for(c)
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title=c["subject"])
    s = [Paragraph(c["sender"][0], LT["name"])]
    s += [Paragraph(x, LT["meta"]) for x in c["sender"][1:]]
    s.append(Spacer(1, 12))
    s += [Paragraph(x, LT["meta"]) for x in c["recipient"]]
    s += [Spacer(1, 10), Paragraph(c["date"], LT["meta"]), Spacer(1, 12),
          Paragraph(c["subject"], LT["subject"]), Spacer(1, 10),
          Paragraph(c["salutation"], LT["body"])]
    s += [Paragraph(p, LT["body"]) for p in c["paragraphs"]]
    s += [Spacer(1, 6), Paragraph(c["closing"], LT["body"]),
          Paragraph(c["signature"], LT["body"])]
    doc.build(s)


def inflate(path, floor=8192):
    # qpdf linearises the file, which some portals like. It is optional: a
    # fresh machine does not have it, and without this guard every build
    # crashed on a missing binary after the PDF was already written. The size
    # padding below works on any PDF, so the file is still valid without it.
    tmp = path + ".tmp"
    try:
        subprocess.run(["qpdf", "--linearize", "--stream-data=uncompress", path, tmp],
                       check=True, capture_output=True)
        os.replace(tmp, path)
    except (FileNotFoundError, subprocess.CalledProcessError):
        if os.path.exists(tmp):
            os.remove(tmp)
    if os.path.getsize(path) < floor:
        # Pad with a comment placed after the cross reference table and before
        # the final startxref, so object offsets and the trailer stay valid.
        # Padding after %%EOF instead leaves startxref out of reach of strict
        # parsers, and several ATS PDF readers then refuse the file outright.
        data = open(path, "rb").read()
        i = data.rfind(b"startxref")
        pad = b"%" + b"X" * (floor - len(data) + 64) + b"\n"
        open(path, "wb").write(data[:i] + pad + data[i:])
    return os.path.getsize(path)


if __name__ == "__main__":
    kind, src, dst = sys.argv[1], sys.argv[2], sys.argv[3]
    payload = json.load(open(src, encoding="utf-8"))
    (build_cv if kind == "cv" else build_letter)(payload, dst)
    print("wrote %s, %d bytes" % (dst, inflate(dst)))
