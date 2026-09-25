#!/usr/bin/env python3
"""
Join the scraper's ranked output to the full job descriptions.

The scraper already did the mechanical work: relevance floor, German gate at
B1, years extraction, and dropping anything on applied.json. What it cannot do
is judge how close a role actually is to the user, which needs the description read
in full. So this writes one file with everything needed for that judgement.

    .venv/bin/python shortlist.py             one line per role
    .venv/bin/python shortlist.py --full 8    plus the top 8 full descriptions
    .venv/bin/python shortlist.py --full      plus every description
    .venv/bin/python shortlist.py --json-only just write the file, print nothing

Nineteen full descriptions run past 38 KB, which is more than a reader wants in
one go, so --full takes an optional count and a batch of 5 only ever needs the
head of the list.

Paths come from pipeline.json so the scraper can move without editing code.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
CONFIG = json.loads((HERE / "pipeline.json").read_text(encoding="utf-8"))
# Relative to this folder, so it works from any working directory.
SCRAPER = (HERE / CONFIG["scraper_root"]).resolve()


def _read(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return default


# The scraper's own key, not a local imitation.
#
# This file used to key descriptions on `url.split("?")[0]`, which drops the
# query string. For Indeed the query string IS the identity: every
# `de.indeed.com/viewjob?jk=<id>` collapsed to `de.indeed.com/viewjob`, so all
# six Indeed roles in a shortlist shared one description, whichever happened to
# be written last. On 19 September that was an unrelated Siemens leadership
# posting, and the batch correctly refused to build any of them.
#
# jobkey.job_key already knows how each board spells identity: Indeed's jk,
# LinkedIn's numeric id, and the path for StepStone. Use it rather than guessing
# again here.
sys.path.insert(0, str(SCRAPER))
from jobkey import job_key  # noqa: E402


def _key(url: str) -> str:
    return job_key(url or "")


def descriptions() -> dict[str, str]:
    """Every description the extension captured, newest file winning."""
    out: dict[str, str] = {}
    inbox = SCRAPER / "inbox"
    for path in sorted(inbox.glob("*.json")) + sorted((inbox / "archive").glob("*.json")):
        payload = _read(path, {})
        records = payload.get("jobs", []) if isinstance(payload, dict) else payload
        for rec in records:
            if not isinstance(rec, dict):
                continue
            k, desc = _key(rec.get("url", "")), (rec.get("description") or "").strip()
            if k and desc and k not in out:
                out[k] = desc
    return out


def build() -> list[dict]:
    """
    Prefer the local model's ranking, fall back to the old pipeline's.

    `out/ranked.json` is written by rank_ollama.py and is already cut to the
    batch size, already ordered, and already past the German, years, marketplace
    and repeat filters. `out/jobs.json` is the old run.py output, which the
    bridge rewrites on every push from the extension: on 18 September it
    replaced the ranking with 90 unfiltered rows twenty minutes after it was
    written. Reading the wrong one silently undoes every filter.
    """
    ranked = _read(SCRAPER / "out" / "ranked.json", [])
    if not ranked:
        ranked = _read(SCRAPER / "out" / "jobs.json", [])
        if ranked:
            print("WARNING: out/ranked.json is missing, falling back to the old "
                  "out/jobs.json. Run rank_ollama.py first.")
    if not ranked:
        return []
    desc = descriptions()
    rows = []
    for job in ranked:
        rows.append({**job, "description": desc.get(_key(job.get("url", "")), "")})
    return _shard(rows)


def _shard(rows: list[dict]) -> list[dict]:
    """
    Keep only this worker's slice of the ranked list, when the build is running
    in parallel.

    run_batch.py can launch two or more Claude sessions at once to cut wall-clock
    time. Each is one shard, told which by APPLY_SHARD (1-based) and how many by
    APPLY_SHARDS. With APPLY_SHARDS unset or 1 this is a no-op and the single
    session sees the whole list exactly as before.

    The split is by COMPANY, not by row: every posting from one company lands in
    the same shard. Two roles at the same company would otherwise be able to fall
    into different shards, and since both sessions write to the same
    applications/<date>/<Company>/ folder that is a collision. Companies are
    handed out round-robin in fit order (first new company -> shard 1, next new
    -> shard 2, …), which keeps the slices disjoint, keeps each company whole,
    and still gives every shard a fit-interleaved mix with its own spares.
    """
    try:
        n = int(os.environ.get("APPLY_SHARDS", "1") or "1")
        i = int(os.environ.get("APPLY_SHARD", "1") or "1")
    except ValueError:
        return rows
    if n <= 1 or i < 1 or i > n:
        return rows

    def _co(r: dict) -> str:
        return (r.get("company") or r.get("title") or "").strip().lower()

    assigned: dict[str, int] = {}
    nxt = 0
    for r in rows:
        c = _co(r)
        if c not in assigned:
            assigned[c] = nxt % n
            nxt += 1
    return [r for r in rows if assigned.get(_co(r)) == (i - 1)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", nargs="?", type=int, const=0, default=None,
                    metavar="N",
                    help="also print full descriptions, for the top N (default all)")
    ap.add_argument("--json-only", action="store_true",
                    help="write shortlist.json and print only its path")
    args = ap.parse_args()

    rows = build()
    if not rows:
        print("Nothing ranked. Collect some jobs first, or check that the bridge is up.")
        return 1

    # Parallel shards must not write the same artifact at the same time, so a
    # sharded run writes shortlist.shard-N.json. Claude reads this command's
    # stdout, not the file, so the name only matters for avoiding a clobber.
    try:
        _shard_i = int(os.environ.get("APPLY_SHARD", "1") or "1")
        _shard_n = int(os.environ.get("APPLY_SHARDS", "1") or "1")
    except ValueError:
        _shard_i, _shard_n = 1, 1
    out = (HERE / f"shortlist.shard-{_shard_i}.json") if _shard_n > 1 else (HERE / "shortlist.json")
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.json_only:
        print(out)
        return 0

    applied = _read(SCRAPER / "applied.json", {"applied": []}).get("applied", [])
    missing = sum(1 for r in rows if not r["description"])

    print(f"{len(rows)} open roles · {len(applied)} already applied "
          f"· {missing} without a description")
    print(f"written to {out}\n")

    for i, r in enumerate(rows, 1):
        print(f"{i:>2}. {r['title'][:58]:<58} {(r.get('company') or '')[:24]:<24} "
              f"{r['location'][:22]:<22} {r.get('years_source', ''):<12} "
              f"{r.get('german_level', '')}")
        print(f"    {r['url']}")

    if args.full is not None:
        shown = rows if args.full == 0 else rows[:args.full]
        for i, r in enumerate(shown, 1):
            print("\n" + "=" * 100)
            print(f"{i}. {r['title']} | {r.get('company')} | {r['location']}")
            print(f"{r['url']}\n")
            print(r["description"] or "(no description captured)")
        if len(shown) < len(rows):
            print(f"\n… {len(rows) - len(shown)} more descriptions not shown, "
                  f"pass --full or a larger number")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
