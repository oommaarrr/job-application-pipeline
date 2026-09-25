#!/usr/bin/env python3
"""
How much is actually there to build from, regardless of which day it arrived.

Why this exists
---------------
`run-batch.sh` used to gate on the bridge's `collected_today`, which counts one
file: `inbox/job-collector-<today>.json`. On 19 September that meant a pool of
158 scraped jobs, 23 of them buildable, sat untouched while the batch printed
"pool is empty, waiting for a scrape" every twenty seconds. Nothing was wrong
with the pool. The clock had simply rolled past midnight.

A job posting does not expire at midnight, so neither should the pool. What
matters is whether there are enough roles still worth building, and how old the
newest scrape is.

    .venv/bin/python pool_status.py          one line of JSON
    .venv/bin/python pool_status.py --human  a sentence
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys

import config
from applied_index import built_before, index as applied_index
from jobkey import job_key
import ledger

HERE = pathlib.Path(__file__).parent
INBOX = HERE / "inbox"


def _read(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def status() -> dict:
    files = sorted(INBOX.glob("job-collector-*.json"))
    days = []
    for f in files:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
        if m:
            days.append(m.group(1))
    newest = max(days) if days else None
    age = None
    if newest:
        age = (dt.date.today() - dt.date.fromisoformat(newest)).days

    # Only count scrapes inside the freshness window. An eight week old posting
    # is usually filled, and a CV written for it is wasted work.
    cutoff = dt.date.today() - dt.timedelta(days=config.MAX_POOL_AGE_DAYS)
    jobs: dict[str, dict] = {}
    for f in files:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
        if m and dt.date.fromisoformat(m.group(1)) < cutoff:
            continue
        payload = _read(f, {})
        for rec in (payload.get("jobs", []) if isinstance(payload, dict) else payload):
            if isinstance(rec, dict) and rec.get("url"):
                # The day it was collected, as the ranker stamps it, so the
                # repeat rule below gives the same answer here as there.
                jobs[job_key(rec["url"])] = {**rec, "_collected": m.group(1)} if m else rec

    applied_urls, applied_roles = applied_index()

    def seen(j: dict) -> bool:
        if built_before(j.get("company", ""), j.get("title", ""), applied_roles):
            return True
        # applied_urls is keyed with job_key now, so one comparison is enough.
        # The old second comparison against split("?")[0] was the Indeed bug:
        # every viewjob?jk=<id> collapsed to the same string.
        return job_key(j.get("url", "")) in applied_urls

    described = [j for j in jobs.values() if (j.get("description") or "").strip()]
    not_built = [j for j in described if not seen(j)]
    # The ranker also drops REPEATS: a role first collected on an earlier day
    # (history/seen.csv). Leaving that out here made the dashboard say "scoring
    # 128 jobs" while the ranker was judging 86 (25 September 2026). Read only:
    # the ranker is the one that records what it sees.
    try:
        first = ledger.seen_first() if getattr(config, "SEEN_DAYS", 30) > 0 else {}
    except OSError:
        first = {}
    repeats = [j for j in not_built if ledger.seen_earlier(j, first)]
    usable = [j for j in not_built if not ledger.seen_earlier(j, first)]
    return {
        "total": len(jobs),
        "with_descriptions": len(described),
        # Not yet built or applied, and not a repeat: exactly what the ranker
        # will judge. The only number that decides anything.
        "usable": len(usable),
        "repeats": len(repeats),
        "newest_scrape": newest,
        "age_days": age,
        "stale": bool(age is not None and age > config.MAX_POOL_AGE_DAYS),
        # The floor, not the target. A pool of 10 is worth building; it is
        # simply a batch of 10 rather than a batch of 15.
        "enough_to_build": len(usable) >= config.MIN_WORTH_BUILDING,
        "build_now": min(len(usable), config.BUILD_TARGET),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--human", action="store_true")
    args = ap.parse_args()
    s = status()
    if not args.human:
        print(json.dumps(s))
        return 0
    if not s["newest_scrape"]:
        print("nothing has ever been scraped")
    else:
        print(f"{s['usable']} usable role(s) from {s['total']} scraped · "
              f"newest scrape {s['newest_scrape']} ({s['age_days']} day(s) old)"
              + (" · STALE" if s["stale"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
