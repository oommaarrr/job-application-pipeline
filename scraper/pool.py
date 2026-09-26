"""
Which collected jobs the local model reads, and why each of the others is not.

ONE rule, used by the ranker and by every number the dashboard shows. Until
25 September 2026 the ranker and pool_status.py each had their own copy, they
drifted, and the dashboard said "scoring 128 jobs" while the ranker judged 86.
Every job lands in exactly one bucket, in this order, so the buckets always
add up to what was collected:

    too_old          only in inbox files older than MAX_POOL_AGE_DAYS
    applied          applied to, or built in an earlier batch
    no_description   nothing for the model to read
    repeat           collected on an earlier day AND already judged by the
                     model then (history/seen.csv). Collected but never judged
                     is not a repeat: it goes to the model now.
    to_judge         everything else: what the model reads
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re

import config
import ledger
from applied_index import built_before, index as applied_index
from jobkey import job_key

HERE = pathlib.Path(__file__).parent
INBOX = HERE / "inbox"
CACHE = HERE / "out" / "ollama_cache.json"
BACKFILLED = ledger.HISTORY / ".judged-backfilled"

# Plain words for each bucket, for the dashboard and the ranker's log.
REASON = {
    "too_old": "collected more than {days} days ago",
    "applied": "already applied or built",
    "no_description": "no description to read",
    "repeat": "already judged on an earlier day",
}


def _read(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _records(path: pathlib.Path) -> list[dict]:
    payload = _read(path, {})
    recs = payload.get("jobs", []) if isinstance(payload, dict) else payload
    return [r for r in (recs or []) if isinstance(r, dict) and r.get("url")]


def load(day: str | None = None) -> tuple[list[dict], int]:
    """
    The collected jobs (newest file winning on a repeated link), each stamped
    with "_collected" = the day of its inbox file, plus how many were left out
    for being too old. `day` limits it to one day's file.
    """
    files = sorted(INBOX.glob("job-collector-*.json"))
    if day:
        files = [f for f in files if day in f.name]
    cutoff = (dt.date.today() - dt.timedelta(days=config.MAX_POOL_AGE_DAYS)).isoformat()
    fresh: dict[str, dict] = {}
    old: set[str] = set()
    for f in files:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
        stamp = m.group(1) if m else None
        for rec in _records(f):
            k = job_key(rec["url"])
            if stamp and not day and stamp < cutoff:
                old.add(k)
                continue
            fresh[k] = {**rec, "_collected": stamp} if stamp else rec
    return list(fresh.values()), len(old - set(fresh))


def _backfill_once() -> None:
    """
    seen.csv rows written before the judged column existed have it empty,
    which would make every earlier role look never-judged and send all of them
    to the model again. Once, fill it in from the model's cache: a role counts
    as judged if any link it was collected under has an answer there.
    """
    if BACKFILLED.exists() or not ledger.SEEN_CSV.exists():
        return
    cached = {k.split("|", 1)[0] for k in (_read(CACHE, {}) or {})}
    roles: set[str] = set()
    sources = list(INBOX.glob("job-collector-*.json")) + list((INBOX / "archive").glob("*.json"))
    sources += list(HERE.glob("purged-*/**/*.json"))
    for f in sources:
        for rec in _records(f):
            if job_key(rec["url"]) in cached:
                k = ledger.role_key(rec.get("company", ""), rec.get("title", ""))
                if k:
                    roles.add(k)
    n = ledger.backfill_judged(roles)
    try:
        BACKFILLED.write_text(f"{dt.datetime.now().isoformat(timespec='seconds')} {n} rows\n",
                              encoding="utf-8")
    except OSError:
        pass


def split(jobs: list[dict], record: bool = False) -> dict[str, list]:
    """
    Sort jobs into the buckets above (all but too_old, which load() counts).
    record=True also logs them in history/seen.csv; only the ranker does, so
    looking at the dashboard never changes what counts as seen.
    Each repeat carries "_judged", the day it was judged before.
    """
    applied_urls, applied_roles = applied_index()
    applied_urls = {job_key(u) for u in applied_urls} | set(applied_urls)
    applied_by_role = ledger.applied_role_keys()

    def applied(j: dict) -> str:
        when = built_before(j.get("company", ""), j.get("title", ""), applied_roles)
        if when:
            return f"built {when}"
        u = (j.get("url") or "").split("?")[0].rstrip("/").lower()
        if job_key(j.get("url", "")) in applied_urls or u in applied_urls:
            return "already applied"
        if ledger.role_key(j.get("company", ""), j.get("title", "")) in applied_by_role:
            return "already applied (same role, new link)"
        return ""

    hist: dict = {}
    if getattr(config, "SEEN_DAYS", 30) > 0:
        try:
            _backfill_once()
            if record:
                ledger.record_seen(jobs)
            hist = ledger.history()
        except OSError as e:
            print(f"  ! job history unreadable ({e}); not dropping repeats this run")

    out: dict[str, list] = {"applied": [], "no_description": [], "repeat": [], "to_judge": []}
    for j in jobs:
        if applied(j):
            out["applied"].append(j)
        elif not (j.get("description") or "").strip():
            out["no_description"].append(j)
        elif (when := ledger.repeat_of(j, hist)):
            out["repeat"].append({**j, "_judged": when})
        else:
            out["to_judge"].append(j)
    return out


def summary(day: str | None = None) -> dict:
    """The whole picture as numbers, without recording anything."""
    jobs, too_old = load(day)
    b = split(jobs)
    counts = {k: len(v) for k, v in b.items()}
    counts["too_old"] = too_old
    counts["collected"] = len(jobs) + too_old
    return counts


def explain(counts: dict) -> str:
    """ "86 to judge, of 137 collected: 42 already judged on an earlier day, ..." """
    parts = []
    for key in ("repeat", "applied", "no_description", "too_old"):
        n = counts.get(key, 0)
        if n:
            parts.append(f"{n} {REASON[key].format(days=config.MAX_POOL_AGE_DAYS)}")
    head = f"{counts.get('to_judge', 0)} to judge, of {counts.get('collected', 0)} collected"
    return head + (": " + ", ".join(parts) if parts else "")
