#!/usr/bin/env python3
"""
Check a CV payload spreads its evidence across the portfolio.

A model asked to tailor a CV will reach for whichever project it has the most
material on, then keep reaching. The result is a page where five of seven
bullets describe the same system, several of them things that went wrong with
it, and a reader sees one project that kept breaking rather than an engineer
with a portfolio. Generic labels hide it, because "Reliability work" and
"Evaluation" look like separate projects until you notice they are the same one.

This is a build gate, not a linter. Three rules:

    - at most MAX_PER_PROJECT bullets carrying the same bold label
    - at least MIN_PROJECTS distinct labels in the experience sections
    - no topic labels standing in for a project name

    .venv/bin/python check_spread.py applications/<date>/<Company>/cv_*.json
    .venv/bin/python check_spread.py applications/<date>/*/cv_*.json

Exits non-zero if any rule fails, so a failing payload never reaches the PDF
builder. Extend TOPIC_LABEL with the euphemisms your own writing falls into.
"""

from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

MAX_PER_PROJECT = 2
MIN_PROJECTS = 3

# Labels that describe a topic rather than a named project. They are how a
# fourth bullet about the same system sneaks onto the page looking new.
# Add your own: whatever your CV writer reaches for when it runs out of
# genuinely distinct projects belongs in this list.
TOPIC_LABEL = re.compile(
    r"^(agent |retrieval|evaluation|security review|production debugging|"
    r"serverless|reliability|built with|iterating|wiring|reverse engineering|"
    r"sicherheit|evaluation und|serverless unter|tool-integrationen|"
    r"llm-orchestrierung)", re.I)


def bullets_of(payload: dict) -> list[str]:
    out = []
    for sec in payload.get("sections", []):
        if sec.get("type") != "experience":
            continue
        # Only the employer sections carry project bullets; education has none.
        for item in sec.get("items", []):
            out += item.get("bullets", [])
    return out


def label_of(bullet: str) -> str | None:
    m = re.match(r"\s*<b>(.*?):?</b>", bullet)
    return m.group(1).strip() if m else None


def check(path: pathlib.Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    labels = [l for l in (label_of(b) for b in bullets_of(payload)) if l]
    if not labels:
        return [f"{path}: no labelled bullets found"]

    counts = collections.Counter(labels)
    problems = []

    for label, n in counts.items():
        if n > MAX_PER_PROJECT:
            problems.append(f"{label!r} appears {n} times, limit is {MAX_PER_PROJECT}")

    if len(counts) < MIN_PROJECTS:
        problems.append(f"only {len(counts)} distinct projects, minimum is {MIN_PROJECTS}")

    topics = [l for l in counts if TOPIC_LABEL.match(l)]
    if topics:
        problems.append("topic labels, not project names: " + ", ".join(sorted(topics)))

    print(f"\n{path.parent.name}/{path.name}  ({len(labels)} labelled bullets)")
    for label, n in counts.most_common():
        flag = "  <-- over limit" if n > MAX_PER_PROJECT else ""
        flag += "  <-- topic, not a project" if TOPIC_LABEL.match(label) else ""
        print(f"   {n}  {label}{flag}")
    return [f"{path.parent.name}: {p}" for p in problems]


def main() -> int:
    paths = [pathlib.Path(a) for a in sys.argv[1:]]
    if not paths:
        print(__doc__)
        return 2

    problems: list[str] = []
    for p in paths:
        if p.exists():
            problems += check(p)
        else:
            problems.append(f"{p}: not found")

    print()
    if problems:
        print("FAILED")
        for p in problems:
            print("  -", p)
        return 1
    print("OK, evidence is spread across the portfolio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
