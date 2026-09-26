#!/usr/bin/env python3
"""
Repair LinkedIn descriptions already in the inbox, without re-scraping.

Why this exists
---------------
Every LinkedIn description collected before 18 September was page furniture
rather than job content: the header, a Premium upsell, the footer and the
language picker. The collector's fallback chain ended at `main`, and on a
hashed-class LinkedIn page every specific selector misses, so `main` matched the
whole document. Median 1756 characters of chrome against about 4000 of real text
from StepStone and Indeed.

The postings themselves are fine and we already hold their ids, so there is no
reason to scrape again. LinkedIn's guest endpoint serves the posting body with
no session, which is the same source the fixed collector now uses.

    .venv/bin/python backfill_linkedin.py            repair today's inbox
    .venv/bin/python backfill_linkedin.py --day 2026-09-18
    .venv/bin/python backfill_linkedin.py --dry-run  report, change nothing
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib
import random
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser

HERE = pathlib.Path(__file__).parent
INBOX = HERE / "inbox"
GUEST = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# The same guard the collector uses. Text matching any of these is LinkedIn's
# furniture, and storing it is worse than storing nothing: it is well-formed
# prose that survives every downstream check and poisons the gates.
CHROME = re.compile("|".join([
    "reactivate premium", "select language", "linkedin corporation ©",
    "get ai-powered advice", "community guidelines", "join or sign in",
    "user agreement",
]), re.I)

WANTED = ("show-more-less-html__markup", "description__text")


class Block(HTMLParser):
    """Text of the first element whose class contains one of WANTED."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.inside = False
        self.done = False
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if self.inside:
            self.depth += 1
            if tag in ("p", "br", "li", "div", "ul", "ol"):
                self.parts.append("\n")
            return
        cls = dict(attrs).get("class") or ""
        if any(w in cls for w in WANTED):
            self.inside, self.depth = True, 1

    def handle_endtag(self, tag):
        if not self.inside or self.done:
            return
        self.depth -= 1
        if self.depth <= 0:
            self.inside, self.done = False, True

    def handle_data(self, data):
        if self.inside and not self.done:
            self.parts.append(data)

    @property
    def text(self) -> str:
        t = html.unescape("".join(self.parts))
        t = re.sub(r"[ \t\xa0]+", " ", t)
        t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t)
        return t.strip()


def fetch(job_id: str, timeout: int = 25) -> str:
    req = urllib.request.Request(GUEST.format(job_id), headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-GB,en;q=0.9,de;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    p = Block()
    p.feed(raw)
    return p.text


def is_chrome(text: str) -> bool:
    return not text or len(text) < 300 or bool(CHROME.search(text))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", default=dt.date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    path = INBOX / f"job-collector-{args.day}.json"
    if not path.exists():
        print(f"no inbox for {args.day}")
        return 1
    payload = json.loads(path.read_text(encoding="utf-8"))
    jobs = payload["jobs"] if isinstance(payload, dict) else payload

    broken = []
    for rec in jobs:
        if rec.get("source") != "LinkedIn":
            continue
        if not is_chrome(rec.get("description") or ""):
            continue
        m = re.search(r"/jobs/view/(\d+)", rec.get("url") or "")
        if m:
            broken.append((rec, m.group(1)))
    if args.limit:
        broken = broken[: args.limit]

    total_li = sum(1 for r in jobs if r.get("source") == "LinkedIn")
    print(f"{len(jobs)} jobs · {total_li} from LinkedIn · "
          f"{len(broken)} with an unusable description")
    if not broken:
        print("nothing to repair")
        return 0
    if args.dry_run:
        for rec, _ in broken[:10]:
            print(f"  would refetch: {rec.get('company') or '?'} — {rec.get('title')}")
        return 0

    fixed = failed = still_bad = 0
    # Paced deliberately. Seven requests in quick succession from a shell was
    # enough for LinkedIn to start refusing connections outright while this was
    # being tested, and a backfill is not urgent.
    for i, (rec, job_id) in enumerate(broken, 1):
        try:
            text = fetch(job_id)
        except OSError as e:
            failed += 1
            print(f"  {i:>3}/{len(broken)}  !! {(rec.get('company') or '?')[:26]:<26} {e}")
        else:
            if is_chrome(text):
                still_bad += 1
                print(f"  {i:>3}/{len(broken)}  ?? {(rec.get('company') or '?')[:26]:<26} "
                      f"still unusable ({len(text)} chars)")
            else:
                rec["description"] = text
                fixed += 1
                print(f"  {i:>3}/{len(broken)}  ok {(rec.get('company') or '?')[:26]:<26} "
                      f"{len(text):>5} chars")
        if i < len(broken):
            time.sleep(random.uniform(3.0, 6.0))
        # Save as we go: a backfill that is interrupted should keep what it
        # already bought rather than starting over.
        if fixed and i % 10 == 0:
            path.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                            encoding="utf-8")

    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n{fixed} repaired · {still_bad} still unusable · {failed} could not be fetched")
    print(f"written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
