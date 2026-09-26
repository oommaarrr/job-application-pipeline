#!/usr/bin/env python3
"""
Local ingest server — the bridge between the Chrome extension and the pipeline.

A Chrome extension cannot write to this folder. `chrome.downloads` can only
write inside Downloads, which is why v1 made you save a file and move it by
hand. This server removes that step: the extension POSTs what it collected, we
write it into inbox/ and rerank immediately.

    ./.venv/bin/python serve.py            run in the foreground
    ./install-agent.sh                     install as a launchd agent

Endpoints (all on 127.0.0.1 only, never exposed to the network):

    GET  /status    counts, and how the last rank went
    POST /ingest    {"jobs": [...]}  -> merge into today's inbox file, rerank
    POST /applied   {"urls": [...]}  -> add to history/applied.csv, rerank

Today's collection is the working set. Older inbox files are moved into
inbox/archive/ on first ingest of a new day, so a rank always reflects what you
just scraped rather than everything you have ever scraped. Nothing is deleted.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import os
import subprocess
import urllib.request
import threading
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Started with any Python (`python serve.py` on a fresh download)? Set the
# project up and continue inside its own environment, instead of crashing on
# the first package that system Python does not have.
if __name__ == "__main__":
    import bootstrap
    bootstrap.ensure(__file__)

ROOT = pathlib.Path(__file__).parent.resolve()
# The repo root, one level up from scraper/. Everything shared (web/, builder/,
# profiles/) hangs off this, so the checkout can live anywhere. Before
# 24 September 2026 these were absolute paths into ~/Desktop and the project
# only ran on one laptop.
REPO = ROOT.parent
WEB = REPO / "web"


def _load_local_env(path: pathlib.Path) -> None:
    """
    KEY=VALUE lines from local.env (gitignored) into the environment, without
    overriding anything already set. The same settings then apply however the
    bridge was started: ./start.sh, a login agent, or by hand.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_local_env(REPO / "local.env")
# The ranking profile. PROFILE_DIR lets a user keep several (profiles/me,
# profiles/side-quest) and pick one per run without editing any source.
from profile_dir import profile_dir as _profile_dir
import platform_util as pu
import ollama_model as om
# Every Python child (ranker, Arbeitnow, the build and what Claude runs) reads
# and writes UTF-8, whatever the Windows code page is.
os.environ.update(pu.utf8_env())
PROFILE_DIR = _profile_dir()
PROFILE = PROFILE_DIR / "profile.md"
INBOX = ROOT / "inbox"
ARCHIVE = INBOX / "archive"
APPLIED = ROOT / "applied.json"
OUT = ROOT / "out"
PYTHON = pu.venv_python(ROOT / ".venv")
if not PYTHON.exists():
    # Started with some other interpreter (a hand-made venv, a CI runner).
    PYTHON = pathlib.Path(sys.executable)
# Where /apply-batch writes its results. Read-only from here: the bridge shows
# what was built so the popup can flag it, and never writes into that project.
BATCHES = REPO / "builder" / "applications"
# Loopback by default. PORT is overridable so a second copy of the project can
# run alongside the first — which is exactly how the open-source version was
# developed, with the author's own pipeline still live on 8765.
HOST = os.environ.get("BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("BRIDGE_PORT", "8765"))

# One rank at a time. Collecting a page fires an ingest per page, and two
# concurrent runs would race on out/jobs.json.
_rank_lock = threading.Lock()
_last_rank: dict = {"ran_at": None, "matches": None, "collected": None, "error": None}


def _today() -> str:
    return dt.date.today().isoformat()


def _active_inbox() -> pathlib.Path:
    return INBOX / f"job-collector-{_today()}.json"


def _archive(path: pathlib.Path) -> None:
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    target = ARCHIVE / path.name
    if target.exists():
        target = ARCHIVE / f"{path.stem}-{dt.datetime.now():%H%M%S}{path.suffix}"
    path.rename(target)


def _consolidate() -> list[dict]:
    """
    Leave exactly one live inbox file: today's.

    Files stamped with today's date are absorbed rather than set aside, so a
    manual export or an earlier session's collection is not lost the first time
    the bridge writes. Older days move to archive/, which run.py does not glob,
    so a rank reflects this session instead of every scrape ever made. Nothing
    is deleted either way.
    """
    active = _active_inbox()
    absorbed: list[dict] = []
    for path in sorted(INBOX.glob("*.json")):
        if path.resolve() == active.resolve():
            continue
        stamp = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
        if stamp and stamp.group(1) == _today():
            payload = _read_json(path, {})
            records = payload.get("jobs", []) if isinstance(payload, dict) else payload
            absorbed.extend(r for r in records if isinstance(r, dict))
        _archive(path)
    return absorbed


def _read_json(path: pathlib.Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: pathlib.Path, data) -> None:
    """
    Write through a temp file and rename, so a crash, a full disk or a killed
    bridge mid-write leaves the previous copy whole instead of half a file.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json_safely(path: pathlib.Path, default):
    """
    Like _read_json, for files we are about to REWRITE (the inbox, applied.json).

    _read_json turns an unreadable file into the default, which is right for a
    display and wrong for a merge: reading a damaged inbox as empty and writing
    the merge back would replace the whole day's collection with one page. So a
    file that exists but does not parse is moved aside, kept, and reported.
    """
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        # Not *.json, so nothing globbing the inbox picks the broken copy up again.
        keep = path.with_name(f"{path.name}.damaged-{dt.datetime.now():%Y%m%d-%H%M%S}")
        try:
            path.rename(keep)
        except OSError:
            pass
        _event("data", "failed", f"{path.name} could not be read ({e}); kept it as {keep.name} and started a fresh one",
               fix=f"Nothing is lost: the damaged copy is {keep}.")
        return default


# ==================================================================== audit
#
# One line per thing that happened, in out/events.jsonl: every collection,
# rank, build, reset and failure, with what to do about a failure. It answers
# "what happened while I was away" without reading four log files, and it is
# what the dashboard's Activity panel and its Retry buttons are built from.
# Append-only; trimmed to the newest EVENTS_KEEP lines.
EVENTS = ROOT / "out" / "events.jsonl"
EVENTS_KEEP = 2000
_events_lock = threading.Lock()

# The action behind each step's Retry button: which endpoint to POST.
RETRY = {"arbeitnow": "/arbeitnow", "rank": "/rank", "build": "/build",
         "scrape": "/trigger"}


def _event(step: str, status: str, message: str, fix: str = "", **extra) -> dict:
    """Record one event. status: started | ok | failed | stopped | info."""
    row = {"at": dt.datetime.now().isoformat(timespec="seconds"), "step": step,
           "status": status, "message": message}
    if fix:
        row["fix"] = fix
    if status in ("failed", "stopped") and step in RETRY:
        row["retry"] = RETRY[step]
    row.update({k: v for k, v in extra.items() if v is not None})
    with _events_lock:
        try:
            EVENTS.parent.mkdir(parents=True, exist_ok=True)
            with EVENTS.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if EVENTS.stat().st_size > 1_500_000:
                lines = EVENTS.read_text(encoding="utf-8").splitlines()[-EVENTS_KEEP:]
                EVENTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass
    print(f"  [{step}] {status}: {message}")
    return row


def recent_events(n: int = 60) -> list[dict]:
    try:
        lines = EVENTS.read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out[::-1]          # newest first


def _step_state() -> dict:
    """
    The latest outcome of each step, so the dashboard can show one line per
    step ("Build: failed at 14:02 — Claude is signed out. [Retry]") instead of
    making anyone read a log. A later success clears an earlier failure.
    """
    state: dict[str, dict] = {}
    for e in reversed(recent_events(400)):          # oldest first
        if e.get("status") in ("ok", "failed", "stopped", "started") or (
                e.get("status") == "info" and e.get("step") in RETRY):
            state[e["step"]] = e
    return state


# Serialises every read-modify-write of the inbox and applied.json. The server
# is threaded: the extension and Arbeitnow can push at the same moment, and two
# unlocked merges each read the old file and the second write drops the first
# one's jobs.
_data_lock = threading.Lock()


import config
import ledger
import pool_status
from jobkey import job_key as _key


# Page furniture LinkedIn serves instead of a description. Non-empty, plausible
# prose, and worthless: it survives every "is there text" check.
_CHROME_RX = re.compile("|".join([
    "reactivate premium", "select language", "linkedin corporation",
    "get ai-powered advice", "join or sign in", "community guidelines",
]), re.I)


def _is_chrome(text: str) -> bool:
    return bool(text) and bool(_CHROME_RX.search(text))


def _better_description(incoming: str | None, existing: str | None) -> str:
    """
    Which of two descriptions to keep. Never downgrade.

    Real text always beats chrome, whatever order they arrive in. Between two
    real ones the longer wins, because a truncated capture is the common
    failure and a longer one is never worse.
    """
    a, b = (incoming or "").strip(), (existing or "").strip()
    if not a:
        return b
    if not b:
        return a
    if _is_chrome(a) and not _is_chrome(b):
        return b
    if _is_chrome(b) and not _is_chrome(a):
        return a
    return a if len(a) >= len(b) else b


def merge_jobs(incoming: list[dict]) -> tuple[int, int]:
    """Merge into today's inbox file, keyed by URL. Returns (added, total)."""
    with _data_lock:
        return _merge_jobs(incoming)


def _merge_jobs(incoming: list[dict]) -> tuple[int, int]:
    absorbed = _consolidate()
    path = _active_inbox()
    payload = _read_json_safely(path, {"jobs": []})
    if not isinstance(payload, dict):
        payload = {"jobs": payload if isinstance(payload, list) else []}
    existing = {_key(j.get("url", "")): j for j in payload.get("jobs", []) if j.get("url")}

    added = 0
    for job in [*absorbed, *incoming]:
        k = _key(job.get("url", ""))
        if not k or not (job.get("title") or "").strip():
            continue
        if k not in existing:
            added += 1
        prev = existing.get(k, {})
        # A later sighting must never clobber a BETTER description, not merely a
        # non-empty one.
        #
        # 18 September: 67 LinkedIn descriptions were repaired from the guest
        # endpoint, then silently replaced with page furniture an hour later,
        # because the extension still held its own bad copies and re-pushed them
        # all when the next scrape finished. The batch then dropped all 20
        # candidates as untailorable. "Non-empty wins" was not a strong enough
        # rule: chrome text is non-empty.
        existing[k] = {**prev, **job,
                       "description": _better_description(
                           job.get("description"), prev.get("description"))}

    jobs = list(existing.values())
    INBOX.mkdir(exist_ok=True)
    _write_json(path, {"exported_at": dt.datetime.now().isoformat(timespec="seconds"),
                       "count": len(jobs), "jobs": jobs})
    return added, len(jobs)


def _lookup_job(url: str) -> dict | None:
    """Company and title for a link, from today's pool, the archived pools, or
    a built batch. The extension only knows the title of the page you are on."""
    k = _key(url)
    files = [_active_inbox(), *sorted(ARCHIVE.glob("*.json"), reverse=True)] if ARCHIVE.exists() else [_active_inbox()]
    for f in files:
        payload = _read_json(f, {})
        for j in (payload.get("jobs", []) if isinstance(payload, dict) else payload or []):
            if isinstance(j, dict) and j.get("url") and _key(j["url"]) == k:
                stamp = re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
                return {**j, "_collected": stamp.group(1) if stamp else ""}
    for bj in sorted(BATCHES.glob("**/batch.json")) if BATCHES.exists() else []:
        for j in _read_json(bj, {}).get("built", []):
            if j.get("url") and _key(j["url"]) == k:
                return j
    return None


def merge_applied(urls: list[str], jobs: list[dict] | None = None) -> tuple[int, int]:
    """
    Add to history/applied.csv (date, company, title, link). That file survives
    Erase and is kept forever; see ledger.py for why it is CSV.
    """
    given = {_key(j["url"]): j for j in (jobs or []) if isinstance(j, dict) and j.get("url")}
    rows = []
    for u in urls:
        if not u:
            continue
        found = _lookup_job(u) or {}
        g = given.get(_key(u), {})
        rows.append({"url": u, "company": found.get("company") or g.get("company") or "",
                     "title": found.get("title") or g.get("title") or ""})
    with _data_lock:
        return ledger.add_applied(rows)


def _applied_count() -> int:
    return len(ledger.applied_rows())


def built_jobs() -> dict[str, dict]:
    """
    Every role that already has a CV and cover letter, keyed by URL.

    Lets the popup tell you "documents built" while you are looking at the
    posting, which is the moment it matters. Newest batch wins on a repeat.
    """
    out: dict[str, dict] = {}
    if not BATCHES.exists():
        return out
    for path in sorted(BATCHES.glob("*/batch.json")):
        payload = _read_json(path, {})
        for job in payload.get("built", []):
            k = _key(job.get("url", ""))
            if k:
                out[k] = {"company": job.get("company"), "rank": job.get("rank"),
                          "date": payload.get("date") or path.parent.name,
                          "report": str(path.parent / "batch.html")}
    return out


def rerank() -> dict:
    """Run the inbox-only pipeline. Fast now that inbox jobs are not paced."""
    with _rank_lock:
        try:
            proc = subprocess.run(
                [str(PYTHON), "run.py", "--inbox", "--no-open"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300, **pu.quiet_kwargs())
        except (subprocess.TimeoutExpired, OSError) as e:
            _last_rank.update(ran_at=dt.datetime.now().isoformat(timespec="seconds"),
                              error=str(e))
            _event("pool", "failed", f"updating the job list failed: {e}",
                   fix="The jobs are saved. It updates again on the next collection.")
            return dict(_last_rank)
        if proc.returncode != 0:
            _event("pool", "failed", "updating the job list failed: "
                   + ((proc.stderr or "").strip().splitlines() or ["unknown error"])[-1][:200],
                   fix="The jobs are saved. It updates again on the next collection.")

        ranked = _read_json(ROOT / "out" / "jobs.json", [])
        _last_rank.update(
            ran_at=dt.datetime.now().isoformat(timespec="seconds"),
            matches=len(ranked),
            collected=len(_read_json(_active_inbox(), {"jobs": []}).get("jobs", [])),
            error=None if proc.returncode == 0 else (proc.stderr or "")[-400:],
        )
        return dict(_last_rank)


# Reported by the extension while a scrape is in flight.
#
# Guessing at "the scrape has finished" from the collected count not moving
# does not work, and cost a whole run: the extension deliberately pauses three
# to seven minutes between searches so three sites in a row do not look
# mechanical, and any plateau shorter than the longest gap is indistinguishable
# from being done. The count went quiet during the gap before StepStone, the
# batch declared the scrape finished, and built from a third of the pool.
#
# So the extension says so explicitly instead.
_scrape: dict = {"running": False, "done": 0, "total": 0, "at": None}

# The extension's saved searches, mirrored here so the launcher can open one.
# Seeded from the copy on disk, so a restart does not start with nothing.
_searches: list = [q for q in (_read_json(ROOT / "out" / "searches.json", []) or [])
                   if isinstance(q, dict) and q.get("url") and q.get("enabled") is not False]

# Set by POST /trigger, consumed by the extension's GET /trigger poll.
_trigger: str | None = None
# Optional host filter carried with a trigger, e.g. "linkedin.com". Lets a
# manual run cover one site without touching the saved schedule.
_trigger_only: str | None = None

# ------------------------------------------------------------------ autobuild
#
# The missing link. Until now the scrape and the build were joined by a shell
# script that sat in a loop asking "are you finished yet", which meant the
# terminal had to stay open, the window had to be started before the scrape, and
# a run that finished after the wait expired built nothing.
#
# The extension already tells us the moment it is done, on POST /scrape. That is
# an event, so treat it as one: when a run goes from running to not running with
# something in the pool, start the build itself. No polling, no window, and it
# does not matter how the scrape was started.
BUILDER = pathlib.Path(os.environ.get("BUILDER_DIR") or (REPO / "builder"))
BUILD_SCRIPT = BUILDER / "run_batch.py"

# Off by default. Autobuild spends Claude usage on its own the moment a scrape
# finishes, which a first-time user should never discover by surprise. Turn it
# on from the dashboard toggle, or start the bridge with AUTOBUILD=1.
_autobuild = os.environ.get("AUTOBUILD") == "1"
_build: dict = {"running": False, "started": None, "finished": None,
                "reason": None, "pid": None, "log": None}
_build_lock = threading.Lock()

# The number the user asked to build ("build 20"), persisted to disk so the
# tracker keeps showing 20 after the build finishes or the bridge restarts. It
# used to live only in _build["target"], an in-memory value: once the build
# process exited or the bridge was restarted, the number was lost and
# build_progress fell back to config.BUILD_TARGET (15), so a run the user
# launched with 20 displayed "of 15". This file is the durable copy. Absent when
# the run had no explicit target (autobuild builds "everything that fits"), in
# which case the denominator becomes what the run actually considered.
_BUILD_TARGET_MARK = ROOT / "out" / ".build_target"


def _write_build_target(n: int | None) -> None:
    try:
        if n and int(n) > 0:
            _BUILD_TARGET_MARK.write_text(str(int(n)), encoding="utf-8")
        else:
            _BUILD_TARGET_MARK.unlink(missing_ok=True)
    except OSError:
        pass


def _read_build_target() -> int | None:
    try:
        return int(_BUILD_TARGET_MARK.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None

# Per-search results for the current run, newest run only. Written to
# out/scrape_audit.txt on every update so it can be read while a run is going.
_search_log: list = []

# When the pool was last erased ("Start fresh"). A fresh start clears the job
# pool but deliberately keeps the CVs already built, so without this the
# dashboard would keep showing the previous run's build as this cycle's. Their
# instruction, 19 September 2026: erasing the jobs restarts the pipeline's
# flags too. Persisted so it survives a bridge restart; build_progress ignores
# anything built at or before it.
_RESET_MARK = ROOT / "out" / ".cycle_reset"

def _load_reset_at() -> float | None:
    try:
        return float(_RESET_MARK.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None

_reset_at: float | None = _load_reset_at()


def _rank_is_current() -> bool:
    """False when the last ranking predates the last erase. The ranking belongs
    to the pool that was erased, so the funnel and the Fit ranking page must not
    keep showing it as if it were this pool's. The file itself is kept (its
    cache makes the next rank faster); only its numbers stop being shown."""
    f = OUT / "ollama_rank.json"
    try:
        return not (_reset_at is not None and f.stat().st_mtime <= _reset_at)
    except OSError:
        return False

def _load_search_log() -> list:
    """
    Repopulate the per-search table after a bridge restart, so the dashboard
    still shows the last scrape instead of an empty scrape step. Skipped when
    the audit on disk predates the last fresh start, so an erase stays erased.
    """
    f = OUT / "scrape_audit.json"
    try:
        if _reset_at is not None and f.stat().st_mtime <= _reset_at:
            return []
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []

_search_log = _load_search_log()


def _adopt_running_rank() -> None:
    """If a rank_ollama.py is still going (bridge restarted mid-rank), track it."""
    pid = next(iter(pu.find_script_procs(ROOT / "rank_ollama.py")), None)
    if pid:
        _rank.update(running=True, pid=pid, finished=None,
                     started=_rank.get("started"))


def _write_scrape_audit() -> None:
    lines = [f"SCRAPE AUDIT · {dt.datetime.now():%Y-%m-%d %H:%M:%S}",
             f"{len(_search_log)} search(es) finished", ""]
    head = (f"{'search':<40} {'site':<10} {'found':>6} {'new':>5} "
            f"{'desc':>5} {'loaded':>7} {'secs':>5}  note")
    lines += [head, "-" * len(head)]
    tot = {"found": 0, "added": 0, "withDesc": 0}
    for r in _search_log:
        for k in tot:
            tot[k] += int(r.get(k) or 0)
        note = ""
        if not r.get("ok"):
            note = "FAILED: " + (r.get("why") or "unknown")
        elif r.get("scrollDead"):
            note = "the list never scrolled"
        elif r.get("refused"):
            note = f"{r['refused']} page(s) refused"
        elif r.get("userScrolled"):
            # Not a failure, but it means this page does not prove the collector
            # can load the list on its own: a person did it.
            note = "YOU scrolled — auto-scroll unproven here"
        elif r.get("everMoved") is False and int(r.get("found") or 0) < 20:
            # The honest label for what happened on 18 September: the loader
            # reported "auto-scrolled" while never having moved anything.
            note = (f"NOTHING MOVED — {r.get('scrollables', '?')} scrollable "
                    f"element(s) seen, container={r.get('container') or '?'}")
        else:
            note = "auto-scrolled"
        lines.append(
            f"{(r.get('label') or '')[:40]:<40} {(r.get('site') or r.get('host') or '')[:10]:<10} "
            f"{int(r.get('found') or 0):>6} {int(r.get('added') or 0):>5} "
            f"{int(r.get('withDesc') or 0):>5} {int(r.get('loaded') or 0):>7} "
            f"{round((r.get('ms') or 0) / 1000):>5}  {note}")
    lines += ["-" * len(head),
              f"{'TOTAL':<40} {'':<10} {tot['found']:>6} {tot['added']:>5} "
              f"{tot['withDesc']:>5}"]
    # The scroll trace, for anything that came up short. This is the evidence
    # for which element actually moves on a LinkedIn results page, after three
    # separate theories about it turned out to be wrong.
    short = [r for r in _search_log
             if r.get("trace") and int(r.get("found") or 0) < 20]
    for r in short:
        lines += ["", f"SCROLL TRACE · {r.get('label')} "
                      f"(picked container: {r.get('container') or 'unknown'})",
                  f"  {'pass':>4} {'cards':>5} {'moved':>5}  geometry (top/scrollHeight/clientHeight)"]
        for t in r["trace"]:
            geom = "  ".join(f"{g['el']} {g['top']}/{g['h']}/{g['ch']}"
                             for g in t.get("geom", []))
            lines.append(f"  {t.get('pass'):>4} {t.get('cards'):>5} "
                         f"{str(t.get('moved')):>5}  {geom}")

    failed = [r for r in _search_log if not r.get("ok")]
    if failed:
        lines += ["", f"{len(failed)} search(es) returned nothing:"]
        for r in failed:
            lines.append(f"  - {r.get('label')}: {r.get('why') or 'unknown'}")
            lines.append(f"    {r.get('url')}")
    try:
        (OUT / "scrape_audit.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (OUT / "scrape_audit.json").write_text(
            json.dumps(_search_log, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# The Popen handle for the running build, when this process is the one that
# started it. Kept because os.kill(pid, 0) is not a liveness test for a child:
# an exited child stays a zombie until it is reaped, kill succeeds against it,
# and the build would read as running forever, blocking every later one.
_build_proc: subprocess.Popen | None = None

# The local ranker as its own action, so the Rank step on the dashboard can be
# triggered without committing to a full build. The rule, 19 September
# 2026: a button on every stage. run-batch.sh still ranks as its first step, so
# this is a preview — it writes out/ranked.json and the same ollama cache the
# build then reuses, so ranking here makes the subsequent build's ranking free.
_rank_proc: subprocess.Popen | None = None
_rank: dict = {"running": False, "started": None, "finished": None, "result": None}


def _rank_running() -> bool:
    global _rank_proc
    if not _rank["running"]:
        return False
    if _rank_proc is not None:
        if _rank_proc.poll() is None:
            return True
    else:
        # No handle: the bridge restarted while a rank was going. Trust the pid,
        # treating a zombie as finished (same reasoning as _build_running).
        pid = _rank.get("pid")
        if pid is not None:
            if pu.alive(pid) and any("rank_ollama.py" in t for t in pu.cmdline(pid)):
                return True
    code = _rank_proc.returncode if _rank_proc is not None else None
    _rank_proc = None
    _rank.update(running=False,
                 finished=dt.datetime.now().isoformat(timespec="seconds"))
    if code not in (None, 0):
        # A failed rank leaves the PREVIOUS ranking on disk. Showing that as
        # this run's result is how a dead Ollama used to look like success.
        tail = " ".join(_tail(OUT / "rank.log", 3))[-220:]
        _rank["result"] = None
        _event("rank", "failed", f"ranking stopped with an error (exit {code}): {tail}",
               fix=_error_fix(tail) or "Press Retry. Jobs already judged are cached, so it resumes where it stopped.")
        return False
    try:
        h = json.loads((OUT / "ollama_rank.json").read_text(encoding="utf-8")).get("health", {})
        _rank["result"] = {"buildable": h.get("buildable"),
                           "strong": h.get("strong"), "verdict": h.get("verdict")}
        _event("rank", "ok", f"ranked: {h.get('buildable', 0)} passed the filters, "
               f"{h.get('strong', 0)} strong")
    except (OSError, ValueError, AttributeError):
        pass
    return False


def start_rank() -> dict:
    """Run rank_ollama.py detached — a preview of what would be built."""
    if _rank_running():
        return {"ok": False, "why": "already ranking"}
    if _build_running():
        return {"ok": False, "why": "a build is running (it ranks as it goes)"}
    ranker = ROOT / "rank_ollama.py"
    if not (PYTHON.exists() and ranker.exists()):
        return {"ok": False, "why": "ranker or venv is missing"}
    up, _, entries = _ollama_tags()
    if not up and pu.start_ollama(OUT / "ollama.log",
                                  url=getattr(config, "OLLAMA_URL", "http://127.0.0.1:11434")):
        up, _, entries = _ollama_tags()
    if not up:
        _event("rank", "failed", "Ollama is not running, so nothing can be ranked",
               fix="Open the Ollama app (or run: ollama serve), then press Retry.")
        return {"ok": False, "why": "Ollama is not running. Open the Ollama app, then try again.",
                "fix": "ollama serve"}
    if _model_wanted(entries) is None:
        return {"ok": False, "why": f"the local model '{om.wanted(config)}' is still downloading "
                "(it starts by itself when Ollama runs); try again when Activity says it is ready"}
    top = getattr(config, "TOP_N", 20)
    log = open(OUT / "rank.log", "a", encoding="utf-8")
    log.write(f"\n=== rank started by the dashboard {dt.datetime.now():%H:%M:%S} ===\n")
    log.flush()
    global _rank_proc
    proc = subprocess.Popen([str(PYTHON), str(ranker), "--top", str(top)],
                            cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, **pu.detach_kwargs())
    _rank_proc = proc
    _rank.update(running=True, started=dt.datetime.now().isoformat(timespec="seconds"),
                 finished=None, pid=proc.pid, result=None)
    _event("rank", "started", "ranking the pool with the local model")
    return {"ok": True, "pid": proc.pid}


def stop_rank() -> dict:
    """
    Stop a standalone ranking now. Nothing is lost: the ranker saves every
    answer as it goes, so pressing Rank pool again continues where this one
    stopped, which is what makes Stop also a pause.

    A ranking that is part of a build is not stopped here: killing it would fail
    the build with "the local ranking failed". The build's own Stop covers it.
    """
    global _rank_proc
    if _build_running():
        return {"ok": False, "why": "this ranking is part of a build; use Stop build"}
    pids = pu.find_script_procs(ROOT / "rank_ollama.py")
    for pid in pids:
        pu.kill_tree(pid, grace=5)
    # Cleared before the next status poll reaps it, so an exit code from the
    # stop is not reported as the ranking failing.
    _rank_proc = None
    was = _rank.get("running")
    _rank.update(running=False, finished=dt.datetime.now().isoformat(timespec="seconds"))
    rp = _rank_progress()
    if was or pids:
        _event("rank", "stopped",
               f"ranking stopped by hand after {rp['done']} of {rp['total']} judged; "
               "their answers are saved",
               fix="Press Rank pool to continue from where it stopped.")
    return {"ok": True, "stopped": bool(pids),
            "still_running": bool(pu.find_script_procs(ROOT / "rank_ollama.py"))}


_adopt_running_rank()


def _build_pids() -> list[int]:
    """Running builds: the Python runner, or a run-batch.sh started before the
    runner moved to Python and still going."""
    return pu.find_script_procs(BUILD_SCRIPT, BUILDER / "run-batch.sh")


def _adopt_running_build() -> None:
    """
    Adopt a build (run_batch.py) still running when the bridge restarts, so the
    tracker keeps following it instead of showing it finished. _build_running()
    already falls back to the pid; this repopulates it on startup.
    """
    pid = next(iter(_build_pids()), None)
    if pid and not _build.get("running"):
        _build.update(running=True, pid=pid, finished=None, target=_build_target_of(pid),
                      reason=_build.get("reason") or "in progress (adopted on restart)")


def _build_target_of(pid: int | None) -> int | None:
    """The build ceiling the running run_batch.py was launched with — its numeric
    argument. Read from the live process so the tracker shows the number the user
    actually asked for (e.g. 25), not the config default. None if not passed."""
    if not pid:
        return None
    toks = pu.cmdline(pid)
    for i, t in enumerate(toks):
        if t.endswith(("run_batch.py", "run-batch.sh")):
            for nxt in toks[i + 1:]:
                if nxt.isdigit():
                    return int(nxt)
            break
    return None


_adopt_running_build()


def _build_running() -> bool:
    """Is the build still going? Reaps it if not."""
    global _build_proc
    if not _build["running"]:
        return False

    if _build_proc is not None:
        if _build_proc.poll() is None:
            return True
        code = _build_proc.returncode
        _build_proc = None
        _build.update(running=False,
                      finished=dt.datetime.now().isoformat(timespec="seconds"))
        _record_build_outcome(code)
        return False

    # No handle: the bridge restarted while a build was running. Fall back to
    # the pid, and treat a zombie as finished rather than as alive.
    pid = _build.get("pid")
    if pid is None:
        _build.update(running=False)
        return False
    # ...and check it is still a BUILD: a recycled pid must not read as one.
    if pu.alive(pid) and any(t.endswith(("run_batch.py", "run-batch.sh"))
                             for t in pu.cmdline(pid)):
        return True
    _build.update(running=False,
                  finished=dt.datetime.now().isoformat(timespec="seconds"))
    _record_build_outcome(None)
    return False


BUILD_OUTCOME = BUILDER / "logs" / "last-outcome.json"


_arbeitnow: dict = {"proc": None, "started": None}


def arbeitnow_running() -> bool:
    """Reap a finished Arbeitnow run into one audit line."""
    proc = _arbeitnow.get("proc")
    if proc is None:
        return False
    if proc.poll() is None:
        return True
    _arbeitnow["proc"] = None
    seg = (OUT / "arbeitnow.log").read_text(encoding="utf-8", errors="replace") \
        if (OUT / "arbeitnow.log").exists() else ""
    seg = seg.rsplit("=== arbeitnow started", 1)[-1]
    kept = re.findall(r"(\d+) kept", seg)
    new = re.findall(r"bridge: (\d+) new", seg)
    if proc.returncode == 0:
        _event("arbeitnow", "ok", f"Arbeitnow: {kept[-1] if kept else 0} jobs kept, "
               f"{new[-1] if new else 0} new")
    else:
        last = next((l.strip() for l in reversed(seg.splitlines()) if l.strip()), "")
        _event("arbeitnow", "failed", f"Arbeitnow failed: {last[:200]}",
               fix="Check the internet connection, then press Retry.")
    return False


def _build_outcome() -> dict:
    """What the last build ended with, as run_batch.py recorded it."""
    return _read_json(BUILD_OUTCOME, {})


def _record_build_outcome(code: int | None) -> None:
    """
    Turn a finished build into one audit line. run_batch.py writes
    logs/last-outcome.json on every exit path with a plain reason and a fix;
    when it could not (killed, crashed), the exit code and log tail stand in.
    """
    o = _build_outcome()
    fresh = o.get("pid") == _build.get("pid") or (
        o.get("at", "") >= (_build.get("started") or "9999"))
    if fresh and o.get("status"):
        _event("build", o["status"], o.get("message", ""), fix=o.get("fix", ""),
               built=o.get("built"))
        return
    if _build.get("reason") == "stopped by hand":
        return
    if code in (None, 0):
        _event("build", "ok", f"build finished: {build_progress().get('built', 0)} built today")
    else:
        tail = " ".join(_tail(BUILDER / "logs" / f"{_today()}.log", 3))[-220:]
        _event("build", "failed", f"build stopped with an error (exit {code}): {tail}",
               fix=_error_fix(tail) or "Press Retry. Finished applications are kept and skipped.")


# ------------------------------------------------------------------ live view
#
# Everything the dashboard needs, computed on request. The rule,
# 19 September 2026: one place to watch the whole run — scraping or building,
# which stage, how many searches done, how far the build has got — without
# tailing a log or refreshing a folder.
#
# The scrape half is already known here (the extension reports it). The build
# half is not: run-batch.sh runs in another process, so build stage is read the
# same way a human would — by looking at what is on disk in today's batch folder
# and at the tail of the build log.

def _tail(path: pathlib.Path, n: int = 16) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def _pdf_ok(f: pathlib.Path) -> bool:
    try:
        return f.stat().st_size > 8192      # build_docs inflates every real PDF past 8 KB
    except OSError:
        return False


def _rank_result() -> dict | None:
    """The local ranker's summary, surviving a bridge restart via its output
    file, and cleared by a fresh start (the file predates the reset)."""
    if _rank.get("result"):
        return _rank["result"]
    f = OUT / "ollama_rank.json"
    try:
        if _reset_at is not None and f.stat().st_mtime <= _reset_at:
            return None
        h = json.loads(f.read_text(encoding="utf-8")).get("health", {})
        if h:
            return {"buildable": h.get("buildable"), "strong": h.get("strong"),
                    "verdict": h.get("verdict")}
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _ranked_pass_count() -> int:
    f = OUT / "ollama_rank.json"
    try:
        if _reset_at is not None and f.stat().st_mtime <= _reset_at:
            return 0
        return len(json.loads(f.read_text(encoding="utf-8")).get("passed", []))
    except (OSError, ValueError, AttributeError):
        return 0


def _ranked_ready() -> int:
    """How many candidates are staged in ranked.json, ignoring a pre-reset file."""
    f = OUT / "ranked.json"
    try:
        if _reset_at is not None and f.stat().st_mtime <= _reset_at:
            return 0
        return len(json.loads(f.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return 0


def _rank_progress() -> dict:
    """
    How many jobs the local ranker has judged so far, out of the batch.

    rank_ollama.py prints "  62/172  ..." per job; the last such pair in the
    current run's section of the log is where it is now. The rule,
    19 September 2026: every stage should show how many are left.
    """
    try:
        txt = (OUT / "rank.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"done": 0, "total": 0}
    seg = txt.rsplit("=== rank started", 1)[-1]     # current run only
    pairs = re.findall(r"\b(\d+)/(\d+)\b", seg)
    if not pairs:
        return {"done": 0, "total": 0}
    d, t = pairs[-1]
    return {"done": int(d), "total": int(t)}


def _latest_batch_dir() -> pathlib.Path | None:
    """Today's batch folder if it has a report, else the most recent that does."""
    today = BATCHES / _today()
    if (today / "batch.html").exists():
        return today
    dirs = sorted(d for d in BATCHES.glob("*")
                  if d.is_dir() and d.name != "archive" and (d / "batch.html").exists())
    return dirs[-1] if dirs else None


_REPORT_CT = {".html": "text/html; charset=utf-8", ".pdf": "application/pdf",
              ".json": "application/json", ".md": "text/plain; charset=utf-8",
              ".png": "image/png", ".jpg": "image/jpeg", ".css": "text/css",
              ".js": "text/javascript"}


def _archive_todays_build() -> int:
    """
    Move EVERY built batch aside so a fresh start is actually fresh.

    Called by the hard reset ("Erase everything and start fresh"). Until
    24 September 2026 this moved only today's folder, so every earlier day's CVs
    and cover letters stayed in applications/ after an erase, and nobody could
    tell which had been archived. Now every dated folder moves to
    applications/archive/<date>-<time>/, in one sweep.

    Nothing is deleted. The PDFs stay recoverable, and they still count for
    duplicate detection, because applied_index reads applications/archive/ too,
    so a role already built never comes back into a batch.

    Returns how many day folders were archived.
    """
    _write_build_target(None)   # a fresh start has no pending target
    if not BATCHES.exists():
        return 0
    stamp = dt.datetime.now().strftime("%H%M%S")
    moved = 0
    for day in sorted(BATCHES.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]")):
        if not day.is_dir():
            continue
        if not any(day.rglob("*")):
            # A refused or empty run leaves an empty day folder; that is not a
            # batch, so remove it rather than count it as archived CVs.
            try:
                day.rmdir()
            except OSError:
                pass
            continue
        dest = BATCHES / "archive" / f"{day.name}-{stamp}"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            day.rename(dest)
            moved += 1
        except OSError:
            pass
    return moved


def build_progress() -> dict:
    """How far the build has got, read from disk rather than guessed.

    Two numbers matter and both used to be wrong. `built` is how many
    applications finished; `rejected` is how many the build read and dropped
    (wrong field, hard German, already applied); `target` is the number the user
    asked for. Once the round has written batch.json, that file is authoritative
    for built and rejected: counting PDF folders on disk instead once reported
    "12 built" when the run built 11, because a stray folder from an earlier
    attempt today also had both PDFs. While the build is still running batch.json
    is stale or absent, so the live folder scan is the only signal and is used
    then.
    """
    day = _today()
    folder = BATCHES / day
    running = _build.get("running", False)

    companies, built_live = [], 0
    if folder.exists():
        for d in sorted(x for x in folder.iterdir() if x.is_dir()):
            if d.name in ("archive",) or d.name.startswith("_"):
                continue
            # A fresh start wipes the run without deleting the CVs on disk, so a
            # folder older than the last reset belongs to a previous cycle and
            # is not counted as this run's progress.
            if _reset_at is not None:
                try:
                    if d.stat().st_mtime <= _reset_at:
                        continue
                except OSError:
                    pass
            cv = any(_pdf_ok(f) for f in d.glob("*_CV_*.pdf"))
            cl = any(_pdf_ok(f) for f in d.glob("*_CoverLetter_*.pdf"))
            if cv and cl:
                phase = "done"; built_live += 1
            elif any(d.glob("*.pdf")):
                phase = "rendering"           # a PDF exists but not both, or still a stub
            elif any(d.glob("*.json")):
                phase = "writing"             # shortlist/plan JSON written, documents not yet
            elif (d / "PLAN.md").exists():
                phase = "planning"
            else:
                phase = "started"
            companies.append({"name": d.name, "phase": phase})

    # batch.json is the authoritative built/rejected record once a round has
    # finished writing it. Prefer it when the build is not running; fall back to
    # the live folder count while it is.
    bj = _read_json(folder / "batch.json", {}) if folder.exists() else {}
    have_batch = bool(bj.get("built")) or bool(bj.get("dropped"))
    if have_batch and not running:
        built = len(bj.get("built", []))
        rejected = len(bj.get("dropped", []))
    else:
        built = built_live
        rejected = 0

    # The ceiling this run aimed for, in priority: the live in-memory value, the
    # durable file the launch wrote (survives a bridge restart), then a batch.json
    # target field if one was recorded. When none of those exist the run had no
    # fixed target (an autobuild builds everything that fits), so the honest
    # denominator is what it actually considered, built plus rejected. Config is
    # the last resort.
    target = (_build.get("target") or _read_build_target() or bj.get("target")
              or ((built + rejected) if (have_batch and not running) else 0)
              or getattr(config, "BUILD_TARGET", 15))

    return {"target": target, "built": built, "rejected": rejected,
            "companies": companies,
            "log": _tail(BUILDER / "logs" / f"{day}.log", 16)}


# Phases in the order a folder passes through them, for a readable label.
_PHASE_LABEL = {"planning": "planning", "writing": "writing the CV & letter",
                "rendering": "rendering PDFs", "done": "done", "started": "starting"}


def build_detail() -> dict:
    """
    A structured account of the build, for the dashboard's Build panel — the
    thing the log tail could never make legible: what is being written right now,
    what finished, what was rejected and why, and what was skipped because it was
    already built. Reads from disk (batch.json + the folder phases) so it is true
    whether the build is running, finished, or was stopped halfway.
    """
    bp = build_progress()
    day = _today()
    folder = BATCHES / day

    # Live folder phases (real-time), split into finished and in-flight.
    building_now, done_names = [], []
    for c in bp["companies"]:
        if c["phase"] == "done":
            done_names.append(c["name"])
        else:
            building_now.append({"company": c["name"],
                                  "phase": _PHASE_LABEL.get(c["phase"], c["phase"])})

    # batch.json is the authoritative record once a round has written it: the
    # reasoning for each built role and, crucially, why each rejected one was
    # dropped. It is merged across shards at the end but each shard's fragment
    # also lands here mid-run, so this fills in as the build proceeds.
    built_roles, dropped_roles = [], []
    data = _read_json(folder / "batch.json", {})
    for e in data.get("built", []):
        built_roles.append({"rank": e.get("rank"), "company": e.get("company", ""),
                            "title": e.get("title", ""), "why": e.get("why", ""),
                            "flags": e.get("flags", []) or []})
    for e in data.get("dropped", []):
        dropped_roles.append({"company": e.get("company", ""),
                             "title": e.get("title", ""), "reason": e.get("reason", "")})

    # RESUME.md lists the companies an earlier attempt today already built, which
    # this run carries through untouched — i.e. skipped because they exist. Shown
    # so a resumed build does not look like it silently ignored them.
    carried = []
    resume = folder / "RESUME.md"
    if resume.exists():
        # Don't repeat a company that already appears in the Built list — after a
        # merge every carried-over role is in batch.json, so listing it under both
        # "Built" and "already built earlier" just reads as a duplicate. Show it
        # here only while it is genuinely carried but not yet merged into batch.
        # RESUME.md lists folder names ("Axelera") while batch.json carries full
        # company names ("Axelera AI"), so compare on an alphanumeric-only key and
        # treat one containing the other as the same company.
        norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
        shown = [norm(r.get("company")) for r in built_roles if r.get("company")]
        try:
            for line in resume.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s.startswith("- "):
                    name = s[2:].strip()
                    k = norm(name)
                    if k and not any(k in b or b in k for b in shown if b):
                        carried.append(name)
        except OSError:
            pass

    return {"target": bp["target"], "built": bp["built"],
            "rejected": bp.get("rejected", 0),
            "building_now": building_now, "done_names": done_names,
            "built_roles": built_roles, "dropped_roles": dropped_roles,
            "carried_over": carried}


# Turn an error line into something the reader can act on.
#
# "Ollama is down" with no command attached is the most common way a new user
# gives up, so every pattern here owes the reader one concrete next step. The
# fallback is deliberately empty rather than a guess: a wrong instruction is
# worse than none.
_ERROR_FIXES = (
    ("usage limit",      "Claude's usage window is spent — wait for the reset, or run with a smaller target."),
    ("ollama",           "ollama serve"),
    ("is down",          "ollama serve"),
    ("no network",       "Check the connection, then press Rank pool again."),
    ("cards loaded",     "LinkedIn lazy-loads 25 cards. Leave the tab in the foreground and do not scroll while it runs."),
    ("only",             "LinkedIn lazy-loads 25 cards. Leave the tab in the foreground and do not scroll while it runs."),
    ("403",              "That site is bot-walled from scripted requests. Use the Chrome extension for it, or drop the search."),
    ("perimeterx",       "That site is bot-walled from scripted requests. Use the Chrome extension for it, or drop the search."),
    ("traceback",        "Full detail is in the build log below."),
)


def _error_fix(text: str) -> str:
    low = (text or "").lower()
    for needle, fix in _ERROR_FIXES:
        if needle in low:
            return fix
    return ""


def _source_health(jobs: list[dict]) -> list[dict]:
    """
    Per-source counts, computed from what actually landed.

    Deliberately driven by the inbox's own `source` field rather than a
    hardcoded list of sites, so adding a fetcher (Arbeitnow was added on
    23 September 2026) shows up here without touching the dashboard.
    """
    by: dict[str, dict] = {}
    for j in jobs:
        name = (j.get("source") or "Unknown").strip() or "Unknown"
        row = by.setdefault(name, {"source": name, "jobs": 0, "described": 0, "thin": 0})
        row["jobs"] += 1
        desc = j.get("description") or ""
        if len(desc) >= 400:
            row["described"] += 1
        elif desc:
            row["thin"] += 1
    return sorted(by.values(), key=lambda r: -r["jobs"])


# A scrape that has not reported for this long is treated as interrupted: Chrome
# was closed, the laptop slept, or the extension was reloaded mid-run. Longer
# than the extension's deliberate pause between searches (three to seven
# minutes), so a pause is never mistaken for a stop. Without this the dashboard
# said "scraping" forever and every build waited on a scrape that was gone.
SCRAPE_SILENT_MIN = 20


def _check_scrape_alive() -> None:
    if not _scrape.get("running") or not _scrape.get("at"):
        return
    try:
        age = (dt.datetime.now() - dt.datetime.fromisoformat(_scrape["at"])).total_seconds()
    except ValueError:
        return
    if age > SCRAPE_SILENT_MIN * 60:
        _scrape["running"] = False
        _event("scrape", "stopped",
               f"the extension stopped reporting {int(age // 60)} min ago, after "
               f"{_scrape.get('done', 0)} of {_scrape.get('total', 0)} searches",
               fix="Open Chrome and press Retry (or Run all searches now). It resumes where it stopped.")


def progress() -> dict:
    """One object describing the whole pipeline, right now."""
    _check_scrape_alive()
    arbeitnow_running()
    scr = dict(_scrape)
    building = _build_running()
    ranking = _rank_running()
    bp = build_progress()

    # One word for the banner. The order matters: a scrape in flight wins, then
    # a build (ranking before any documents exist, otherwise writing them),
    # then the resting states.
    joined = " ".join(bp["log"]).lower()
    # A build ranks first, then hands its candidates to Claude to write. The log
    # keeps the ranking lines in view for a while after Claude has started, so
    # "the word rank is in the log" is not enough — look for the hand-off.
    handed_to_claude = ("apply-batch" in joined or "going to claude" in joined
                        or "round " in joined)
    build_ranking = building and not handed_to_claude and not bp["companies"]
    if scr.get("running"):
        phase = "scraping"
    elif ranking or build_ranking:
        phase = "ranking"
    elif building:
        phase = "building"
    elif _build.get("finished") and bp["built"] >= bp["target"] and bp["target"] > 0:
        phase = "done"
    elif bp["built"] > 0:
        phase = "built-partial"
    else:
        phase = "idle"

    # "25 or close" for LinkedIn; Indeed has no fixed page size, so it is judged
    # only on whether it returned anything and did not error.
    LI_FULL, LI_CLOSE, IND_THIN = 22, 15, 4
    searches = []
    li = {"total": 0, "full": 0, "short": 0, "failed": 0}
    ind = {"total": 0, "collected": 0, "thin": 0, "failed": 0}
    errors = []
    for e in _search_log:
        host = (e.get("host") or "").lower()
        loaded = int(e.get("loaded") or 0)
        is_li = "linkedin" in host
        ok = bool(e.get("ok"))
        label = e.get("label") or e.get("query") or "?"
        if is_li:
            li["total"] += 1
            if not ok:
                st = "failed"; li["failed"] += 1
                errors.append(f"scrape · {label}: {e.get('why') or 'failed'}")
            elif loaded >= LI_FULL:
                st = "ok"; li["full"] += 1
            elif loaded >= LI_CLOSE:
                st = "close"; li["full"] += 1
            else:
                st = "short"; li["short"] += 1
                errors.append(f"scrape · {label}: only {loaded}/25 cards loaded"
                              + (" (read as a manual scroll)" if e.get("userScrolled") else ""))
        else:
            ind["total"] += 1; ind["collected"] += loaded
            if not ok:
                st = "failed"; ind["failed"] += 1
                errors.append(f"scrape · {label}: {e.get('why') or 'failed'}")
            elif loaded <= IND_THIN:
                st = "thin"; ind["thin"] += 1
            else:
                st = "ok"
        searches.append({
            "label": label, "host": host, "found": int(e.get("found") or 0),
            "added": int(e.get("added") or 0), "loaded": loaded,
            "status": st, "why": e.get("why") or "",
            "userScrolled": bool(e.get("userScrolled")),
        })

    # Errors from the rest of the pipeline, not just the scrape.
    if isinstance(_last_rank, dict) and _last_rank.get("error"):
        errors.append(f"ranking · {_last_rank['error']}")
    BENIGN = ("could not check, keeping it", "0 gone or closed", "still open")
    for line in bp["log"]:
        low = line.lower()
        if any(b in low for b in BENIGN):
            continue                      # liveness notes, not problems
        if any(w in low for w in ("error", "failed", "could not", "usage limit",
                                  "no network", "is down", "traceback")):
            errors.append(f"build · {line.strip()[:140]}")

    healthy = (li["short"] == 0 and li["failed"] == 0 and ind["failed"] == 0
               and not (isinstance(_last_rank, dict) and _last_rank.get("error")))
    inbox = _read_json(_active_inbox(), {"jobs": []}).get("jobs", [])
    health = {"ok": healthy, "linkedin": li, "indeed": ind,
              "sources": _source_health(inbox),
              "errors": [{"text": e, "fix": _error_fix(e)} for e in errors[-12:]]}

    applied = range(_applied_count())
    total = int(scr.get("total") or 0) or len(_searches) or len(searches)
    # What is buildable regardless of which day it was scraped. collected_today
    # is zero the moment the clock rolls past midnight even though yesterday's
    # pool is still perfectly good, so the buttons must not gate on it.
    try:
        ps = pool_status.status()
        pool_usable = int(ps.get("usable") or 0)
        pool_repeats = int(ps.get("repeats") or 0)
        pool_explain = ps.get("explain") or ""
        pool_stale = bool(ps.get("stale"))
    except Exception:
        pool_usable, pool_repeats, pool_stale = len(inbox), 0, False
        pool_explain = ""

    # One progress reading for whatever stage is running, so every process shows
    # how many of the batch are done and how many are left.
    active = None
    if phase == "scraping":
        active = {"label": "searches scraped", "done": int(scr.get("done") or 0),
                  "total": total}
    elif phase == "ranking":
        if ranking:                       # standalone rank → its own log
            rp = _rank_progress()
        else:                             # ranking inside a build → build log
            bm = re.findall(r"\b(\d+)/(\d+)\b", joined)
            rp = ({"done": int(bm[-1][0]), "total": int(bm[-1][1])} if bm
                  else {"done": 0, "total": 0})
        active = {"label": "jobs judged", "done": rp["done"], "total": rp["total"]}
    elif phase == "building":
        in_progress = sum(1 for c in bp["companies"] if c["phase"] != "done")
        active = {"label": "documents built", "done": bp["built"],
                  "total": bp["target"], "in_progress": in_progress}

    return {
        "steps": _step_state(),
        "build_outcome": _build_outcome(),
        "arbeitnow_running": _arbeitnow.get("proc") is not None,
        "health": health,
        "active": active,
        "ok": True,
        "now": dt.datetime.now().isoformat(timespec="seconds"),
        "phase": phase,
        "autobuild": _autobuild,
        "collected_today": len(inbox),
        "pool_usable": pool_usable,
        "pool_repeats": pool_repeats,
        # "81 to judge, of 137 collected: 41 already judged on an earlier day,
        # ..." Every collected job accounted for, in words (pool.py).
        "pool_explain": pool_explain,
        "pool_stale": pool_stale,
        "with_descriptions": sum(1 for j in inbox if j.get("description")),
        "applied": len(applied),
        "scrape": {"running": bool(scr.get("running")),
                   "done": int(scr.get("done") or 0),
                   "total": total, "at": scr.get("at")},
        "searches_done": len(searches),
        "searches_total": total,
        "searches": searches,
        "rank": _last_rank,
        "rank_run": {"running": ranking, "result": _rank_result(),
                     "started": _rank.get("started")},
        "ranked_ready": _ranked_ready(),
        # How many the local model judged strong enough to be worth applying to.
        # This is the SUGGESTED build count — no fixed 15. The rule,
        # 20 September 2026: the model proposes, the user edits, then builds.
        "suggested_build": (
            (_rank_result() or {}).get("strong")
            or (_rank_result() or {}).get("buildable")
            or getattr(config, "BUILD_TARGET", 15)),
        "passed_count": _ranked_pass_count(),
        "build": {"running": building, "started": _build.get("started"),
                  "finished": _build.get("finished"), "reason": _build.get("reason")},
        "build_progress": bp,
        "build_detail": build_detail(),
    }


def start_build(reason: str, top: int | None = None) -> dict:
    """
    Launch run_batch.py detached, at most one at a time.

    Detached on purpose: this runs inside an HTTP handler, and a build takes
    the better part of an hour between the local ranking and Claude writing.
    Nothing here waits for it. Progress is in the log, and /status reports it.
    """
    with _build_lock:
        if _build_running():
            return {"ok": False, "why": "a build is already running",
                    "started": _build["started"]}
        if not BUILD_SCRIPT.exists():
            return {"ok": False, "why": f"{BUILD_SCRIPT} is missing"}

        log_dir = BUILDER / "logs"
        log_dir.mkdir(exist_ok=True)
        log_path = log_dir / f"{_today()}.log"
        cmd = [str(PYTHON), str(BUILD_SCRIPT)]
        if top:
            cmd.append(str(top))
        env = dict(os.environ, BRIDGE_PORT=str(PORT))
        if reason == "asked for by hand":
            # A press of Build (or Retry) is a person deciding to try again, so
            # the day's automatic-retry budget must not refuse it. Without this,
            # three failed attempts earlier meant every later click silently
            # did nothing until midnight.
            env["BUILD_MANUAL"] = "1"
        handle = open(log_path, "a", encoding="utf-8")
        handle.write(f"\n=== build started by the bridge ({reason}) "
                     f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
        handle.flush()
        # Detached (a new session, or on Windows a new process group with no
        # window), so stopping the bridge does not take a running build down.
        global _build_proc
        proc = subprocess.Popen(cmd, cwd=str(BUILDER), stdout=handle,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=env, **pu.detach_kwargs())
        _build_proc = proc
        _build.update(running=True, started=dt.datetime.now().isoformat(timespec="seconds"),
                      finished=None, reason=reason, pid=proc.pid, log=str(log_path),
                      target=top)
        # Persist the requested target so the tracker still shows it after the
        # build finishes or the bridge restarts. Cleared (None) for an autobuild,
        # which has no fixed target.
        _write_build_target(top)
        _event("build", "started", f"build started ({reason})"
               + (f", target {top}" if top else ""))
        return {"ok": True, "pid": proc.pid, "log": str(log_path), "reason": reason}


def stop_build() -> dict:
    """
    Stop a running build now, cleanly, so the dashboard's Stop button actually
    halts it and a fresh build can start straight after.

    The runner's whole process tree goes: killing the runner alone would leave
    its claude children writing documents. TERM first for a chance to clean up
    (on POSIX the runner then records "stopped" itself), then KILL. The run
    lock and the resume state are cleared too, otherwise the next build would
    refuse with "already running" or resume the half-finished batch.
    """
    # Found by command line, never by the remembered pid alone: pids are reused
    # (quickly, on Windows), and a stale one would stop an unrelated program.
    pids = _build_pids()

    killed = False
    for pid in set(pids):
        # The runner and every Claude session and watchdog under it.
        killed = pu.kill_tree(pid) or killed
    # Any Claude session that outlived its runner (an adopted orphan) dies here.
    for pid in pu.find_apply_batch_claudes(BUILDER):
        killed = pu.kill_tree(pid) or killed

    # Clear the lock and resume state so the next build starts clean.
    for path in (BUILDER / "logs" / ".run.lock",):
        try:
            if path.is_dir():
                import shutil
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass
    try:
        state = BATCHES / _today() / ".batch-state"
        if state.exists():
            state.unlink()
    except OSError:
        pass

    global _build_proc
    _build_proc = None
    _build.update(running=False,
                  finished=dt.datetime.now().isoformat(timespec="seconds"),
                  reason="stopped by hand")
    _event("build", "stopped", "build stopped by hand; finished applications are kept",
           fix="Press Build to continue. It skips what is already built.")
    return {"ok": True, "stopped": killed,
            "still_running": _any_build_proc()}


def _any_build_proc() -> bool:
    """True if a build runner or an apply-batch Claude is still alive anywhere."""
    return bool(_build_pids() or pu.find_apply_batch_claudes(BUILDER))


def ranking_data() -> dict:
    """
    The last local ranking as data, for web/ranking.html to render.

    This used to be a 55-line Python f-string that emitted the whole page,
    CSS included — which meant 32 escaped braces (`{{`/`}}`) and a stylesheet
    nobody could safely edit, because every CSS brace was a format placeholder.
    The page is now static and this only carries the numbers.
    """
    data = _read_json(OUT / "ollama_rank.json", {}) if _rank_is_current() else {}
    if not data or not (data.get("passed") or data.get("dropped")):
        return {"ok": False, "rows": [], "counts": {}, "model": ""}

    top_urls = {t.get("url") for t in data.get("top", [])}

    def row(r: dict, kind: str) -> dict:
        return {
            "kind": kind,
            "score": r.get("rank_score") or 0,
            "company": r.get("company") or "",
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "location": r.get("location") or "",
            "source": r.get("source") or "",
            "berlin": bool(r.get("berlin")),
            "german": (str(r.get("german_level")).upper()
                       if r.get("german_level") not in (None, "none", "") else ""),
            "years": r.get("years_required") or None,
            "verdict": r.get("verdict") or "",
            "why": r.get("why") or r.get("reason") or "",
        }

    rows = [row(r, "sent" if r.get("url") in top_urls else "passed")
            for r in sorted(data.get("passed", []), key=lambda x: -(x.get("rank_score") or 0))]
    rows += [row(r, "dropped")
             for r in sorted(data.get("dropped", []), key=lambda x: -(x.get("rank_score") or 0))]
    rows += [row(r, "dropped") for r in data.get("repeats", [])]

    return {"ok": True, "rows": rows, "counts": data.get("counts", {}),
            "health": data.get("health", {}), "model": data.get("model", ""),
            "generated": data.get("generated")}


# ==================================================================== doctor
#
# Every check answers three questions: is it ok, what did we actually see, and
# what do I type to fix it. The third is the one that matters — "Ollama is down"
# with no command attached is the single most common way a new user gives up.
#
# Nothing here mutates anything, so the dashboard can poll it freely.

def _ollama_tags() -> tuple[bool, str, list[dict]]:
    base = getattr(config, "OLLAMA_URL", "http://127.0.0.1:11434")
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=3) as r:
            return True, "", list(json.loads(r.read()).get("models") or [])
    except Exception as e:                                   # noqa: BLE001
        return False, str(e), []


# The local model, downloaded by the bridge itself. Setup used to be the only
# thing that pulled it, so anyone who started the bridge without running setup
# first (or with Ollama closed at the time) had a ranker that could never run
# and a Setup panel telling them to type a command. Now: whenever Ollama is up
# and the model is missing, download it in the background, once at a time.
# Only when Ollama has no usable model at all: one that is already installed is
# used instead of downloading OLLAMA_MODEL (scraper/ollama_model.py).
_model_pull: dict = {"running": False, "failures": 0}


def _model_wanted(entries: list[dict]) -> str | None:
    """The model the ranker will use; None when it still has to be downloaded."""
    return om.resolve(config, entries, fetch=False)


def _model_watch() -> None:
    import time as _t
    base = getattr(config, "OLLAMA_URL", "http://127.0.0.1:11434")
    while True:
        try:
            # Installed but not running (after a reboot, or just installed)?
            # Start it, rather than show "open the Ollama app" and wait.
            if base.startswith("http://127.0.0.1") and pu.find_ollama() and not pu.ollama_up(base):
                if pu.start_ollama(OUT / "ollama.log", url=base):
                    _event("model", "info", "started Ollama, which was not running")
            up, _why, entries = _ollama_tags()
            want = om.wanted(config)
            exe = pu.find_ollama()
            if (up and exe and _model_wanted(entries) is None
                    and _model_pull["failures"] < 3 and os.environ.get("AUTO_PULL") != "0"):
                _model_pull["running"] = True
                _event("model", "started", f"downloading the local model '{want}' "
                       "(a few GB, once); ranking can start when it is done")
                with open(OUT / "model-pull.log", "a", encoding="utf-8") as log:
                    code = subprocess.call([exe, "pull", want], stdout=log,
                                           stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                           **pu.quiet_kwargs())
                _model_pull["running"] = False
                if code == 0:
                    _event("model", "ok", f"the local model '{want}' is ready")
                else:
                    _model_pull["failures"] += 1
                    _event("model", "failed", f"downloading '{want}' failed (exit {code})",
                           fix=f"Check the internet connection. It retries by itself; "
                               f"or run: ollama pull {want}")
        except Exception as e:                               # noqa: BLE001
            _model_pull["running"] = False
            print(f"  model watch: {e}")
        _t.sleep(120)


_claude_auth_cache: dict = {"at": 0.0, "value": None}


def _claude_signed_in(binary) -> bool | None:
    """
    Whether Claude Code has a login. None when it cannot be determined.

    Checked because a headless build cannot prompt for a login: a signed-out CLI
    just fails the build, and the binary merely existing said nothing about it.
    Cached for five minutes; the answer rarely changes and the CLI is slow to
    start. Only the yes/no is used, never the account details, because this
    panel ends up in screenshots.
    """
    import time as _t
    if _t.time() - _claude_auth_cache["at"] < 300 and _claude_auth_cache["value"] is not None:
        return _claude_auth_cache["value"]
    try:
        out = subprocess.run([str(binary), "auth", "status"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=12,
                             stdin=subprocess.DEVNULL, **pu.quiet_kwargs()).stdout
        value = bool(json.loads(out).get("loggedIn"))
    except Exception:                                      # noqa: BLE001
        value = None
    _claude_auth_cache.update(at=_t.time(), value=value)
    return value


def doctor() -> dict:
    """Preflight. Everything a fresh clone needs, checked one at a time."""
    checks: list[dict] = []

    def add(cid, label, ok, detail, fix="", weight="required"):
        checks.append({"id": cid, "label": label, "ok": bool(ok),
                       "detail": detail, "fix": fix, "weight": weight})

    # --- the local model -----------------------------------------------
    up, why, entries = _ollama_tags()
    models = [m.get("name", "") for m in entries]
    chosen = _model_wanted(entries) if up else None
    want = chosen or om.wanted(config)
    add("ollama", "Ollama is running", up,
        f"{len(models)} model(s) available" if up else f"not reachable — {why}",
        "ollama serve")
    have = chosen is not None
    add("model", f"Model '{want}' is pulled", up and have,
        ("downloading now, by itself (a few GB, once)" if _model_pull["running"]
         else (", ".join(models[:4]) if have else "not yet: it downloads by itself shortly"))
        if up else "cannot tell while Ollama is down; open the Ollama app",
        f"ollama pull {want}")

    # --- the extension --------------------------------------------------
    # Judged by what the EXTENSION brought in, not by whether the pool has
    # anything: Arbeitnow fills the pool with no extension at all, and the old
    # check then reported "Chrome extension has pushed jobs" for an extension
    # that had never run. Optional, because Arbeitnow-only is a valid setup.
    browser_sources = {"linkedin", "indeed", "stepstone"}
    def _from_browser(jobs):
        return sum(1 for j in jobs if (j.get("source") or "").strip().lower() in browser_sources)
    inbox = _read_json(_active_inbox(), {"jobs": []}).get("jobs", [])
    today = _from_browser(inbox)
    earlier = any(_from_browser(_read_json(f, {"jobs": []}).get("jobs", []))
                  for f in (INBOX / "archive").glob("*.json")) if (INBOX / "archive").exists() else False
    add("extension", "Chrome extension has collected jobs", bool(today) or earlier,
        f"{today} job(s) from LinkedIn, Indeed or StepStone today" if today
        else ("none today, but an earlier scrape exists" if earlier
              else "no job has come from the extension yet — Arbeitnow works without it"),
        "Load extension/ unpacked at chrome://extensions, then press Run scrape",
        weight="optional")

    # --- the document builder -------------------------------------------
    add("builder", "Builder directory found", BUILDER.exists(),
        str(BUILDER) if BUILDER.exists() else f"missing: {BUILDER}",
        "Set BUILDER_DIR, or keep builder/ next to scraper/")
    claude = pu.find_claude()
    add("claude", "Claude Code is installed", bool(claude),
        "found" if claude else "not found on PATH or where its installers put it",
        pu.install_hint("claude"))
    if pu.IS_WIN:
        # Claude Code runs its commands through Git Bash on Windows, so a build
        # without it fails at the first command.
        bash = pu.find_git_bash()
        add("git-bash", "Git for Windows (Git Bash) is installed", bool(bash),
            "found" if bash else "not found — Claude Code needs it on Windows",
            pu.install_hint("git-bash"))
    if claude:
        signed = _claude_signed_in(claude)
        add("claude-login", "Claude Code is signed in", signed is not False,
            "signed in" if signed else ("could not check" if signed is None else "not signed in"),
            "claude auth login")

    # --- the profile ------------------------------------------------------
    prof = PROFILE if PROFILE.exists() else None
    add("profile", "Ranking profile present", bool(prof),
        f"{PROFILE_DIR.name}/{PROFILE.name}, {PROFILE.stat().st_size} bytes" if prof
        else f"missing: {PROFILE}",
        "cp -r profiles/example profiles/me   # then edit profiles/me/profile.md")
    # Required, and judged by CONTENT, not by folder name. setup.sh creates
    # profiles/me as a copy of the fictional example, so "the folder is not
    # called example" passed for someone who had filled in nothing, and every
    # job was ranked, and every CV written, as Alex Rivera.
    example = REPO / "profiles" / "example"
    def _same(name):
        a, b = PROFILE_DIR / name, example / name
        try:
            return a.exists() and b.exists() and a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")
        except OSError:
            return False
    idn = PROFILE_DIR / "identity.md"
    idn_text = idn.read_text(encoding="utf-8") if idn.exists() else ""
    still = [n for n in ("profile.md", "identity.md") if _same(n)]
    if "example.com" in idn_text and "identity.md" not in still:
        still.append("identity.md")
    own = PROFILE_DIR.name != "example" and not still
    add("own-profile", "Your profile is filled in", own,
        f"profiles/{PROFILE_DIR.name}" if own
        else ("still the fictional example: " + ", ".join(still)) if still
        else "using the fictional example profile",
        "claude \"/make-profile path/to/your-cv.pdf\"   # run in the project folder")

    # --- writability -------------------------------------------------------
    try:
        OUT.mkdir(exist_ok=True); INBOX.mkdir(exist_ok=True)
        probe = OUT / ".write-probe"; probe.write_text("ok"); probe.unlink()
        add("write", "Working directories writable", True, f"{OUT} and {INBOX}")
    except OSError as e:
        add("write", "Working directories writable", False, str(e),
            f"chmod u+w {OUT} {INBOX}")

    required = [c for c in checks if c["weight"] == "required"]
    return {"ok": all(c["ok"] for c in required),
            "passed": sum(1 for c in required if c["ok"]),
            "total": len(required),
            "checks": checks}


# ==================================================================== funnel
def funnel() -> dict:
    """
    The scrape reduced to the only five numbers that explain a small batch.

    This is the most useful thing the pipeline knows about itself and it was
    invisible until now: a user whose batch came back tiny could not see whether
    the scrape was thin, the gates were harsh, or the target was simply low.
    """
    if not _rank_is_current() and (OUT / "ollama_rank.json").exists():
        return {"ok": False, "erased": True, "stages": [], "dropped": [],
                "erased_at": dt.datetime.fromtimestamp(_reset_at).isoformat(timespec="minutes"),
                "why": "Erased. The funnel fills again after the next scrape and rank."}
    rank = _read_json(OUT / "ollama_rank.json", {})
    counts = rank.get("counts", {}) or {}
    health = rank.get("health", {}) or {}
    dropped = rank.get("dropped", []) or []

    reasons: dict[str, int] = {}
    # Not read by the model at all, in the words pool.py uses everywhere.
    not_read = rank.get("not_read") or {}
    labels = {"repeat": "already judged on an earlier day", "applied": "already applied or built",
              "no_description": "no description", "too_old": "collected too long ago"}
    for key, label in labels.items():
        if not_read.get(key):
            reasons[label] = not_read[key]
    if rank.get("repeats") and not not_read:          # a ranking from before not_read
        reasons["already judged on an earlier day"] = len(rank["repeats"])
    for row in dropped:
        reasons[row.get("verdict", "other")] = reasons.get(row.get("verdict", "other"), 0) + 1

    bp = build_progress()
    # A strict chain: every stage is a subset of the one above it, so the
    # percentage beside each number is honest. `strong` is deliberately NOT a
    # link here — it is a quality label on the roles that passed, and putting it
    # between "passed" and "sent" produced "250%" because more roles are sent
    # than are strong.
    stages = [
        {"key": "scraped", "label": "Scraped",        "n": counts.get("scraped", 0)},
        {"key": "judged",  "label": "Read by model",  "n": counts.get("judged", 0)},
        {"key": "passed",  "label": "Passed gates",   "n": counts.get("passed", 0),
         "note": f"{health.get('strong', 0)} strong"},
        {"key": "sent",    "label": "Sent to Claude", "n": counts.get("sent_to_claude", 0)},
        {"key": "built",   "label": "Documents",      "n": bp.get("built", 0)},
    ]
    return {"ok": bool(counts), "generated": rank.get("generated"),
            "stages": stages,
            "dropped": sorted(({"reason": k, "n": v} for k, v in reasons.items()),
                              key=lambda r: -r["n"]),
            "verdict": health.get("verdict"), "why": health.get("why")}



# ============================================================ live batch report
_report_mod = None


def _render_report(batch_dir: pathlib.Path) -> str | None:
    """
    Re-render a batch's review page from its batch.json, with the CURRENT design.

    batch.html used to be written once, on the day the batch was built, and then
    served as a frozen file. So every older batch kept the look of the day it
    was made, and a redesign never reached the Applications page. Rendering on
    request fixes that; the fresh copy is also written back to disk so a
    batch.html opened straight from Finder is current too. Any failure falls
    back to the file on disk.
    """
    global _report_mod
    src = batch_dir / "batch.json"
    if not src.exists():
        return None
    try:
        if _report_mod is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "batch_report", BUILDER / "batch_report.py")
            _report_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(_report_mod)
        _report_mod.CSS = _report_mod._load_css()        # pick up CSS edits live
        batch = json.loads(src.read_text(encoding="utf-8"))
        batch.setdefault("date", batch_dir.name)
        html = _report_mod.render(batch)
        try:
            (batch_dir / "batch.html").write_text(html, encoding="utf-8")
        except OSError:
            pass
        return html
    except Exception as e:                                  # noqa: BLE001
        print(f"  report: live render failed, serving the saved file ({e})")
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "JobCollectorBridge/1.0"

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # The extension calls this from its service worker, which host
        # permissions already cover. These headers just make curl and any
        # future page-context call work too.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data: bytes, ct: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        # The report is rewritten every build; a cached copy is how a browser
        # keeps showing an old batch.html whose PDF links point at files that
        # have since been renamed or moved, which reads as "file not on site".
        # Never let it cache.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def _serve_report(self) -> None:
        # /report/<date>/... is that day's batch (the report links to every
        # other day); /report/... alone is the newest.
        rel = self.path.split("?")[0][len("/report"):].lstrip("/")
        m = re.match(r"(\d{4}-\d{2}-\d{2})(?:/|$)", rel)
        if m and (BATCHES / m.group(1) / "batch.json").exists():
            base, prefix, rel = BATCHES / m.group(1), f"/report/{m.group(1)}/", rel[len(m.group(0)):]
        else:
            base, prefix = _latest_batch_dir(), "/report/"
        if not base:
            return self._send_html(
                # Styled with the shared tokens so it follows dark mode. This is
                # the first thing a new user sees behind "Applications".
                '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>Applications · Job Pipeline</title>'
                '<link rel="stylesheet" href="/web/tokens.css">'
                '<link rel="stylesheet" href="/web/topbar.css">'
                '<link rel="stylesheet" href="/web/app.css">'
                '<script>try{var t=localStorage.getItem("theme");'
                'if(t&&t!=="auto")document.documentElement.setAttribute("data-theme",t)}catch(e){}</script>'
                '</head><body><div class="wrap">'
                # The same top bar as every other page (web/topbar.css).
                '<header class="topbar"><a class="brand" href="/dashboard">'
                '<span class="brand-mark" aria-hidden="true"></span>'
                '<span class="brand-name">Job Pipeline</span></a><span class="grow"></span>'
                '<nav class="nav" aria-label="Pages">'
                '<a class="go ghost" href="/dashboard"><span class="ico i-home" aria-hidden="true"></span>Dashboard</a>'
                '<a class="go ghost" href="/report/" aria-current="page"><span class="ico i-doc" aria-hidden="true"></span>Applications</a>'
                '<a class="go ghost" href="/ranking"><span class="ico i-list" aria-hidden="true"></span>Fit ranking</a>'
                '</nav></header>'
                '<div class="card"><h2>No applications yet</h2>'
                '<p class="muted">Nothing has been built. Rank the pool, then press '
                '<b>Build</b> on the dashboard; each finished batch appears here.</p>'
                '<p><a class="go" href="/dashboard">Go to the dashboard</a></p></div>'
                '</div></body></html>')
        rel = rel or "batch.html"
        target = (base / rel).resolve()
        try:
            base_r = base.resolve()
            if base_r != target and base_r not in target.parents:
                return self._send({"error": "forbidden"}, 403)
        except OSError:
            return self._send({"error": "bad path"}, 400)
        if not target.is_file():
            return self._send({"error": "not found"}, 404)
        ct = _REPORT_CT.get(target.suffix.lower(), "application/octet-stream")
        data = target.read_bytes()
        if rel == "batch.html":
            fresh = _render_report(base)
            if fresh is not None:
                data = fresh.encode("utf-8")
        # The report links to its PDFs with paths relative to its own folder
        # ("Acme/Alex_Rivera_CV_Acme.pdf"). Served at "/report" the
        # browser resolved those against "/" and every download 404'd. The route
        # normaliser strips a trailing slash, so a redirect to "/report/" loops;
        # inject a <base> instead so relative links resolve to "/report/..."
        # regardless of how the index URL was spelled. Only the HTML index needs
        # it; the PDFs are served straight through.
        if target.suffix.lower() in (".html", ".htm"):
            html = data.decode("utf-8", "replace")
            tag = f'<base href="{prefix}">'
            if tag not in html:
                lower = html.lower()
                i = lower.find("<head")
                if i != -1:
                    i = html.find(">", i) + 1
                    html = html[:i] + tag + html[i:]
                else:
                    html = tag + html
            data = html.encode("utf-8")
        self._send_bytes(data, ct)

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _guarded(self, handler):
        """
        Run a handler; turn any exception into a JSON error and an audit line.

        Without this an unexpected exception closed the connection with no
        response, which the extension and the dashboard both read as "the
        bridge is down" — the wrong fix for a bug in one endpoint.
        """
        try:
            handler()
        except (BrokenPipeError, ConnectionResetError):
            pass                                 # the client went away; nothing to tell it
        except Exception as e:                   # noqa: BLE001
            import traceback
            traceback.print_exc()
            _event("bridge", "failed", f"{self.command} {self.path.split('?')[0]} failed: "
                   f"{type(e).__name__}: {e}"[:300],
                   fix="The bridge is still running. Try again; if it repeats, the "
                       "traceback is in the window running the bridge, or in "
                       "scraper/out/bridge.log.")
            try:
                self._send({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
            except OSError:
                pass

    def do_GET(self):
        self._guarded(self._do_get)

    def do_POST(self):
        self._guarded(self._do_post)

    def _do_get(self):
        # Route on the path alone. No handler reads a query string, and
        # matching the raw path made "/dashboard?x" a 404.
        route = self.path.split("?", 1)[0].rstrip("/")
        if route == "/searches":
            return self._send({"ok": True, "searches": _searches})

        if route == "/searches/saved":
            saved = _read_json(OUT / "searches.json", [])
            return self._send({"ok": True, "searches": saved if isinstance(saved, list) else []})

        if route == "/searches-audit":
            return self._send({"ok": True, "searches": _search_log})

        if route == "/build":
            return self._send({"ok": True, "build": {**_build,
                                                     "running": _build_running()}})

        if route == "/progress":
            return self._send(progress())

        if route == "/doctor":
            return self._send(doctor())

        if route == "/events":
            return self._send({"ok": True, "events": recent_events(80),
                               "steps": _step_state()})

        if route == "/funnel":
            return self._send(funnel())

        if route.startswith("/web/"):
            return self._serve_static(route[len("/web/"):])

        if route in ("/dashboard", "/live"):
            return self._serve_static("dashboard.html")

        if route == "/ranking":
            return self._serve_static("ranking.html")

        if route == "/ranking.json":
            return self._send(ranking_data())

        if route == "/report" or route.startswith("/report/"):
            return self._serve_report()

        if route not in ("/status", "", "/built", "/trigger"):
            return self._send({"error": "not found"}, 404)

        # The extension asks here every minute. A pending trigger is handed over
        # once and then cleared, so a run starts exactly once no matter how many
        # tabs are polling.
        if route == "/trigger":
            global _trigger, _trigger_only
            pending, _trigger = _trigger, None
            only, _trigger_only = _trigger_only, None
            return self._send({"ok": True, "run": bool(pending), "at": pending,
                               "only": only})

        built = built_jobs()
        if route == "/built":
            return self._send({"ok": True, "built": built})

        inbox = _read_json(_active_inbox(), {"jobs": []}).get("jobs", [])
        applied = range(_applied_count())
        latest = max((b["date"] for b in built.values()), default=None)
        self._send({
            "ok": True,
            "today": _today(),
            "collected_today": len(inbox),
            "with_descriptions": sum(1 for j in inbox if j.get("description")),
            "applied": len(applied),
            "built": len(built),
            "latest_batch": latest,
            "latest_report": next((b["report"] for b in built.values()
                                   if b["date"] == latest), None),
            "last_rank": _last_rank,
            "scrape": _scrape,
            "build": {**_build, "running": _build_running()},
            "autobuild": _autobuild,
            "searches_done": len(_search_log),
        })

    # ------------------------------------------------------------ static files
    _TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
              ".js": "text/javascript; charset=utf-8", ".json": "application/json",
              ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}

    def _serve_static(self, name: str):
        """
        Serve one file out of web/.

        The path is resolved and then checked to be inside web/, so a request
        for /web/../../applied.json cannot walk out of the directory. This is a
        loopback server on 127.0.0.1, but it is also about to be open source and
        someone will eventually bind it to 0.0.0.0.
        """
        # Drop any query string or fragment first. "app.css?v=3" is ordinary
        # cache-busting and must serve app.css, not a 404 that silently unstyles
        # the page.
        name = name.split("?", 1)[0].split("#", 1)[0]
        try:
            path = (WEB / name).resolve()
            path.relative_to(WEB.resolve())
            data = path.read_bytes()
        except (ValueError, OSError):
            return self._send({"error": "not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", self._TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        # The dashboard is edited live while it is running; never cache it.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _do_post(self):
        # Route on the path alone. No handler reads a query string, and
        # matching the raw path made "/dashboard?x" a 404.
        route = self.path.split("?", 1)[0].rstrip("/")
        body = self._body()

        if route == "/ingest":
            jobs = body.get("jobs") or []
            if not isinstance(jobs, list):
                return self._send({"error": "jobs must be a list"}, 400)
            added, total = merge_jobs(jobs)
            try:
                ledger.record_seen([j for j in jobs if isinstance(j, dict)])
            except OSError as e:
                _event("data", "failed", f"could not update history/seen.csv: {e}")
            if added:
                src = next((j.get("source") for j in jobs if j.get("source")), "extension")
                _event("collect", "info", f"{added} new job(s) from {src}, {total} in today's pool")
            result = rerank() if (added or body.get("force")) else dict(_last_rank)
            return self._send({"ok": True, "added": added, "total": total,
                               "matches": result.get("matches"),
                               "error": result.get("error")})

        if route == "/arbeitnow" and arbeitnow_running():
            return self._send({"ok": False, "why": "Arbeitnow is already running"})

        if route == "/arbeitnow":
            # Arbeitnow is the one source that needs no browser: a free public
            # JSON feed, descriptions included. It is run detached and pushes
            # its own results back through /ingest, which is also why it must
            # NOT be run inline — this handler would still be holding the
            # request when the child tried to reach the bridge.
            log = OUT / "arbeitnow.log"
            try:
                OUT.mkdir(exist_ok=True)
                handle = open(log, "a", encoding="utf-8")
                handle.write(f"\n=== arbeitnow started "
                             f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
                handle.flush()
                _arbeitnow["proc"] = subprocess.Popen(
                    [str(PYTHON), "arbeitnow.py"], cwd=ROOT,
                    stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    **pu.detach_kwargs(),
                    env=dict(os.environ, BRIDGE_PORT=str(PORT)))
                _event("arbeitnow", "started", "collecting from Arbeitnow")
            except OSError as e:
                _event("arbeitnow", "failed", f"could not start Arbeitnow: {e}")
                return self._send({"ok": False, "why": str(e)}, 500)
            return self._send({"ok": True, "started": True, "log": str(log)})

        if route == "/searches":
            # Mirrored from the extension so a shell script can open a real
            # search page, which is also what wakes the service worker.
            global _searches
            _searches = [q for q in (body.get("searches") or []) if q.get("url")]
            # Keep a copy on disk, the whole list including switched-off ones.
            # A reinstalled extension restores from it (GET /searches/saved),
            # and a bridge restart no longer forgets what to launch.
            full = [q for q in (body.get("all") or body.get("searches") or [])
                    if isinstance(q, dict) and q.get("url")]
            if full:
                try:
                    OUT.mkdir(exist_ok=True)
                    _write_json(OUT / "searches.json", full)
                except OSError:
                    pass
            return self._send({"ok": True, "count": len(_searches)})

        if route == "/search-result":
            # One line per saved search, recorded as it finishes. This is the
            # audit: without it, a search that refused to load and a search with
            # nothing new both looked identical from the outside, which is how
            # three empty LinkedIn searches went unnoticed on 18 September.
            _search_log.append({**body,
                                "at": dt.datetime.now().isoformat(timespec="seconds")})
            _write_scrape_audit()
            return self._send({"ok": True, "logged": len(_search_log)})

        if route == "/scrape":
            global _scrape, _autobuild
            was_running = bool(_scrape.get("running"))
            _scrape = {
                "running": bool(body.get("running")),
                "done": int(body.get("done") or 0),
                "total": int(body.get("total") or 0),
                "at": dt.datetime.now().isoformat(timespec="seconds"),
            }
            # The edge, not the level. Only a transition from running to
            # finished starts a build, so the extension repeating "not running"
            # on every wake cannot start a second one.
            if _scrape["running"] and not was_running:
                _search_log.clear()      # a new run starts a new audit
                _event("scrape", "started", f"extension started {_scrape['total']} search(es)")
            if was_running and not _scrape["running"]:
                failed = [r for r in _search_log if not r.get("ok")]
                if failed:
                    _event("scrape", "failed",
                           f"{len(failed)} of {len(_search_log)} searches returned nothing: "
                           + "; ".join(f"{r.get('label')}: {r.get('why') or 'unknown'}" for r in failed[:3]),
                           fix="Keep the Chrome tab in front and press Retry; finished searches are not repeated.")
                else:
                    _event("scrape", "ok", f"{len(_search_log)} search(es) collected, "
                           f"{sum(int(r.get('added') or 0) for r in _search_log)} new jobs")
            finished = was_running and not _scrape["running"]
            started = None
            if finished and _autobuild:
                pool = len(_read_json(_active_inbox(), {"jobs": []}).get("jobs", []))
                if pool > 0:
                    started = start_build(f"scrape finished with {pool} jobs")
                    print(f"  autobuild: {started}")
                else:
                    print("  autobuild: scrape finished but the pool is empty")
            return self._send({"ok": True, "build": started})

        if route == "/build":
            # The structured manual trigger: start the ranking and the build
            # right now, against whatever is already in today's pool. Takes an
            # optional {"top": N} to override TOP_N for this run.
            top = body.get("top")
            return self._send(start_build("asked for by hand",
                                          int(top) if top else None))

        if route == "/build/stop":
            return self._send(stop_build())

        if route == "/rank/stop":
            return self._send(stop_rank())

        if route == "/rank":
            # Rank only — a preview of what would be built, no Claude, no PDFs.
            return self._send(start_rank())

        if route == "/autobuild":
            if "on" in body:
                _autobuild = bool(body["on"])
            return self._send({"ok": True, "autobuild": _autobuild})

        if route == "/trigger":
            # "Run the whole thing now." The shell cannot talk to a Chrome
            # extension, and the extension's own alarm only fires every twenty
            # minutes, which is far too slow to feel like a button. So the
            # request is parked here and the extension picks it up on its next
            # one minute poll.
            global _trigger, _trigger_only
            _trigger = dt.datetime.now().isoformat(timespec="seconds")
            _trigger_only = (body.get("only") or None)
            return self._send({"ok": True, "queued_at": _trigger,
                               "only": _trigger_only})

        if route == "/reset":
            # A "hard" reset also forgets that a job was ever seen. Without it,
            # erasing and re-scraping returns almost nothing: every posting from
            # an earlier day is still in the history and comes straight back as
            # seen-before, so a fresh start is not fresh at all. The history is
            # archived rather than dropped, so a mistaken click is recoverable.
            archived_build = 0
            if body.get("hard"):
                hist = ROOT / config.HISTORY_FILE
                if hist.exists():
                    _archive(hist)
                    hist.unlink(missing_ok=True)
                # Reset the build too: move today's finished batch aside so the
                # next build runs against the fresh pool instead of reporting the
                # day already complete.
                archived_build = _archive_todays_build()
            # Archive rather than delete. "Erase" in the popup means "stop
            # showing me these", not "destroy the only copy", and a mistaken
            # click should never cost a scrape that took real time to collect.
            moved = 0
            for path in list(INBOX.glob("*.json")):
                _archive(path)
                moved += 1
            for name in ("jobs.html", "jobs.json", "jobs.md", "all_jobs.json"):
                target = ROOT / "out" / name
                if target.exists():
                    target.unlink()
            # Restart the pipeline's own flags, not just the pool. Otherwise
            # the tracker keeps reporting the last scrape, rank and build after
            # a fresh start, which is exactly the stale state that should be
            # gone. Decision, 19 September 2026.
            global _reset_at
            _last_rank.update(ran_at=None, matches=None, collected=None, error=None)
            _scrape.update(running=False, done=0, total=0, at=None)
            _search_log.clear()
            _build.update(running=False, started=None, finished=None,
                          reason=None, pid=None, log=None)
            # Roll the on-disk audit aside too, so a tail or a bridge restart
            # does not resurrect the old run's per-search table.
            for _name in ("scrape_audit.txt", "scrape_audit.json"):
                _f = OUT / _name
                if _f.exists():
                    _archive(_f)
            _reset_at = dt.datetime.now().timestamp()
            try:
                _RESET_MARK.write_text(str(_reset_at))
            except OSError:
                pass
            _event("reset", "info",
                   f"erased the job pool ({moved} file(s) archived)"
                   + (f", {archived_build} day(s) of CVs moved to applications/archive/"
                      if body.get("hard") else ""))
            return self._send({"ok": True, "archived": moved,
                               "build_archived": archived_build})

        if route == "/applied":
            jobs = [j for j in (body.get("jobs") or []) if isinstance(j, dict)]
            urls = body.get("urls") or ([body["url"]] if body.get("url") else [])
            urls = list(dict.fromkeys([*urls, *(j.get("url") for j in jobs if j.get("url"))]))
            if not urls:
                return self._send({"error": "no urls"}, 400)
            added, total = merge_applied(urls, jobs)
            if added:
                _event("applied", "info", f"marked {added} job(s) applied ({total} in total)")
            result = rerank() if added else dict(_last_rank)
            return self._send({"ok": True, "added": added, "total": total,
                               "matches": result.get("matches")})

        self._send({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s  %s\n" % (dt.datetime.now().strftime("%H:%M:%S"), fmt % args))


def _migrate_history() -> None:
    """One-time moves into history/: applied.json -> applied.csv, and the last
    SEEN_DAYS days of collected jobs -> seen.csv, so repeats are caught from day one."""
    if APPLIED.exists():
        n = ledger.migrate_applied_json(APPLIED, _lookup_job)
        _event("applied", "info", f"moved {n} applied job(s) from applied.json to history/applied.csv")
    if not ledger.SEEN_CSV.exists():
        days = getattr(config, "SEEN_DAYS", 30)
        cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        jobs = []
        for f in [*sorted(ARCHIVE.glob("*.json")), *sorted(INBOX.glob("*.json"))] if INBOX.exists() else []:
            stamp = re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
            if not stamp or stamp.group(1) < cutoff:
                continue
            payload = _read_json(f, {})
            for j in (payload.get("jobs", []) if isinstance(payload, dict) else payload or []):
                if isinstance(j, dict):
                    jobs.append({**j, "_collected": stamp.group(1)})
        n = ledger.record_seen(jobs)
        _event("collect", "info", f"started history/seen.csv with {n} role(s) collected in the last {days} days")


def main() -> int:
    INBOX.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    threading.Thread(target=_model_watch, daemon=True, name="model-watch").start()
    try:
        _migrate_history()
    except Exception as e:                                   # noqa: BLE001
        _event("data", "failed", f"could not set up history/: {e}")
    if not PYTHON.exists():
        print(f"! {PYTHON} not found — is the venv set up?", file=sys.stderr)
        return 1
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        # Almost always "address already in use": another copy is running.
        # Exit 0 so start.sh does not keep restarting into the same wall.
        print(f"! cannot listen on {HOST}:{PORT} ({e}). Is it already running? "
              f"Open http://127.0.0.1:{PORT}/dashboard", file=sys.stderr)
        return 0
    print(f"Job collector bridge listening on http://{HOST}:{PORT}")
    print(f"  inbox   {_active_inbox()}")
    print(f"  applied {ledger.APPLIED_CSV}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
