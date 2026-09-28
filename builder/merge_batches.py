#!/usr/bin/env python3
"""
Merge the batch fragments written by parallel build shards into one batch.json.

When run_batch.py builds in parallel, each Claude session is one shard and writes
its own applications/<date>/batch.shard-<i>.json rather than the shared
batch.json, because two sessions writing the same file at once would corrupt it.
This stitches them back together after both have finished:

    .venv/bin/python merge_batches.py applications/2026-09-20

  * carries through any entries already in batch.json (an earlier round or an
    earlier batch the same day), so a resumed run never loses the morning's work
  * appends every shard's built[] and dropped[], de-duplicating by role (the
    posting link, else company and title). By company alone, two different
    roles both posted by "Confidential" became one.
  * recovers every finished application on disk that no record lists (see
    recover_orphans)
  * re-orders the built list by the ranker's own global fit order (the position
    in out/ranked.json), so the merged report reads top-fit-first even though
    each shard only ever saw its own interleaved slice
  * renumbers rank 1..N across the whole list
  * writes batch.json and removes the shard fragments

It is a no-op-safe merge: run it with no fragments present and it just
renumbers and rewrites whatever batch.json already holds.
"""

from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
CONFIG = json.loads((HERE / "pipeline.json").read_text(encoding="utf-8"))
# Relative to this folder, so it works from any working directory.
SCRAPER = (HERE / CONFIG["scraper_root"]).resolve()
sys.path.insert(0, str(SCRAPER))
from jobkey import job_key  # noqa: E402


def _read(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _company_key(entry: dict) -> str:
    return (entry.get("company") or entry.get("title") or "").strip().lower()


def _role_key(entry: dict) -> str:
    if entry.get("url"):
        return "url:" + job_key(entry["url"])
    return "role:" + _company_key(entry) + "|" + (entry.get("title") or "").strip().lower()


def _slug(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _big_pdf(d: pathlib.Path, pattern: str) -> bool:
    return any(f.is_file() and f.stat().st_size > 8 * 1024 for f in d.glob(pattern))


def _folder_of(entry: dict) -> str:
    # Either separator: a record written on Windows can carry "Acme\\cv.pdf".
    for f in (entry.get("files") or {}).values():
        if isinstance(f, str) and ("/" in f or "\\" in f):
            return f.replace("\\", "/").split("/", 1)[0]
    return ""


def _candidates() -> list[dict]:
    """Every role Claude could have been building: the shortlists, then the ranking."""
    out: list[dict] = []
    for f in sorted(HERE.glob("shortlist*.json")):
        rows = _read(f, [])
        out += [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    rows = _read(SCRAPER / "out" / "ranked.json", [])
    out += [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    return out


def recover_orphans(day_dir: pathlib.Path, built: list[dict]) -> list[dict]:
    """
    Entries for finished applications (both PDFs on disk) that no record lists.

    A session that runs out of usage stops wherever it is, and batch.json (or a
    shard's fragment) is written last. On 25 September 2026 shard 2 finished
    six applications and hit the limit before writing its fragment: the PDFs
    were on disk, the record listed none of them, so they never appeared on the
    Applications page, never counted as built, and the next day's run built all
    six again. The documents are the proof of work, so the record follows them.

    Each finished folder is identified by the entry.json Claude writes beside
    the PDFs, else by matching the folder name to a role it was given.
    """
    listed = {_folder_of(e) for e in built}
    listed_roles = {_role_key(e) for e in built}
    cands = None
    found: list[dict] = []
    for d in sorted(p for p in day_dir.iterdir() if p.is_dir()):
        if d.name in listed or d.name.startswith((".", "_")) or d.name == "archive":
            continue
        cv = sorted(f for f in d.glob("*_CV_*.pdf") if f.stat().st_size > 8 * 1024)
        cl = sorted(f for f in d.glob("*_CoverLetter_*.pdf") if f.stat().st_size > 8 * 1024)
        if not (cv and cl):
            continue                      # unfinished: left for the next attempt
        entry = _read(d / "entry.json", {})
        entry = entry if isinstance(entry, dict) else {}
        if not entry.get("company"):
            if cands is None:
                cands = _candidates()
            slug = _slug(d.name)
            match = next((c for c in cands if _slug(c.get("company", "")) == slug), None) \
                or next((c for c in cands if slug and _slug(c.get("company", "")).startswith(slug)), None)
            if not match and len(slug) >= 5:
                # A recruiter posting: the board's company is the agency ("Jack &
                # Jill") and the employer only appears in the title. Accepted only
                # when exactly one role fits, so a short, common folder name can
                # never mark a different role as built.
                hits = {c.get("url") or c.get("title"): c for c in cands
                        if slug in _slug(c.get("title", ""))}
                match = next(iter(hits.values())) if len(hits) == 1 else None
            letter = next(iter(d.glob("letter_*.json")), None)
            subject = (_read(letter, {}) or {}).get("subject", "") if letter else ""
            title = subject.split(":", 1)[-1].strip() if subject else ""
            if match:
                named = _slug(match.get("company", "")).startswith(slug)
                entry = {"company": match.get("company", d.name) if named else d.name,
                         "title": match.get("title") or title,
                         "location": match.get("location", ""), "url": match.get("url", "")}
            else:
                entry = {"company": d.name, "title": title, "location": "", "url": ""}
            entry.setdefault("why", "")
        flags = list(entry.get("flags") or [])
        flags.append("Recovered from the documents on disk: the session stopped "
                     "(usage limit or stop) before it wrote this role into the batch record.")
        entry = {**entry, "flags": flags,
                 "files": {"CV": f"{d.name}/{cv[0].name}",
                           "cover letter": f"{d.name}/{cl[0].name}"}}
        if _role_key(entry) in listed_roles:
            continue
        listed_roles.add(_role_key(entry))
        found.append(entry)
    return found


def _global_rank_map() -> dict[str, int]:
    """url key -> position in the ranker's full ordered output (0 = best fit)."""
    ranked = _read(SCRAPER / "out" / "ranked.json", [])
    return {job_key(j.get("url", "")): i for i, j in enumerate(ranked)}


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: merge_batches.py applications/<date>")
        return 2
    day_dir = pathlib.Path(sys.argv[1])
    if not day_dir.is_dir():
        print(f"{day_dir} is not a directory")
        return 2

    target = day_dir / "batch.json"
    existing = _read(target, {})
    existing = existing if isinstance(existing, dict) else {"built": existing}
    fragments = sorted(day_dir.glob("batch.shard-*.json"))

    built: list[dict] = list(existing.get("built", []))
    dropped: list[dict] = list(existing.get("dropped", []))
    seen_built = {_role_key(e) for e in built}
    seen_dropped = {_role_key(e) for e in dropped}
    n_prior = len(built)

    for frag in fragments:
        data = _read(frag, {})
        for e in data.get("built", []):
            k = _role_key(e)
            if k and k not in seen_built:
                seen_built.add(k)
                built.append(e)
        for e in data.get("dropped", []):
            k = _role_key(e)
            if k and k not in seen_dropped:
                seen_dropped.add(k)
                dropped.append(e)

    orphans = recover_orphans(day_dir, built)
    built += orphans
    # A role that was built is not also dropped.
    built_keys = {_role_key(e) for e in built}
    dropped = [e for e in dropped if _role_key(e) not in built_keys]

    # Order the newly added entries by true global fit; leave any pre-existing
    # entries (already ranked in an earlier round) ahead of them, in place.
    gr = _global_rank_map()
    head, tail = built[:n_prior], built[n_prior:]
    tail.sort(key=lambda e: gr.get(job_key(e.get("url", "")), 10_000))
    built = head + tail
    for i, e in enumerate(built, 1):
        e["rank"] = i

    merged = dict(existing)
    merged["built"] = built
    merged["dropped"] = dropped
    merged.setdefault("date", day_dir.name)
    merged["pool_size"] = len(_read(SCRAPER / "out" / "ranked.json", [])) or merged.get("pool_size", len(built))
    target.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

    for frag in fragments:
        try:
            frag.unlink()
        except OSError:
            pass

    print(f"merged {len(fragments)} shard fragment(s) -> {target}: "
          f"{len(built)} built ({n_prior} carried over), {len(dropped)} dropped"
          + (f", {len(orphans)} recovered from documents no record listed" if orphans else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
