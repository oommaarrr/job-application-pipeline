#!/usr/bin/env python3
"""
Arbeitnow source.

The one source in this pipeline that needs no browser and no extension.
Arbeitnow publishes a free, unauthenticated JSON feed of its whole board, and
every record already carries its full description.

The feed cannot be searched (every search parameter is ignored), so this pulls
the whole week, about 3,400 jobs, and keeps the ones that match the searches
set on the dashboard (Arbeitnow, the gear next to "+ Arbeitnow") by meaning,
not by words in the title. How that works: arbeitnow_match.py. The first time,
the local model writes those searches from the profile.

Output goes to the bridge's POST /ingest, the same door the Chrome extension
uses, so the jobs land in today's inbox, get deduped by job_key and reranked
with everything else. If the bridge is down the file is written directly.

    python arbeitnow.py              # fetch, match, push
    python arbeitnow.py --dry-run    # show what would land, write nothing
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import urllib.request

import arbeitnow_match as am
import countries
from jobkey import job_key

# Follow the bridge's own port. The /arbeitnow route spawns this script from
# the bridge, so it inherits BRIDGE_PORT; hardcoding 8765 sent a second
# copy's jobs into whichever bridge happened to own the default port.
BRIDGE = f"http://127.0.0.1:{os.environ.get('BRIDGE_PORT', '8765')}/ingest"
ROOT = pathlib.Path(__file__).parent
INBOX = ROOT / "inbox"


def push(jobs: list[dict]) -> bool:
    """Bridge first — it dedupes, merges descriptions and reranks for us."""
    body = json.dumps({"jobs": jobs}).encode("utf-8")
    req = urllib.request.Request(
        BRIDGE, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as res:
            out = json.loads(res.read().decode("utf-8"))
    except Exception as e:                           # noqa: BLE001
        print(f"  ! bridge not answering ({e}) — writing the inbox file directly")
        return False
    print(f"  bridge: {out.get('added')} new, {out.get('total')} in today's pool, "
          f"{out.get('matches')} matches after rerank")
    return True


def write_direct(jobs: list[dict]) -> pathlib.Path:
    """
    Fallback when the bridge is down.

    Merges into today's file by job_key rather than overwriting it, because the
    extension may already have written into it this morning.
    """
    INBOX.mkdir(exist_ok=True)
    path = INBOX / f"job-collector-{dt.date.today().isoformat()}.json"
    existing: dict[str, dict] = {}
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        existing = {job_key(j["url"]): j for j in payload.get("jobs", []) if j.get("url")}
    for job in jobs:
        key = job_key(job["url"])
        prev = existing.get(key, {})
        existing[key] = {**prev, **job,
                         "description": job["description"] or prev.get("description", "")}
    rows = list(existing.values())
    path.write_text(json.dumps(
        {"exported_at": dt.datetime.now().isoformat(timespec="seconds"),
         "count": len(rows), "jobs": rows}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    print(f"  wrote {len(rows)} jobs to {path.name} (rerank with: python run.py --inbox)")
    return path


def describe(s: dict) -> None:
    on = [x for x in s["searches"] if x.get("on", True)]
    for x in on:
        print(f"  search: {x['name']}: {x['looking_for']}")
    where = ", ".join(countries.name(c) for c in s["countries"]) or "anywhere"
    print(f"  on site: {where}"
          + ("; remote from other countries too" if s["countries"] and s["remote_elsewhere"]
             else ""))
    print(f"  internships and working-student jobs: {'in' if s['student_roles'] else 'out'}; "
          f"last {s['days']} day(s); match: {s['strictness']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Pull Arbeitnow into the job pipeline.")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would land, write nothing")
    ap.add_argument("--pages", type=int, default=am.MAX_PAGES)
    args = ap.parse_args()

    def log(msg: str) -> None:
        print(msg, flush=True)

    print("Arbeitnow")
    s, note = am.ensure_settings(log)
    if note:
        print(f"  {note}")
    if not any(x.get("on", True) for x in s["searches"]):
        print("  ! FAILED: no Arbeitnow search is set. Open the dashboard, press the gear "
              "next to + Arbeitnow, and describe the jobs you want.")
        return 1
    describe(s)

    feed, error = am.fetch(log, args.pages)
    jobs = feed["jobs"]
    print(f"  {len(jobs)} jobs from the last {am.MAX_DAYS} days on disk")
    if not jobs:
        print(f"  ! FAILED: {error or 'Arbeitnow returned no jobs'}. "
              "Check the connection and press + Arbeitnow again.")
        return 1
    if error:
        print(f"  ! {error}; going on with the jobs already on disk")

    places = am.resolve_places([j.get("location") or "" for j in jobs.values()], log)
    try:
        vecs = am.ensure_vectors(jobs, log)
        res = am.select(s, feed, vecs, places)
    except Exception as e:                                   # noqa: BLE001
        print(f"  ! FAILED: {e}. The local model (Ollama) has to be running "
              f"with '{am.EMBED_MODEL}' installed.")
        return 1

    d = res["dropped"]
    print(f"  {res['eligible']} pass the rules "
          f"({d['old']} older than {s['days']} day(s), {d['student']} internships or "
          f"working-student jobs, {d['location']} in the wrong place)")
    for name, n in res["per_search"].items():
        print(f"  Arbeitnow: {name}: {n}")
    kept = res["kept"]
    print(f"  {len(kept)} kept")
    if not kept:
        return 0
    for j in kept:
        j.pop("_z", None)
        j.pop("_slug", None)

    if args.dry_run:
        for j in kept:
            print(f"    · {j['title']} — {j['company']} — {j['location']} "
                  f"[{j['query']}, {j['match']:.2f}]")
        return 0

    if not push(kept):
        write_direct(kept)
    return 0


if __name__ == "__main__":
    sys.exit(main())
