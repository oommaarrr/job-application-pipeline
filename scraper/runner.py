"""
Query rotation with abandon-on-drift, then gating and ranking.

Flow per query:
    page 1..N -> score each title -> feed the DriftDetector
              -> the moment the rolling window mean drops below the floor,
                 abandon this query, log how deep it got, move to the next.

Then, once collecting is done:
    dedupe -> fetch descriptions -> German gate -> years extraction -> rank.

Ranking is primarily by fewest required years (your stated main measurement),
with relevance as the tiebreak.
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import time
from dataclasses import dataclass, field

import config
from jobkey import job_key
import experience
import language_gate
import scoring
from fetchers import Job, build_sources


def load_applied() -> set[str]:
    """
    URLs already applied to, written by the extension through serve.py.

    Compared on the URL without its query string, the same key the collector
    dedupes on, so a tracking parameter can't smuggle a job back onto the list.
    """
    import ledger
    keys = ledger.applied_urls()
    path = pathlib.Path(__file__).parent / config.APPLIED_FILE
    if not path.exists():
        return keys
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return keys
    urls = payload.get("applied", payload) if isinstance(payload, dict) else payload
    return keys | {job_key(u) for u in urls if u}


def _url_key(url: str) -> str:
    """The one canonical form of a job URL, shared by applied and history."""
    return job_key(url)


def load_history() -> dict[str, str]:
    """
    Every job URL ever collected, mapped to the date it was first seen.

    Returned as it is on disk, before this run is folded in, so the caller can
    tell "seen in an earlier scrape" apart from "seen ten seconds ago by this
    very run".
    """
    path = pathlib.Path(__file__).parent / config.HISTORY_FILE
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt history is not worth failing a scan over. Worst case a
        # handful of jobs get reviewed twice, which is the old behaviour.
        print(f"⚠ {config.HISTORY_FILE} unreadable, treating every job as new")
        return {}
    if not isinstance(payload, dict):
        return {}
    return {k: str(v) for k, v in payload.items() if k}


def save_history(history: dict[str, str], jobs, today: str) -> int:
    """
    Record today's sightings, never overwriting an older first-seen date.

    Written atomically through a temp file: a half-written history that fails
    to parse would silently reset the whole memory on the next run.
    """
    added = 0
    for j in jobs:
        key = _url_key(j.url)
        if not key or key in history:
            continue
        history[key] = today
        added += 1
    path = pathlib.Path(__file__).parent / config.HISTORY_FILE
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(history, indent=2, sort_keys=True),
                       encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        print(f"⚠ could not write {config.HISTORY_FILE}: {e}")
        return 0
    return added


@dataclass
class QueryRun:
    query: str
    source: str
    results: int = 0
    kept: int = 0
    pages: int = 0
    mean_score: float = 0.0
    drifted: bool = False
    drifted_at: int | None = None
    note: str = ""

    blocked: bool = False

    @property
    def status(self) -> str:
        # "blocked" must never be shown as "no results" — the first is a network
        # problem, the second is a verdict on the search term. Confusing them
        # would make you retire a query that actually works.
        if self.blocked:
            return "blocked / timed out"
        if self.results == 0:
            return "no results"
        if self.drifted:
            return f"drifted at #{self.drifted_at}"
        return "completed"


def load_imported(seen: set[str]) -> tuple[list[Job], list[QueryRun]]:
    """
    Read whatever the Chrome extension exported into inbox/.

    This runs FIRST and never touches the network, so a throttled or slow live
    source can't stop you from seeing jobs you already collected by hand.
    """
    from fetchers import Imported

    src = Imported()
    if not src.available:
        return [], []

    jobs: list[Job] = []
    per_source: dict[str, QueryRun] = {}
    for job in src.fetch_page(config.QUERIES[0], 1):
        ts = scoring.score_title(job.title)
        job.score, job.score_detail = ts.score, ts
        run = per_source.setdefault(
            job.source, QueryRun(query="(extension export)", source=job.source))
        run.results += 1
        if job.key in seen:
            continue
        seen.add(job.key)
        jobs.append(job)
        run.kept += 1

    for run in per_source.values():
        run.pages = 1
        kept = [j for j in jobs if j.source == run.source]
        run.mean_score = round(sum(j.score for j in kept) / len(kept), 3) if kept else 0.0
        print(f"│    {run.source}: {run.results} imported · {run.kept} new · "
              f"mean {run.mean_score:.2f}")
    return jobs, list(per_source.values())


def collect(sources, seen: set[str] | None = None) -> tuple[list[Job], list[QueryRun]]:
    """Rotate through QUERIES on each source, abandoning drifted queries."""
    seen = seen if seen is not None else set()
    jobs: list[Job] = []
    runs: list[QueryRun] = []

    for src in sources:
        print(f"\n╭─ {src.name}")
        dead = False          # once a source blocks, stop hammering it
        for query in config.QUERIES:
            if dead:
                runs.append(QueryRun(query=query, source=src.name, blocked=True,
                                     note="source blocked earlier in this run"))
                continue
            run = QueryRun(query=query, source=src.name)
            det = scoring.DriftDetector()
            print(f"│  ▸ “{query}”", flush=True)

            for page in range(1, config.MAX_PAGES_PER_QUERY + 1):
                batch = src.fetch_page(query, page)
                run.pages = page
                # blocked is sticky now. The fetcher only raises it after its
                # own retries and cooldowns have failed, so clearing it here to
                # "give the next query a chance" would just mean walking every
                # remaining query into the same wall, one long timeout at a time.
                if getattr(src, "blocked", False):
                    run.blocked = True
                    break
                if not batch:
                    if page == 1:
                        run.note = "no results on page 1"
                    break

                for job in batch:
                    ts = scoring.score_title(job.title)
                    job.score, job.score_detail = ts.score, ts
                    det.add(ts.score)
                    run.results += 1

                    if job.key in seen:
                        continue
                    seen.add(job.key)
                    jobs.append(job)
                    run.kept += 1

                run.mean_score = round(det.mean, 3)

                if det.has_drifted():
                    run.drifted = True
                    run.drifted_at = det.drifted_at
                    print(f"│    ↳ drift detected at result #{det.drifted_at} "
                          f"(window mean {det.mean:.2f} < {config.DRIFT_THRESHOLD}) "
                          f"— abandoning")
                    break

                if page < config.MAX_PAGES_PER_QUERY:
                    time.sleep(0.1)
                    from fetchers import _sleep
                    _sleep()

            # The fetcher already retried and cooled down before setting this,
            # so by the time it reports blocked it has genuinely given up.
            if run.blocked:
                dead = True
                print(f"│    {src.name} is still refusing after its cooldowns — "
                      f"skipping its remaining queries this run")
            print(f"│    {run.results} seen · {run.kept} new · "
                  f"mean {run.mean_score:.2f} · {run.status}")
            runs.append(run)
        print("╰─")

    return jobs, runs


def enrich_and_rank(jobs: list[Job], sources) -> list[Job]:
    """Fetch descriptions, apply the German gate, extract years, then rank."""
    by_source = {s.name: s for s in sources}

    # Only spend detail fetches on titles that are actually on-profile.
    candidates = [j for j in jobs if j.score_detail.on_profile]
    candidates.sort(key=lambda j: -j.score)
    to_fetch = candidates[:config.MAX_DETAIL_FETCHES]

    # Only jobs whose source actually goes over the network cost anything here.
    # Inbox jobs already carry their description, and on an --inbox run there is
    # no live source at all — pacing those would burn minutes sleeping between
    # dictionary lookups.
    over_network = [j for j in to_fetch
                    if getattr(by_source.get(j.source), "network", False)]

    if config.FETCH_DESCRIPTIONS and over_network:
        print(f"\n▸ Fetching {len(over_network)} job descriptions "
              f"(of {len(jobs)} collected, {len(candidates)} on-profile)…")
        from fetchers import _sleep
        for i, job in enumerate(over_network, 1):
            job.description = by_source[job.source].fetch_description(job)
            if i % 10 == 0:
                print(f"    {i}/{len(over_network)}")
            _sleep()

    for job in jobs:
        job.language = language_gate.assess_german(job.description)
        job.experience = experience.extract_years(job.description, job.title)

    # Record WHY each job ended up where it did, so the report can show a full
    # accounting rather than only the survivors.
    applied = load_applied()
    # Read BEFORE today's jobs are folded in, so a job this run just collected
    # is not immediately reported as something you have already seen.
    history = load_history()
    today = _dt.date.today().isoformat()

    for j in jobs:
        key = _url_key(j.url)
        first_seen = history.get(key)
        if key in applied:
            j.extras["verdict"] = "applied"
            j.extras["why"] = "already applied"
        elif first_seen and first_seen != today:
            # Dropped rather than re-reviewed. It still appears in the audit
            # with this reason, so a wrongly suppressed job is findable.
            j.extras["verdict"] = "seen-before"
            j.extras["why"] = f"already collected on {first_seen}"
        elif not j.score_detail.on_profile:
            j.extras["verdict"] = "off-profile"
            j.extras["why"] = (
                f"title scored {j.score:.2f}, below the {config.RELEVANCE_FLOOR} "
                f"floor" + (f" — matched {', '.join(j.score_detail.support)} but no "
                            f"core term" if not j.score_detail.core else "")
                + (f"; penalised for {', '.join(j.score_detail.penalties)}"
                   if j.score_detail.penalties else "")
            )
        elif not j.language.accepted:
            j.extras["verdict"] = "german"
            j.extras["why"] = j.language.reason
        elif (j.experience.years is not None
              and j.experience.source == "description"
              and j.experience.years >= config.MAX_YEARS_REQUIRED):
            j.extras["verdict"] = "years"
            j.extras["why"] = (f"requires {j.experience.years}+ years"
                               + (f" — {j.experience.evidence[:120]}"
                                  if j.experience.evidence else ""))
        else:
            j.extras["verdict"] = "passed"
            j.extras["why"] = ""

    # Remember what this run saw, once the verdicts above have already been read
    # against the pre-run history.
    newly_recorded = save_history(history, jobs, today)
    if newly_recorded:
        print(f"  history: +{newly_recorded} new URL(s) recorded "
              f"({len(history)} known in total)")

    kept = [j for j in jobs if j.extras["verdict"] == "passed"]

    # PRIMARY: fewest required years — your stated main measurement.
    # SECONDARY: verified before unverified. A job whose description we never
    #   fetched is not "clean", it's unknown; letting those tie-break to the top
    #   fills the head of the list with the least trustworthy rows.
    # THEN: relevance, then German level.
    kept.sort(key=lambda j: (
        j.experience.sort_key,
        0 if j.language.checked else 1,
        -j.score,
        j.language.level if j.language.level is not None else -1,
    ))
    return kept


def run(live: bool = True):
    """
    live=False processes only what the Chrome extension put in inbox/ — no
    network at all. Use it when you've just collected by hand and want output
    immediately, or when a live source is throttled.
    """
    print("=" * 70)
    print(f"  Job scan · {config.LOCATION} · German cap "
          f"{language_gate.LEVEL_NAMES[config.GERMAN_CAP]}"
          + (f" · {len(config.QUERIES)} queries" if live else " · inbox only"))
    print("=" * 70)

    # Imported jobs first, always. They cost nothing and are already complete,
    # so a slow or blocked live source can never stop you seeing them.
    print("\n╭─ Inbox (Chrome extension)")
    seen: set[str] = set()
    jobs, runs = load_imported(seen)
    if not jobs:
        print("│    empty — nothing exported into inbox/ yet")
    print("╰─")

    sources = []
    if live:
        sources = [s for s in build_sources() if s.name != "Imported"]
        live_jobs, live_runs = collect(sources, seen)
        jobs += live_jobs
        runs += live_runs

    ranked = enrich_and_rank(jobs, sources)

    for s in sources:
        if hasattr(s, "close"):
            s.close()

    return jobs, ranked, runs
