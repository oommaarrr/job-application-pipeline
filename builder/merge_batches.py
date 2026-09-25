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
  * appends every shard's built[] and dropped[], de-duplicating by company
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
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return default


def _company_key(entry: dict) -> str:
    return (entry.get("company") or entry.get("title") or "").strip().lower()


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
    fragments = sorted(day_dir.glob("batch.shard-*.json"))

    built: list[dict] = list(existing.get("built", []))
    dropped: list[dict] = list(existing.get("dropped", []))
    seen_built = {_company_key(e) for e in built}
    seen_dropped = {_company_key(e) for e in dropped}
    n_prior = len(built)

    for frag in fragments:
        data = _read(frag, {})
        for e in data.get("built", []):
            k = _company_key(e)
            if k and k not in seen_built:
                seen_built.add(k)
                built.append(e)
        for e in data.get("dropped", []):
            k = _company_key(e)
            if k and k not in seen_dropped:
                seen_dropped.add(k)
                dropped.append(e)

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
          f"{len(built)} built ({n_prior} carried over), {len(dropped)} dropped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
