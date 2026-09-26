"""
Two small, permanent logs that survive "Erase everything".

    history/applied.csv   every job marked applied: date, company, title, url
    history/seen.csv      every job collected in the last SEEN_DAYS days:
                          first_seen, company, title, judged (the day the local
                          model first judged it; empty if it never has)

Why CSV: they are append-mostly lists of short rows that a person may want to
open (Numbers, Excel, a text editor) and that must stay small. A row is about
80 bytes, so a year of applications is tens of KB, and 30 days of scraping at
200 jobs a day is well under half a megabyte. JSON would be larger and
unreadable by hand; SQLite would be smaller but opaque.

Why they are separate from everything Erase touches: "start fresh" means "stop
showing me these postings", never "forget what I applied to". The applied log
is kept forever. The seen log keeps SEEN_DAYS days and prunes itself on every
write, so it never grows past a month.

Matching is on the ROLE, not the link: (company, title) normalised by
applied_index.role_key, the same rule the build uses, so a posting that comes
back under a new URL or on another board is still recognised.
"""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import io
import json
import os
import pathlib

from applied_index import role_key
from jobkey import job_key
from platform_util import file_lock

HERE = pathlib.Path(__file__).parent
HISTORY = HERE / "history"
APPLIED_CSV = HISTORY / "applied.csv"
SEEN_CSV = HISTORY / "seen.csv"
_LOCK = HISTORY / ".lock"

APPLIED_FIELDS = ["date", "company", "title", "url"]
SEEN_FIELDS = ["first_seen", "company", "title", "judged"]


def _seen_days() -> int:
    try:
        import config
        return int(getattr(config, "SEEN_DAYS", 30))
    except Exception:                                    # noqa: BLE001
        return 30


@contextlib.contextmanager
def _locked():
    """One writer at a time across processes: the bridge and the ranker both write."""
    with file_lock(_LOCK):
        yield


def _read(path: pathlib.Path, fields: list[str]) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [{k: (r.get(k) or "").strip() for k in fields}
            for r in csv.DictReader(io.StringIO(text))]


def _write(path: pathlib.Path, fields: list[str], rows: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    os.replace(tmp, path)


def _today() -> str:
    return dt.date.today().isoformat()


# ------------------------------------------------------------------ applied
def applied_rows() -> list[dict]:
    return _read(APPLIED_CSV, APPLIED_FIELDS)


def applied_urls() -> set[str]:
    return {job_key(r["url"]) for r in applied_rows() if r["url"]}


def applied_role_keys() -> dict[str, str]:
    """role key -> date applied, for rows that have a company and a title."""
    out: dict[str, str] = {}
    for r in applied_rows():
        k = role_key(r["company"], r["title"])
        if k:
            out.setdefault(k, r["date"])
    return out


def add_applied(jobs: list[dict]) -> tuple[int, int]:
    """
    Append jobs ({url, company?, title?}) not already on the list, by link.
    A repeat that brings a company or title the first entry lacked fills it in.
    Returns (added, total).
    """
    with _locked():
        rows = applied_rows()
        by_key = {job_key(r["url"]): r for r in rows if r["url"]}
        added = 0
        for j in jobs:
            url = (j.get("url") or "").strip()
            if not url:
                continue
            k = job_key(url)
            if k in by_key:
                row = by_key[k]
                for f in ("company", "title"):
                    if not row[f] and (j.get(f) or "").strip():
                        row[f] = j[f].strip()
                continue
            row = {"date": j["date"] if "date" in j else _today(),
                   "company": (j.get("company") or "").strip(),
                   "title": (j.get("title") or "").strip(), "url": url}
            rows.append(row)
            by_key[k] = row
            added += 1
        _write(APPLIED_CSV, APPLIED_FIELDS, rows)
        return added, len(rows)


# --------------------------------------------------------------------- seen
def _prune(rows: list[dict]) -> list[dict]:
    cutoff = (dt.date.today() - dt.timedelta(days=_seen_days())).isoformat()
    return [r for r in rows if r["first_seen"] >= cutoff]


def history() -> dict[str, dict]:
    """role key -> {"first": first collected, "judged": first judged or ""}."""
    out: dict[str, dict] = {}
    for r in _prune(_read(SEEN_CSV, SEEN_FIELDS)):
        k = role_key(r["company"], r["title"])
        if not k:
            continue
        cur = out.setdefault(k, {"first": r["first_seen"], "judged": r["judged"]})
        cur["first"] = min(cur["first"], r["first_seen"])
        if r["judged"] and (not cur["judged"] or r["judged"] < cur["judged"]):
            cur["judged"] = r["judged"]
    return out


def repeat_of(job: dict, hist: dict[str, dict]) -> str:
    """
    The day this role was JUDGED before, if it is a repeat worth dropping; ""
    otherwise. A repeat is a role the local model already judged on an earlier
    day than this copy was collected. Collected-but-never-judged (the
    pool was erased before ranking, the description was missing that day) is
    not a repeat: it never had its chance, so it goes to the model now.
    Decision, 25 September 2026, after a role collected on the 21st and never
    ranked was dropped as "already had its chance".
    """
    h = hist.get(role_key(job.get("company", ""), job.get("title", "")))
    if not h or not h["judged"]:
        return ""
    # Judged on an EARLIER day than this copy was collected. Judged today (a
    # second ranking of the same pool) is the same pool, not a repeat: dropping
    # it would silently shrink a re-run. Its answer is cached anyway.
    collected = job.get("_collected") or _today()
    return h["judged"] if h["judged"] < collected else ""


def mark_judged(jobs: list[dict], when: str | None = None) -> int:
    """Record that the local model has judged these roles. Keeps the earliest
    date; adds a row for a role not logged yet. Returns how many changed."""
    day = when or _today()
    with _locked():
        rows = _prune(_read(SEEN_CSV, SEEN_FIELDS))
        index = {role_key(r["company"], r["title"]): r for r in rows}
        changed = 0
        for j in jobs:
            company, title = (j.get("company") or "").strip(), (j.get("title") or "").strip()
            k = role_key(company, title)
            if not k:
                continue
            row = index.get(k)
            if row is None:
                row = {"first_seen": j.get("_collected") or day, "company": company,
                       "title": title, "judged": ""}
                rows.append(row)
                index[k] = row
            if not row["judged"] or day < row["judged"]:
                row["judged"] = day
                changed += 1
        if changed:
            _write(SEEN_CSV, SEEN_FIELDS, rows)
        return changed


def needs_judged_backfill() -> bool:
    """A seen.csv from before the judged column existed."""
    try:
        with open(SEEN_CSV, encoding="utf-8") as fh:
            return "judged" not in fh.readline()
    except OSError:
        return False


def backfill_judged(judged_role_keys: set[str]) -> int:
    """One-off, for a seen.csv written before the judged column: mark the roles
    the model's cache shows were judged (dated by when they were first seen,
    the best date available). Everything else stays unjudged."""
    with _locked():
        rows = _prune(_read(SEEN_CSV, SEEN_FIELDS))
        n = 0
        for r in rows:
            if not r["judged"] and role_key(r["company"], r["title"]) in judged_role_keys:
                r["judged"] = r["first_seen"]
                n += 1
        _write(SEEN_CSV, SEEN_FIELDS, rows)
        return n


def seen_first() -> dict[str, str]:
    """role key -> the first date it was collected, within the window."""
    out: dict[str, str] = {}
    for r in _prune(_read(SEEN_CSV, SEEN_FIELDS)):
        k = role_key(r["company"], r["title"])
        if k and (k not in out or r["first_seen"] < out[k]):
            out[k] = r["first_seen"]
    return out


def record_seen(jobs: list[dict], when: str | None = None) -> int:
    """
    Log each job's role the first time it is collected. A job may carry its own
    "_collected" date (the day of the inbox file it came from); otherwise today.
    Never moves a first_seen date later. Prunes rows older than the window.
    Returns how many new roles were logged.
    """
    with _locked():
        rows = _prune(_read(SEEN_CSV, SEEN_FIELDS))
        index = {role_key(r["company"], r["title"]): r for r in rows}
        added = 0
        for j in jobs:
            company, title = (j.get("company") or "").strip(), (j.get("title") or "").strip()
            k = role_key(company, title)
            if not k:
                continue
            day = j.get("_collected") or when or _today()
            if k in index:
                if day < index[k]["first_seen"]:
                    index[k]["first_seen"] = day
                continue
            row = {"first_seen": day, "company": company, "title": title, "judged": ""}
            rows.append(row)
            index[k] = row
            added += 1
        _write(SEEN_CSV, SEEN_FIELDS, rows)
        return added


def seen_earlier(job: dict, first: dict[str, str]) -> str:
    """
    The date this role was first collected, if that was BEFORE the day this copy
    was collected; "" otherwise. Same-day repeats (a second scrape, a re-scrape
    after Erase) are not duplicates: they are the same pool.
    """
    k = role_key(job.get("company", ""), job.get("title", ""))
    when = first.get(k, "")
    collected = job.get("_collected") or _today()
    return when if when and when < collected else ""


# ---------------------------------------------------------------- migration
def migrate_applied_json(applied_json: pathlib.Path, lookup) -> int:
    """
    One-time: turn the old applied.json (a bare list of links) into applied.csv,
    filling company and title from `lookup(url) -> dict | None` where it can.
    The old file is renamed to applied.json.migrated, never deleted.
    """
    if not applied_json.exists():
        return 0
    try:
        payload = json.loads(applied_json.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return 0
    urls = payload.get("applied", []) if isinstance(payload, dict) else payload
    jobs = []
    for u in urls:
        u = u.get("url") if isinstance(u, dict) else u
        if not u:
            continue
        found = lookup(u) or {}
        jobs.append({"url": u, "company": found.get("company", ""),
                     "title": found.get("title", ""), "date": found.get("_collected", "")})
    added, _ = add_applied(jobs)
    applied_json.rename(applied_json.with_name(applied_json.name + ".migrated"))
    return added


if __name__ == "__main__":
    a = applied_rows()
    s = seen_first()
    print(f"applied.csv  {len(a)} jobs  ({APPLIED_CSV.stat().st_size if APPLIED_CSV.exists() else 0} bytes)")
    print(f"seen.csv     {len(s)} roles in the last {_seen_days()} days  "
          f"({SEEN_CSV.stat().st_size if SEEN_CSV.exists() else 0} bytes)")
