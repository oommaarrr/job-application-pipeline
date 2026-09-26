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

HERE = pathlib.Path(__file__).parent
INBOX = HERE / "inbox"


def _read(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def status() -> dict:
    files = sorted(INBOX.glob("job-collector-*.json"))
    days = [m.group(1) for f in files if (m := re.search(r"(\d{4}-\d{2}-\d{2})", f.name))]
    newest = max(days) if days else None
    age = (dt.date.today() - dt.date.fromisoformat(newest)).days if newest else None

    # The same rule the ranker applies, from the same function (pool.py), so
    # "usable" is exactly what the ranker will judge, never a different count.
    import pool
    b = pool.summary()
    usable = b["to_judge"]
    return {
        "total": b["collected"],
        "with_descriptions": b["collected"] - b["no_description"] - b["too_old"],
        # Exactly what the ranker will judge: the only number that decides anything.
        "usable": usable,
        "repeats": b["repeat"],
        # Every collected job is in one bucket; they add up to "total".
        "breakdown": b,
        "explain": pool.explain(b),
        "newest_scrape": newest,
        "age_days": age,
        "stale": bool(age is not None and age > config.MAX_POOL_AGE_DAYS),
        # The floor, not the target. A pool of 10 is worth building; it is
        # simply a batch of 10 rather than a batch of 15.
        "enough_to_build": usable >= config.MIN_WORTH_BUILDING,
        "build_now": min(usable, config.BUILD_TARGET),
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
        print(f"{s['explain']} · "
              f"newest scrape {s['newest_scrape']} ({s['age_days']} day(s) old)"
              + (" · STALE" if s["stale"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
