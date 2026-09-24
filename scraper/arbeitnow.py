#!/usr/bin/env python3
"""
Arbeitnow source.

The one source in this pipeline that needs no browser and no extension.

Arbeitnow publishes a free, unauthenticated JSON feed of its whole board
(https://www.arbeitnow.com/api/job-board-api), 250 jobs a page, newest first,
and — this is the part that matters — **every record already carries its full
description**. LinkedIn costs a scroll loop, a per-card fetch and a bot-wall
risk to get the same thing; StepStone got the IP flagged by Akamai trying. Here
it is two HTTP GETs.

It is also a German-market board that leans English-speaking and
visa-sponsorship roles, which is exactly the gap in the LinkedIn pool: on
22 September the German-language gate dropped 44 of 110 German-located postings,
the single biggest constraint in the funnel. This adds German-located volume
that is far less likely to be German-required.

The feed is not keyword-searchable, so a "search" here is a title pattern
applied to the feed. That is strictly better than a search box: one download
serves both searches, and the patterns catch title variants a literal keyword
would miss ("Applied AI Engineer", "KI-Entwickler", "Agentic AI Developer").

Output goes to the bridge's POST /ingest, the same door the Chrome extension
uses, so the jobs land in today's inbox, get deduped by job_key and reranked
with everything else. If the bridge is down the file is written directly.

    python3 arbeitnow.py              # fetch, filter, push
    python3 arbeitnow.py --dry-run    # show what would land, write nothing
"""

from __future__ import annotations

import argparse
import os
import datetime as dt
import json
import pathlib
import re
import sys
import time
import urllib.request

from bs4 import BeautifulSoup

from jobkey import job_key
from rank_ollama import in_germany

API = "https://www.arbeitnow.com/api/job-board-api"
# Follow the bridge's own port. The /arbeitnow route spawns this script from
# the bridge, so it inherits BRIDGE_PORT; hardcoding 8765 sent a second
# copy's jobs into whichever bridge happened to own the default port.
BRIDGE = f"http://127.0.0.1:{os.environ.get('BRIDGE_PORT', '8765')}/ingest"
ROOT = pathlib.Path(__file__).parent
INBOX = ROOT / "inbox"

# Mirrors LinkedIn's f_TPR=r604800. Anything older has almost certainly been
# seen by a previous run anyway, and seen_jobs would drop it.
MAX_AGE_DAYS = 7
# 250 a page, newest first. The date cutoff normally stops it long before this;
# the cap only exists so a malformed `created_at` cannot spin the loop.
MAX_PAGES = 8
PAGE_DELAY = 1.0

# A role noun. Without one, "AI" in a title is usually marketing ("AI Sales
# Manager", "Head of AI"), not an engineering job.
ROLE = re.compile(
    r"\b(engineer|engineering|entwickler(?:in)?|ingenieur|developer|architect|"
    r"scientist|programmer)\b", re.I)

# Hard-outs from their own rules (section: internships, working-student and
# thesis placements are not jobs). Filtering here rather than in the ranker
# saves an Ollama call per posting on things that can never pass.
EXCLUDE = re.compile(
    r"\b(werkstudent\w*|praktikum|praktikant\w*|intern|internship|trainee|"
    r"thesis|abschlussarbeit|bachelorarbeit|masterarbeit|ausbildung|"
    r"auszubildende\w*|duales?\b|dual stud\w*|schüler\w*)\b", re.I)

_REMOTE = re.compile(r"\b(remote|homeoffice|home office|anywhere)\b", re.I)


def _in_lane_location(loc: str) -> bool:
    """
    Their standing rule, 22 September 2026: anything outside Germany must be
    remote.

    On LinkedIn that rule is the f_WT=2 URL parameter. Arbeitnow has no such
    filter, and the board carries London, Paris and UK postings, so the rule is
    enforced here instead. A German location passes on-site or not; everything
    else has to say remote. A bare "Remote" with no country passes, same as it
    would from LinkedIn.
    """
    return in_germany(loc) or bool(_REMOTE.search(loc or ""))

# The two searches. Each is a title pattern that must co-occur with ROLE.
#
# Keep it at two. He asked for two, and the feed is one download either way —
# a third pattern would only widen the net into lanes the ranker drops anyway
# (data engineering, plain backend), costing Ollama time for nothing.
SEARCHES = [
    (
        "Arbeitnow: AI Engineer",
        re.compile(
            r"(\bai\b|\ba\.i\.|artificial intelligence|künstliche intelligenz|"
            r"\bki[-\s](?:entwickler|engineer|ingenieur)|\bllm\b|\bllms\b|"
            r"genai|generative ai|agentic|\bagents?\b|\bnlp\b|"
            r"natural language|prompt)", re.I),
    ),
    (
        "Arbeitnow: Machine Learning Engineer",
        re.compile(
            r"(machine learning|maschinelles lernen|\bml\b|\bmlops\b|"
            r"deep learning|computer vision|neural)", re.I),
    ),
]


def _html_to_text(html: str) -> str:
    """Arbeitnow descriptions are HTML fragments. The ranker wants prose."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _location(rec: dict) -> str:
    """
    Match the convention the rest of the pipeline reads.

    rank_ollama.in_germany() decides the years ceiling from this string, and
    the remote-only rule for anything outside Germany is read off the same
    "(Remote)" suffix LinkedIn uses. So write it the same way.
    """
    loc = (rec.get("location") or "").strip() or "Germany"
    if rec.get("remote") and "remote" not in loc.lower():
        loc = f"{loc} (Remote)"
    return loc


def fetch(max_pages: int = MAX_PAGES) -> list[dict]:
    """Pull the feed newest-first, stopping once a page falls past the cutoff."""
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        url = f"{API}?page={page}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "job-pipeline/1.0 (personal job search tool)"})
        payload = None
        # Three tries per page. A dropped connection or a 5xx is usually gone
        # seconds later, and giving up on page 1 used to end the whole run with
        # nothing collected.
        for attempt in range(1, 4):
            try:
                with urllib.request.urlopen(req, timeout=45) as res:
                    payload = json.loads(res.read().decode("utf-8"))
                break
            except Exception as e:                   # noqa: BLE001 — any failure is the same failure
                print(f"  ! page {page}, try {attempt}/3: {e}")
                if attempt < 3:
                    time.sleep(5 * attempt)
        if payload is None:
            print(f"  ! giving up at page {page}; keeping the {len(out)} jobs already fetched")
            break

        rows = payload.get("data") or []
        if not rows:
            break
        out.extend(rows)
        oldest = min((r.get("created_at") or 0) for r in rows)
        print(f"  page {page}: {len(rows)} jobs, oldest "
              f"{dt.date.fromtimestamp(oldest)}")
        if oldest < cutoff:
            break
        if not (payload.get("links") or {}).get("next"):
            break
        time.sleep(PAGE_DELAY)
    return out


def select(rows: list[dict]) -> list[dict]:
    """
    Apply the two searches.

    A posting that matches both is kept once, attributed to the first search
    that claimed it, so the per-search counts in the audit stay honest and the
    inbox never carries the same URL twice.
    """
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    claimed: dict[str, dict] = {}
    counts = {name: 0 for name, _ in SEARCHES}
    dropped_location = 0

    for rec in rows:
        title = (rec.get("title") or "").strip()
        url = (rec.get("url") or "").strip()
        if not title or not url:
            continue
        if (rec.get("created_at") or 0) < cutoff:
            continue
        if EXCLUDE.search(title) or not ROLE.search(title):
            continue
        location = _location(rec)
        if not _in_lane_location(location):
            dropped_location += 1
            continue

        for name, pattern in SEARCHES:
            if not pattern.search(title):
                continue
            key = job_key(url)
            if key in claimed:
                break
            claimed[key] = {
                "title": title,
                "company": (rec.get("company_name") or "").strip(),
                "location": location,
                "url": url,
                "source": "Arbeitnow",
                "query": name,
                "description": _html_to_text(rec.get("description") or ""),
            }
            counts[name] += 1
            break

    for name, n in counts.items():
        print(f"  {name}: {n}")
    if dropped_location:
        print(f"  {dropped_location} dropped: outside Germany and not remote")
    return list(claimed.values())


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
    print(f"  wrote {len(rows)} jobs to {path.name} (rerank with: python3 run.py --inbox)")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape Arbeitnow into the job pipeline.")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would land, write nothing")
    ap.add_argument("--pages", type=int, default=MAX_PAGES)
    args = ap.parse_args()

    print("Arbeitnow")
    rows = fetch(args.pages)
    print(f"  {len(rows)} postings in the feed")
    if not rows:
        print("  ! FAILED: Arbeitnow could not be reached. Check the connection and press + Arbeitnow again.")
        return 1

    jobs = select(rows)
    print(f"  {len(jobs)} kept")
    if not jobs:
        return 0

    if args.dry_run:
        for j in jobs:
            print(f"    · {j['title']} — {j['company']} — {j['location']} "
                  f"[{len(j['description'])} chars]")
        return 0

    if not push(jobs):
        write_direct(jobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
