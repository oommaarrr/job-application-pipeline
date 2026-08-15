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

Enforced: black only, no tables, no em/en dashes or double hyphens, qpdf inflate above 8 KB.
"""

import json
import os
import subprocess
import sys

from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, HRFlowable, PageTemplate,
                                Paragraph, SimpleDocTemplate, Spacer)

BLACK = "#000000"


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


def check(payload):
    text = json.dumps(payload, ensure_ascii=False)
    for bad in ("—", "–", "--"):
        if bad in text:
            raise SystemExit("Forbidden dash sequence in content: %r" % bad)


def build_cv(c, out):
    check(c)
    doc = BaseDocTemplate(out, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                          topMargin=12 * mm, bottomMargin=12 * mm,
                          title="%s CV" % c["name"], author=c["name"])
    doc.addPageTemplates([PageTemplate(id="cv", frames=[
        Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")])])

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
    doc.build(s)


def build_letter(c, out):
    check(c)
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
    tmp = path + ".tmp"
    subprocess.run(["qpdf", "--linearize", "--stream-data=uncompress", path, tmp], check=True)
    os.replace(tmp, path)
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
