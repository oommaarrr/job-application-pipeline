#!/usr/bin/env python3
"""
Is this posting still open?

Why this exists
---------------
Decision, 19 September 2026: of 15 applications built, only 10 could be sent.
Three needed German (handled by the gate in rank_ollama.py) and the rest were
simply closed. A scraped pool is a snapshot; a posting that was open when the
card was read can be filled, expired or withdrawn by the time a CV is written
for it, and nothing downstream ever asked.

Two checks, both cheap, neither of which needs a session:

  LinkedIn  the guest endpoint that already serves the description. 404 or 410
            means the posting is gone. 200 plus "no longer accepting
            applications" means it is there and closed, which is the more common
            case and the one a status code alone would miss.
  anything  a plain GET. 404 and 410 are gone. Everything else is kept: a 403
            from a bot wall says nothing about the posting, and dropping a live
            role on a scraper's say-so is the worse error.

Used as a filter over ranked.json before the build:

    .venv/bin/python check_live.py                 # filter out/ranked.json
    .venv/bin/python check_live.py --dry-run       # report, change nothing
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import pathlib
import random
import re
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).parent
RANKED = HERE / "out" / "ranked.json"
GUEST = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Both languages: a German posting says it in German.
CLOSED = re.compile(
    r"(no longer accepting applications|this job is no longer available|"
    r"keine bewerbungen mehr|stellenanzeige ist nicht mehr|"
    r"position has been filled|applications are closed)", re.I)

LINKEDIN_ID = re.compile(r"(?:jobs/view/|currentJobId=|/jobPosting/)(\d{6,})")


def _get(url: str, timeout: int = 15) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en,de;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(120_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def check(url: str) -> tuple[str, str]:
    """('open' | 'gone' | 'closed' | 'unknown', why)."""
    m = LINKEDIN_ID.search(url or "")
    if m and "linkedin.com" in url:
        code, body = _get(GUEST.format(m.group(1)))
        if code in (404, 410):
            return "gone", f"LinkedIn guest endpoint returned {code}"
        if code == 200 and CLOSED.search(body):
            return "closed", "the posting says it is no longer accepting applications"
        if code == 200:
            return "open", ""
        return "unknown", f"guest endpoint returned {code}"

    code, body = _get(url)
    if code in (404, 410):
        return "gone", f"the posting returned {code}"
    if code == 200 and CLOSED.search(body):
        return "closed", "the posting says it is no longer accepting applications"
    # 403, 429, 0: a bot wall or a timeout. Says nothing about the posting.
    return ("open" if code == 200 else "unknown"), ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(RANKED))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    path = pathlib.Path(args.file)
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 1
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        print("nothing to check")
        return 0

    def one(r):
        time.sleep(random.uniform(0.2, 1.2))    # do not hammer either host
        state, why = check(r.get("url", ""))
        return r, state, why

    keep, dropped = [], []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r, state, why in ex.map(one, rows):
            tag = f"{r.get('company','?')[:24]:26} {r.get('title','')[:44]}"
            if state in ("gone", "closed"):
                dropped.append((state, why, r))
                print(f"  {state.upper():7} {tag}  — {why}")
            else:
                keep.append(r)
                if state == "unknown":
                    print(f"  {'?':7} {tag}  — could not check, keeping it")

    print(f"\n{len(keep)} still open · {len(dropped)} gone or closed")
    if dropped and not args.dry_run:
        path.write_text(json.dumps(keep, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        print(f"rewrote {path.name} with the {len(keep)} that are still open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
