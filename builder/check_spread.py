#!/usr/bin/env python3
"""
Show how a CV payload spreads its experience bullets, so whoever is writing it
can see at a glance whether one thing dominates the page.

    .venv/bin/python check_spread.py applications/2026-08-11/Acme/cv_acme.json
    .venv/bin/python check_spread.py applications/2026-08-11/*/cv_*.json

Until November 2026 this was a gate that refused to build a CV: at least four
distinct bold project labels, at most two bullets each, and no label from a
fixed list of engineering topics. That suited one engineer's project portfolio
and blocked everyone else: a sales or marketing CV, whose bullets are results
inside each role, failed with "no labelled bullets found", and a student with
one internship could never show four projects. How a CV is shaped is now the
writer's judgement, from the person's profile and preferences (cv_shape), and
this only reports. It never fails a build.
"""

from __future__ import annotations

import collections
import json
import pathlib
import re
import sys


def bullets_of(payload: dict) -> list[str]:
    out = []
    for sec in payload.get("sections", []):
        if sec.get("type") != "experience":
            continue
        for item in sec.get("items", []):
            out += item.get("bullets", [])
    return out


def label_of(bullet: str) -> str | None:
    """A bold lead-in that names something ("<b>Forecasting service:</b>"),
    not a bold number ("<b>132%</b> of quota")."""
    m = re.match(r"\s*<b>(.*?):?</b>", bullet)
    if not m or not re.search(r"[^\W\d_]", m.group(1)):
        return None
    return m.group(1).strip()


def report(path: pathlib.Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A parallel shard may still be writing it; its owner looks at it.
        print(f"\n{path.parent.name}/{path.name}  (not readable as JSON yet)")
        return
    bullets = bullets_of(payload)
    labels = [lb for lb in (label_of(b) for b in bullets) if lb]
    print(f"\n{path.parent.name}/{path.name}  ({len(bullets)} experience bullets, "
          f"{len(labels)} with a bold label)")
    for label, n in collections.Counter(labels).most_common():
        print(f"   {n}  {label}")


def main() -> int:
    paths = [pathlib.Path(a) for a in sys.argv[1:]]
    if not paths:
        print(__doc__)
        return 0
    for p in paths:
        if p.exists():
            report(p)
        else:
            print(f"\n{p}: not found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
