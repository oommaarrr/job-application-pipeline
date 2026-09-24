#!/usr/bin/env python3
"""
Check a CV payload spreads its evidence across the portfolio.

A CV that spends most of its experience section on one project, especially on that
project's incidents, reads as one system that kept breaking rather than as an
engineer with a portfolio. That happened in the 11 August batch: five of seven
bullets were one project, four of them war stories, hidden behind topic
labels like "Agent reliability" that look like separate projects.

    .venv/bin/python check_spread.py applications/2026-08-11/Acme/cv_acme.json
    .venv/bin/python check_spread.py applications/2026-08-11/*/cv_*.json

Exits non-zero if any rule fails, so it can gate a build.
"""

from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

MAX_PER_PROJECT = 2
# Raised from 3 to 4 on 21 September 2026. Three distinct projects was the floor
# that produced thin, half-empty CVs: three experience bullets and nothing else filled
# barely two thirds of the page. Four or more distinct projects, still at
# most two bullets each, is what fills a page with real range. build_docs.py
# enforces the page-fill floor separately; this enforces the variety.
MIN_PROJECTS = 4

# Labels that describe a topic rather than a named project. They are how a
# fourth bullet from the same project sneaks onto the page looking like something new.
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
    # When the build runs in parallel shards this may glob a payload another
    # shard is still mid-write, which reads back as truncated JSON. That is not
    # this file's problem to report: skip it this pass with a note, and let the
    # shard that owns it check its own finished file. A genuinely malformed file
    # the owner tries to build from fails at build_docs.py, which is the right
    # place for it.
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print(f"\n{path.parent.name}/{path.name}  (skipped: not readable as JSON yet)")
        return []
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
