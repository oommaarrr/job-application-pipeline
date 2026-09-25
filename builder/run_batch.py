#!/usr/bin/env python3
"""
Scheduled /apply-batch, resumable. The build, from pool to PDFs.

    python run_batch.py            build every role that fits, up to the target
    python run_batch.py 5          a deliberately small run: at most 5
    FORCE=1 python run_batch.py    build more even though today is complete

This was run-batch.sh, 864 lines of bash, until 25 September 2026. It moved to
Python so the same build runs on Windows, where there is no bash, no pgrep and
no process groups. Every behaviour was carried over; run-batch.sh is now a
two-line wrapper so anything that called it still works.

Runs Claude Code headless, in this directory, so it has the skill, the
reference files, the venv and applications/ exactly as an interactive session
would. Nothing goes to the cloud and nothing needs a browser.

Two waits shape this script, and they exist for different reasons.

The first is for the scrape. It starts at a random minute inside its window,
so a fixed start time here would sometimes fire mid-collect and build
documents from half a pool. This polls the bridge until the scrape reports it
has finished, which is the only reliable "done" signal from outside the
extension.

The second is the retry loop. Building a batch is twenty minutes of model
calls, file writes and PDF generation, and the ways it fails are mostly
transient: the network drops, a rate limit is hit, the machine sleeps. The work
already on disk is counted, the remainder is what gets asked for, and the day
is only finished when the documents are actually there.

Safe to run repeatedly. If today is already complete it exits in a second.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- roots
# Everything is derived from where this file is, so the checkout runs from any
# directory. Override with SCRAPER_DIR=... or PY=... if your layout differs.
BUILDER_ROOT = pathlib.Path(__file__).resolve().parent
REPO_ROOT = BUILDER_ROOT.parent
SCRAPER_DIR = pathlib.Path(os.environ.get("SCRAPER_DIR") or REPO_ROOT / "scraper").resolve()
sys.path.insert(0, str(SCRAPER_DIR))

if __name__ == "__main__":
    import bootstrap                                         # noqa: E402
    bootstrap.ensure(__file__)
import platform_util as pu                                   # noqa: E402

_venv_py = pu.venv_python(SCRAPER_DIR / ".venv")
PY = os.environ.get("PY") or (str(_venv_py) if _venv_py.exists() else sys.executable)

os.chdir(BUILDER_ROOT)
# Every Python child (the ranker, the PDF builder that Claude runs) reads and
# writes UTF-8, whatever the Windows code page is.
os.environ.update(pu.utf8_env())

LOG_DIR = pathlib.Path("logs")
LOG_DIR.mkdir(exist_ok=True)
DAY = dt.date.today().isoformat()
LOG = LOG_DIR / f"{DAY}.log"
_log_lock = threading.Lock()
# Every child this run starts (ranker, checks, Claude sessions), so stopping the
# run stops them too, however it was started.
_children: list[subprocess.Popen] = []


def _append(text: str) -> None:
    with _log_lock, open(LOG, "a", encoding="utf-8") as fh:
        fh.write(text)


def say(msg: str) -> None:
    _append(f"[{dt.datetime.now():%H:%M:%S}] {msg}\n")


def notify(message: str, subtitle: str = "") -> None:
    pu.notify("Job Pipeline", message, subtitle)


# ---------------------------------------------------------------- outcome
#
# Every way this script ends records WHY in logs/last-outcome.json, in plain
# words with the fix, and the bridge turns it into a line in the dashboard's
# Activity panel with a Retry button. Before this the only record was the log,
# and "nothing happened" looked the same whether the pool was empty, Claude
# was signed out or the usage window was spent.
class End(Exception):
    def __init__(self, status: str, message: str, fix: str = "", code: int = 0):
        super().__init__(message)
        self.status, self.message, self.fix, self.code = status, message, fix, code


def end_with(status: str, message: str, fix: str = "", code: int = 0):
    """end_with <ok|failed|stopped|info> "<what happened>" "<what to do>" <exit code>"""
    say(message)
    raise End(status, message, fix, code)


def write_outcome(end: End) -> None:
    try:
        built = n_built()
    except OSError:
        built = 0
    data = {"at": dt.datetime.now().isoformat(timespec="seconds"),
            "status": end.status, "message": end.message, "fix": end.fix,
            "built": built, "pid": os.getpid()}
    tmp = LOG_DIR / "last-outcome.json.tmp"
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, LOG_DIR / "last-outcome.json")
    except OSError:
        pass


# Stopping from outside (the dashboard's Stop, a kill, Ctrl+C) is recorded as
# "stopped", not as a crash. Raising from the handler unwinds through the
# finally below, which writes the outcome and releases the lock. On Windows a
# kill is TerminateProcess and no handler runs; the bridge records the stop
# itself in that case.
def _on_signal(signum, _frame):
    raise End("stopped", "the build was stopped" if signum != signal.SIGINT
              else "the build was interrupted",
              "Press Build to continue. Finished applications are kept and skipped.",
              143 if signum != signal.SIGINT else 130)


# ---------------------------------------------------------------- one at a time
#
# Only one batch may run on this machine. Decision, 19 September 2026.
#
# The bridge already refuses to start a second build, but that lock only covers
# builds the bridge itself starts. Anything run by hand walks straight past it,
# and on 19 September two were alive at once, both writing to the same logs and
# the same applications folder.
#
# mkdir is the lock because it is atomic on every system: it either creates the
# directory or fails, with no window between checking and taking it.
LOCK = LOG_DIR / ".run.lock"


def take_lock() -> bool:
    try:
        LOCK.mkdir()
    except FileExistsError:
        try:
            other = int((LOCK / "pid").read_text().strip())
        except (OSError, ValueError):
            other = None
        if other and other != os.getpid() and pu.alive(other) and _is_build(other):
            started = pu.started_at(other)
            say(f"another batch is already running (pid {other}, started {started}) "
                "— not starting a second")
            print(f"A batch is already running: pid {other}, started {started}")
            print(f"Watch it:  the log is {(BUILDER_ROOT / LOG)}")
            return False
        # The holder is gone, so the lock is stale. This is the case that
        # matters after a crash or a kill: without it the next run would
        # refuse forever.
        say(f"clearing a stale lock left by pid {other or 'unknown'}")
        shutil.rmtree(LOCK, ignore_errors=True)
        try:
            LOCK.mkdir()
        except FileExistsError:
            print("could not take the run lock")
            raise SystemExit(1)
    (LOCK / "pid").write_text(str(os.getpid()))
    return True


def _is_build(pid: int) -> bool:
    """A reused pid must not hold the lock forever. Windows recycles pids
    quickly, so 'some process has this number' is not enough."""
    line = " ".join(pu.cmdline(pid))
    return "run_batch.py" in line or "run-batch.sh" in line


def release_lock() -> None:
    # Only release a lock we still hold, so a run stopped late cannot delete
    # a lock that a DIFFERENT run has since taken.
    try:
        if (LOCK / "pid").read_text().strip() == str(os.getpid()):
            shutil.rmtree(LOCK, ignore_errors=True)
    except OSError:
        pass


# ---------------------------------------------------------------- settings
BRIDGE = f"http://127.0.0.1:{os.environ.get('BRIDGE_PORT', '8765')}/status"
# No number means "every role that fits", which is the standing rule in
# apply-batch.md. A number is only passed when one is given on the command
# line, which is how a deliberately small test run works.
BATCH_SIZE = next((a for a in sys.argv[1:] if a.isdigit()), "")
TODAY_DIR = pathlib.Path("applications") / DAY
STATE = TODAY_DIR / ".batch-state"
MAX_WAIT = 30 * 60
MAX_ATTEMPTS = 3
# 2 minutes. Decision, 19 September 2026: a rate limit clears in seconds, a
# usage limit takes hours (and is handled separately), and a bad shortlist does
# not clear at all. Ten minutes was long enough to feel broken and too short to
# fix anything.
RETRY_GAP = int(os.environ.get("RETRY_GAP_S", str(2 * 60)))
MANUAL = os.environ.get("BUILD_MANUAL") == "1"


# A company folder counts as built only when both documents are present and
# neither is a stub. build_docs.py inflates every PDF past 8 KB, so anything
# smaller is a half-written file from a run that was interrupted mid-write.
def _big_pdf(d: pathlib.Path, pattern: str) -> bool:
    return any(f.is_file() and f.stat().st_size > 8 * 1024 for f in d.glob(pattern))


def built_companies() -> list[str]:
    if not TODAY_DIR.is_dir():
        return []
    return sorted(d.name for d in TODAY_DIR.iterdir()
                  if d.is_dir() and _big_pdf(d, "*_CV_*.pdf")
                  and _big_pdf(d, "*_CoverLetter_*.pdf"))


def n_built() -> int:
    return len(built_companies())


def run_logged(args: list[str], tee: bool = False) -> tuple[int, str]:
    """Run a child, append its output to the day log, return (code, output).

    tee=True also echoes it, when someone is watching in a terminal. Under the
    bridge stdout IS the day log, so echoing there would write every line twice."""
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="replace", **pu.quiet_kwargs())
    except OSError as e:
        say(f"could not run {pathlib.Path(args[1] if len(args) > 1 else args[0]).name}: {e}")
        return 127, str(e)
    _children.append(proc)          # so a Stop also ends a ranker or check mid-run
    chunks = []
    echo = tee and sys.stdout is not None and sys.stdout.isatty()
    for line in proc.stdout:
        chunks.append(line)
        _append(line)
        if echo:
            sys.stdout.write(line)
    return proc.wait(), "".join(chunks)


# Finish the review page from whatever is on disk. A round cut off after the
# PDFs were written but before the report used to leave good applications with
# no page. The fragments and the PDFs are enough: merge, render, done.
def finish_report() -> None:
    if not TODAY_DIR.is_dir():
        return
    if not (TODAY_DIR / "batch.json").exists() and any(TODAY_DIR.glob("batch.shard-*.json")):
        say("finishing the report: merging shard fragments")
        if run_logged([PY, "merge_batches.py", str(TODAY_DIR)])[0] != 0:
            say("merge failed — see above")
    if (TODAY_DIR / "batch.json").exists():
        if run_logged([PY, "batch_report.py", DAY])[0] != 0:
            say("report render failed — see above")


def _config():
    try:
        import config
        return config
    except Exception:                                     # noqa: BLE001
        return None


def _read_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _get(url: str, timeout: float) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None


def net_up() -> bool:
    """Any HTTP answer at all means the network works; a 404 is still an answer."""
    for url in ("https://api.anthropic.com/", "https://www.google.com/"):
        try:
            urllib.request.urlopen(url, timeout=8).close()
            return True
        except urllib.error.HTTPError:
            return True
        except (urllib.error.URLError, OSError):
            continue
    return False


def status_json() -> dict | None:
    raw = _get(BRIDGE, 5)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


# Only the SCRAPE object's running flag. The status also carries
# "build": {"running": true} while a build is going, and matching that deadlocked
# a build on 20 September, waiting for a "scrape in flight" that was itself.
def scraping(js: dict) -> bool:
    return bool((js.get("scrape") or {}).get("running"))


def ollama_up() -> bool:
    return _get("http://127.0.0.1:11434/api/version", 4) is not None


def json_count(path: pathlib.Path) -> int:
    data = _read_json(path, [])
    return len(data) if isinstance(data, list) else 0


# ---------------------------------------------------------------- the build
def main() -> None:
    TODAY_DIR.mkdir(parents=True, exist_ok=True)
    cfg = _config()

    # Read the target early: the completion check needs to know what it is
    # aiming for.
    build_target_pre = int(BATCH_SIZE or getattr(cfg, "BUILD_TARGET", 15) or 15)

    # Already finished? batch.html is written in the last step, after every PDF
    # is verified, so its presence means the run reached the end. "Complete"
    # means the target was reached, not merely that a batch ran: on 18 September
    # a run of 6 wrote batch.html and every later attempt exited doing nothing
    # while the target was 15.
    if (TODAY_DIR / "batch.html").exists():
        have = n_built()
        if os.environ.get("FORCE") == "1":
            say(f"already complete for {DAY} ({have} built) — FORCE set, continuing")
        elif have < build_target_pre:
            say(f"{have} built for {DAY}, target is {build_target_pre} — building the rest")
        else:
            say("to build more anyway: FORCE=1 python run_batch.py [target]")
            end_with("info", f"already {have} built today, which meets the target of "
                     f"{build_target_pre}",
                     f"To build more today, set a number above {have} and press Build.", 0)

    # A build someone started by hand gets a fresh attempt budget. The budget
    # exists to stop an unattended schedule hammering a broken setup; it must
    # never make a deliberate click do nothing.
    if MANUAL:
        STATE.unlink(missing_ok=True)
    try:
        attempt = int(STATE.read_text().strip()) + 1
    except (OSError, ValueError):
        attempt = 1
    if attempt > MAX_ATTEMPTS:
        end_with("stopped", f"already tried {MAX_ATTEMPTS} times today, not trying again "
                 "automatically", "Press Build (or Retry) to try again now.", 0)
    STATE.write_text(str(attempt))
    say(f"=== batch attempt {attempt}/{MAX_ATTEMPTS} for {DAY} ===")

    # ------------------------------------------------------------ profile
    # Checked before anything slow, so a missing profile fails in a second.
    from profile_dir import profile_dir
    profile = profile_dir()
    os.environ["PROFILE_DIR"] = str(profile)
    # Claude runs with cwd = builder/, and the instructions refer to the profile
    # as `profile/...`. A link keeps those instructions identical for every user
    # (a junction on Windows, which needs no admin rights). Gitignored.
    try:
        pu.link_dir(profile, BUILDER_ROOT / "profile")
    except (OSError, subprocess.SubprocessError) as e:
        end_with("failed", f"could not link builder/profile to {profile}: {e}",
                 "Remove builder/profile if it is a real folder, then press Retry.", 1)

    # Never spend Claude usage writing CVs for the fictional example person.
    # setup copies the example to profiles/me, so compare the contents.
    example = REPO_ROOT / "profiles" / "example"
    unfilled = []
    for name in ("profile.md", "identity.md"):
        mine, ex = profile / name, example / name
        if mine.is_file() and ex.is_file() and mine.read_bytes() == ex.read_bytes():
            unfilled.append(name)
    ident = profile / "identity.md"
    if ident.is_file() and "example.com" in ident.read_text(encoding="utf-8", errors="replace") \
            and "identity.md" not in unfilled:
        unfilled.append("identity.md")
    if profile.name == "example" or unfilled:
        notify("Fill in your profile first", "Build not started")
        end_with("failed", "your profile is still the fictional example "
                 f"({' '.join(unfilled)}), so no CV was written",
                 'In the project folder run: claude "/make-profile path/to/your-cv.pdf", '
                 "then press Retry.", 1)

    # ------------------------------------------------------------ network
    # A laptop that has just woken has no usable network for a while. Starting
    # into that burns an attempt on a problem that clears by itself. Two hours
    # for an unattended run; two minutes when someone pressed a button and is
    # watching, so they hear about it instead of waiting.
    patience = 120 if MANUAL else 2 * 60 * 60
    deadline = time.time() + patience
    announced = False
    while not net_up():
        if time.time() >= deadline:
            say(f"no network after {patience // 60} min, giving up on this attempt")
            notify("will retry", "No internet, batch postponed")
            end_with("failed", "no internet connection, so nothing was built",
                     "Reconnect, then press Retry.", 0)
        if not announced:
            say("no network yet, waiting")
            announced = True
        time.sleep(60)
    if announced:
        say("network is back")

    # ------------------------------------------------------------ the pool
    # Rule: if the pool already has jobs and no scrape is running, BUILD. Do
    # not wait. Waiting is only useful when there is nothing to work with yet.
    js = status_json()
    if js is None:
        notify("nothing was built", "Bridge is down")
        end_with("failed", "the bridge is not answering, so the pool could not be read",
                 f"Run {pu.install_hint('start')}, then press Retry.", 1)
    have_jobs = int(js.get("collected_today") or 0)

    # What is actually buildable, which is not the same as what arrived today:
    # a posting does not expire at midnight (19 September).
    try:
        import pool_status
        pool = pool_status.status()
    except Exception:                                     # noqa: BLE001
        pool = {}
    usable = int(pool.get("usable") or 0)
    enough = bool(pool.get("enough_to_build"))
    stale = bool(pool.get("stale"))

    if enough and not stale and not scraping(js):
        say(f"{usable} usable role(s) already collected and nothing is scraping — building now")
        last = usable
    elif have_jobs > 0 and not scraping(js):
        say(f"{have_jobs} jobs collected today and nothing is scraping — building now")
        last = have_jobs
    else:
        if stale:
            say(f"the newest scrape is older than {pool.get('age_days', '?')} days "
                "— waiting for a fresh one")
        if usable > 0 and not enough:
            say(f"only {usable} usable role(s), not enough to fill a batch — waiting for a scrape")
        # Either a scrape is in flight, or the pool is empty and we hope one
        # starts. Watch until it is running and then not, or until the pool
        # becomes non-empty.
        start = time.time()
        last = 0
        while True:
            if time.time() - start > MAX_WAIT:
                say(f"gave up waiting after {MAX_WAIT // 60} min")
                break
            js = status_json()
            if js is None:
                say("bridge not answering, retrying")
                time.sleep(20)
                continue
            n = int(js.get("collected_today") or 0)
            if scraping(js):
                say(f"scrape in flight · {n} jobs so far")
                time.sleep(30)
                continue
            if n > 0:
                say(f"scrape finished · {n} jobs")
                notify(f"Scrape done: {n} jobs. Building applications…")
                last = n
                break
            say("pool is empty, waiting for a scrape")
            time.sleep(20)

    if last <= 0:
        notify("nothing was built", "No jobs collected today")
        end_with("info", "the job pool is empty, so there was nothing to build",
                 "Collect first: press + Arbeitnow, or run your saved searches.", 0)

    # ------------------------------------------------------------ rank locally
    # Every scraped description is read by a local model before Claude sees
    # anything: reading and extracting is cheap work an 8B model does well
    # enough, and writing a CV is not. The ranker writes out/ranked.json with
    # ONLY the top N, so /apply-batch never sees the rest.
    #
    # Two numbers, not one. BUILD_TARGET is how many applications should exist
    # at the end; TOP_N is how many candidates the ranker hands over to reach
    # it. Some roles can only be rejected after the description is read.
    cfg_target = int(getattr(cfg, "BUILD_TARGET", 15) or 15)
    cfg_max = int(getattr(cfg, "BUILD_TARGET_MAX", cfg_target) or cfg_target)
    cfg_top = int(getattr(cfg, "TOP_N", 20) or 20)
    build_target = int(BATCH_SIZE) if BATCH_SIZE else cfg_target
    build_target_max = int(BATCH_SIZE) if BATCH_SIZE else cfg_max
    rank_top = cfg_top if not BATCH_SIZE else build_target + (build_target + 2) // 3
    # Hand over enough candidates to reach the CEILING, not the floor, so a
    # target that grows after the rank has something to grow into. Overshooting
    # is free: /apply-batch stops at the target. Decision, 24 September 2026.
    rank_top = max(rank_top, build_target_max + (build_target_max + 2) // 3)

    # Ollama has to be up. It is a normal user process, not a service, so a
    # machine that rebooted has it installed and not running.
    if not ollama_up():
        say("ollama is not running, starting it")
        pu.start_ollama(LOG_DIR / "ollama.log")
    want_model = getattr(cfg, "OLLAMA_MODEL", "llama3.1")
    tags = _get(getattr(cfg, "OLLAMA_URL", "http://127.0.0.1:11434") + "/api/tags", 5)
    try:
        names = [m.get("name", "") for m in json.loads(tags or b"{}").get("models", [])]
    except ValueError:
        names = None
    if ollama_up() and names is not None and not any(
            n == want_model or n.startswith(want_model + ":") for n in names):
        end_with("info", f"the local model '{want_model}' is not downloaded yet, so nothing "
                 "was ranked", "It downloads by itself while the pipeline runs; press Build "
                 "again when the Activity panel says it is ready.", 0)
    if not ollama_up():
        notify("nothing was built", "Ollama is down")
        end_with("failed", "Ollama is not running and could not be started, so nothing "
                 "was ranked", "Open the Ollama app (or run: ollama serve), then press Retry.", 1)

    say(f"ranking {last} jobs with the local model (this is the slow part)")
    notify(f"Ranking {last} jobs locally…")
    say(f"target {build_target} built, from {rank_top} ranked candidates")
    code, out = run_logged([PY, str(SCRAPER_DIR / "rank_ollama.py"), "--top", str(rank_top)],
                           tee=True)
    if code != 0:
        if "Nothing to rank" in out:
            notify("nothing was built", "Nothing new to rank")
            end_with("info", "every job in the pool was already applied to or handled, "
                     "so there was nothing new to rank",
                     "Collect fresh jobs, then build again.", 0)
        notify("nothing was built", "Local ranking failed")
        end_with("failed", "the local ranking failed, so nothing was built from an "
                 "unranked pool", "Press Retry: jobs already judged are cached, so it "
                 "resumes. Details are in the build log.", 1)

    # Grow the target to match a strong pool. Decision, 24 September 2026.
    # "Strong" is the ranker's count of roles scoring >= STRONG_SCORE. The list
    # is already sorted best-first, so growing only ever adds weaker roles
    # after the strongest, never displaces a better one.
    strong = int(((_read_json(SCRAPER_DIR / "out" / "ollama_rank.json", {}) or {})
                  .get("health") or {}).get("strong") or 0)
    if strong > build_target:
        grown = min(strong, build_target_max)
        if grown > build_target:
            say(f"{strong} strong candidates — target rises from {build_target} to "
                f"{grown} (ceiling {build_target_max})")
            build_target = grown
            # Tell the dashboard, or its denominator stays at the old number.
            try:
                (SCRAPER_DIR / "out" / ".build_target").write_text(str(build_target))
            except OSError:
                pass

    # From here on the pool is the ranked file, not the raw scrape.
    ranked_file = SCRAPER_DIR / "out" / "ranked.json"
    ranked = json_count(ranked_file)
    if ranked <= 0:
        notify("nothing was built", "No roles passed the filters")
        end_with("info", "no job passed the ranking filters, so nothing was built",
                 "See why on the Fit ranking page (tick Show dropped). Collect more, "
                 "or loosen a filter in scraper/config_local.py.", 0)

    # Still open? A scrape is a snapshot and a CV takes a day to reach the
    # employer, so each candidate is checked against the live posting before
    # anything is written for it. Unverifiable ones are kept.
    say(f"checking which of the {ranked} are still open")
    run_logged([PY, str(SCRAPER_DIR / "check_live.py")], tee=True)
    ranked = json_count(ranked_file)
    if ranked <= 0:
        notify("nothing was built", "All candidate postings have closed")
        end_with("info", "every candidate posting has closed, so nothing was built",
                 "Collect fresh jobs, then build again.", 0)
    say(f"{ranked} roles passed and are going to Claude")

    # The target is a CEILING, not a quota (19 September): build everything
    # that passed, up to the target. It counts everything built today, so the
    # pool only has to cover the REMAINDER.
    have = n_built()
    want = build_target - have
    if want > 0 and ranked < want:
        say(f"pool is short of a full batch — target drops from {build_target} to "
            f"{have + ranked} ({have} already built + {ranked} available)")
        build_target = have + ranked

    # ------------------------------------------------------------ environment
    # Runs on the Claude subscription, not API billing, so there is no dollar cap.
    model = os.environ.get("MODEL", "claude-sonnet-5")
    ref_cvs = os.environ.get("REF_CVS") or str(profile)

    # Resolve the binary rather than trusting PATH: a login agent gets a bare
    # PATH that does not include ~/.local/bin.
    claude = pu.find_claude()
    if not claude:
        notify("claude binary not found")
        end_with("failed", "Claude Code is not installed, so nothing could be written",
                 f"Install it: {pu.install_hint('claude')}, then press Retry.", 1)
    if pu.IS_WIN and not pu.find_git_bash():
        notify("Git for Windows is missing", "nothing was built")
        end_with("failed", "Git for Windows is not installed, and Claude Code needs its "
                 "Git Bash to run commands, so nothing could be written",
                 f"Install it: {pu.install_hint('git-bash')}, then press Retry.", 1)

    build_rounds(claude, model, ref_cvs, build_target)


# ---------------------------------------------------------------- watchdog + shards
#
# Two limits, because a build has two very different phases. claude -p prints
# nothing until it exits, so the only sign of life is documents appearing.
#
#   STARTUP_GRACE   before the FIRST new document Claude is reading: the whole
#                   shortlist, the skill and the references. On 20 September
#                   that alone took 9m24s, so allow a generous silence.
#   CLAUDE_STALL    once documents are appearing, a gap this long really is a
#                   hang (a dropped network, a wedged call).
STARTUP_GRACE = int(float(os.environ.get("STARTUP_GRACE_MIN", "25")) * 60)
CLAUDE_STALL = int(float(os.environ.get("CLAUDE_STALL_MIN", "12")) * 60)
# Parallel Claude sessions per round. Splitting cuts wall-clock roughly in
# proportion for the same total usage. Each shard sees a disjoint slice of the
# ranked pool (shortlist.py honours APPLY_SHARD / APPLY_SHARDS) and writes its
# own batch fragment; this merges them once all have finished.
SHARDS = int(os.environ.get("SHARDS", "2"))
SHARD_MIN = 4        # fewer than this to build and it is one session, not two
WATCH_EVERY = int(os.environ.get("WATCHDOG_EVERY_S", "30"))


def _file_count() -> int:
    return sum(1 for p in TODAY_DIR.rglob("*") if p.is_file()) if TODAY_DIR.is_dir() else 0


def _watch(proc: subprocess.Popen, done: threading.Event) -> None:
    """Kill one claude if it goes silent too long. Watches the whole day folder,
    which shards share: a healthy sibling still writing keeps a hung shard's
    timer alive until it finishes. Acceptable, since shards write together."""
    base = prev = _file_count()
    last_change = time.time()
    writing = False
    while not done.wait(WATCH_EVERY):
        if proc.poll() is not None:
            return
        cur = _file_count()
        now = time.time()
        if cur != prev:
            prev, last_change = cur, now
        if cur > base:
            writing = True
        limit = CLAUDE_STALL if writing else STARTUP_GRACE
        if now - last_change >= limit:
            say(f"watchdog: no new documents for {int((now - last_change) // 60)} min "
                f"(limit {limit // 60} min, phase={'building' if writing else 'reading'}) "
                f"— killing claude ({proc.pid})")
            pu.kill_tree(proc.pid, grace=5)
            return


def build_shard(claude: str, model: str, ref_cvs: str, want: str, idx: int, total: int,
                out: pathlib.Path) -> int:
    """One `claude -p /apply-batch` under the watchdog. Returns claude's status."""
    env = dict(os.environ, APPLY_SHARD=str(idx), APPLY_SHARDS=str(total))
    prompt = f"/apply-batch {want}".strip()
    with open(out, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen([claude, "-p", prompt,
                                 "--permission-mode", "bypassPermissions",
                                 "--model", model,
                                 "--add-dir", str(SCRAPER_DIR),
                                 "--add-dir", ref_cvs],
                                stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env, **pu.quiet_kwargs())
        _children.append(proc)
        done = threading.Event()
        watcher = threading.Thread(target=_watch, args=(proc, done), daemon=True)
        watcher.start()
        code = proc.wait()
        done.set()
        watcher.join(timeout=5)
    return code


def _stop_children() -> None:
    for p in _children:
        if p.poll() is None:
            pu.kill_tree(p.pid, grace=3)


def _read(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


LIMIT_RE = re.compile(r"hit your limit|usage limit|rate limit|quota", re.I)
AUTH_RE = re.compile(r"OAuth access token|authentication_error|Failed to authenticate|"
                     r"Invalid API key|Please run /login|Not logged in", re.I)
NOTHING_RE = re.compile(r"nothing to build|no batch to build|zero (eligible|ranked|open) "
                        r"roles?|nothing to rank|no eligible roles", re.I)


def build_rounds(claude: str, model: str, ref_cvs: str, build_target: int) -> None:
    for rnd in range(1, MAX_ATTEMPTS + 1):
        have = n_built()
        # Stop on the TARGET, not on the marker existing.
        if have >= build_target:
            break
        want = max(1, build_target - have)     # documents exist but no report: finish it

        # RESUME.md is how the command learns what not to rebuild (step 5 of
        # apply-batch.md), without touching the slash command's own arguments.
        resume = TODAY_DIR / "RESUME.md"
        if have > 0:
            resume.write_text(
                "# Resume notice\n\n"
                "A previous attempt today already built these companies. Their PDFs are\n"
                "on disk and verified. Do NOT rebuild them, do NOT re-rank them, and do\n"
                "NOT drop them from batch.json: carry their existing entries through.\n\n"
                + "".join(f"- {c}\n" for c in built_companies())
                + "\nBuild the number the command asks for, excluding the companies above.\n",
                encoding="utf-8")
            say(f"resuming: {have} already built, asking for {want}")
        else:
            resume.unlink(missing_ok=True)

        if rnd > 1:
            notify(f"Retrying build (round {rnd} of {MAX_ATTEMPTS})")

        # This round's output goes to its own file. A verdict about this round
        # has to be read from this round: grepping the whole day's log once
        # matched "nothing to build" written five hours earlier (3 September).
        round_out = LOG_DIR / f".round-{DAY}-{rnd}.out"
        for old in LOG_DIR.glob(f".round-{DAY}-{rnd}.shard-*.out"):
            old.unlink(missing_ok=True)

        # A small batch is not worth splitting: the fixed reading cost is paid
        # once per session.
        nshards = SHARDS if want >= SHARD_MIN else 1
        if nshards <= 1:
            say(f"round {rnd}: /apply-batch {want} on {model} (single session)")
            out = LOG_DIR / f".round-{DAY}-{rnd}.shard-1.out"
            status = build_shard(claude, model, ref_cvs, str(want), 1, 1, out)
            text = _read(out)
        else:
            say(f"round {rnd}: /apply-batch {want} split across {nshards} parallel "
                f"sessions on {model}")
            notify(f"Building {want} applications across {nshards} parallel sessions…")
            results: dict[int, int] = {}
            threads = []
            rem = want
            for i in range(1, nshards + 1):
                left = nshards - i + 1
                ws = (rem + left - 1) // left
                rem -= ws
                out = LOG_DIR / f".round-{DAY}-{rnd}.shard-{i}.out"

                def run(i=i, ws=ws, out=out):
                    results[i] = build_shard(claude, model, ref_cvs, str(ws), i, nshards, out)
                t = threading.Thread(target=run, daemon=True)
                t.start()
                threads.append(t)
                say(f"  shard {i}/{nshards}: building {ws}")
            # join with a timeout so a stop signal is handled while waiting
            for t in threads:
                while t.is_alive():
                    t.join(1)
            status = 0 if all(v == 0 for v in results.values()) else 1
            text = "".join(f"----- {p.name} -----\n{_read(p)}"
                           for p in sorted(LOG_DIR.glob(f".round-{DAY}-{rnd}.shard-*.out")))
            say("all shards finished — merging fragments and rendering the report")
            run_logged([PY, "merge_batches.py", str(TODAY_DIR)], tee=True)
            run_logged([PY, "batch_report.py", DAY], tee=True)
        round_out.write_text(text, encoding="utf-8")
        _append(text if text.endswith("\n") or not text else text + "\n")
        say(f"claude exited {status}")

        # A usage limit is not something a retry can fix: the quota resets at
        # a stated time. Stop, say when, and leave the attempt budget clean.
        if LIMIT_RE.search(text):
            m = re.search(r"resets [^.]*", text, re.I)
            when = m.group(0).strip() if m else ""
            notify(when or "try again after it resets", "Claude usage limit reached")
            STATE.unlink(missing_ok=True)
            finish_report()
            end_with("stopped", f"Claude usage limit reached{f' ({when})' if when else ''}. "
                     f"{n_built()} built so far",
                     "Press Retry after the limit resets. Finished applications are kept "
                     "and skipped.", 0)

        # A signed-out CLI is the one failure retrying cannot fix: headless has
        # no way to prompt for a login.
        if AUTH_RE.search(text):
            notify("No applications built", "Claude is signed out. Run: claude auth login")
            STATE.unlink(missing_ok=True)
            end_with("failed", "Claude is signed out, so nothing could be written",
                     "In a terminal run: claude auth login, then press Retry.", 1)

        # Claude stops ON PURPOSE when it cannot write honestly, with a
        # BATCH-STOP line. Retrying only re-spends usage to hear the same answer.
        m = re.search(r"BATCH-STOP:(.*)", text)
        if m:
            why = m.group(1)[:160]
            notify(why.strip() or "see the build log", "Build stopped")
            STATE.unlink(missing_ok=True)
            finish_report()
            end_with("stopped", f"Claude stopped the batch on purpose:{why}",
                     "Fix what it names, then press Retry.", 0)

        now_built = n_built()
        say(f"after round {rnd}: {now_built} built/{build_target}")
        if now_built >= build_target:
            break

        # "There was nothing eligible" is a verdict, not a failure.
        if NOTHING_RE.search(text):
            notify("nothing was built", "No eligible roles today")
            finish_report()
            end_with("info", "Claude read the candidates and found nothing eligible to build",
                     "Its reasons are on the Applications page. Collect more jobs and "
                     "build again.", 0)
        if now_built <= have and rnd < MAX_ATTEMPTS:
            say(f"no progress this round, waiting {RETRY_GAP // 60} min before retrying")
            time.sleep(RETRY_GAP)

    finish_report()
    final = n_built()
    if (TODAY_DIR / "batch.html").exists() and final > 0:
        say(f"done: {final} application folder(s) in {TODAY_DIR.as_posix()}")
        publish_index()
        notify(f"{final} applications ready — open them from the dashboard", DAY)
        (TODAY_DIR / "RESUME.md").unlink(missing_ok=True)
    elif final > 0:
        say(f"{final} built but no batch.html — will finish the report on the next attempt")
        notify("retrying later", f"{final} built, report incomplete")
    else:
        notify("Batch built nothing. Check the log.", str(LOG))
    say("=== attempt done ===")
    if final >= build_target and final > 0:
        end_with("ok", f"{final} application(s) built today", "", 0)
    if final > 0:
        end_with("stopped", f"{final} of {build_target} built after {MAX_ATTEMPTS} rounds",
                 "Press Retry to build the rest. Finished applications are kept and skipped.", 0)
    end_with("failed", "the build ran but produced no applications",
             "Open the build log from the dashboard to see why, then press Retry.", 1)


def publish_index() -> None:
    """applications/index.html always opens the newest report.

    A redirect page with an absolute file:// URL rather than a link, because the
    report links to its PDFs relatively and how those resolve through a link
    depends on the browser. as_uri() writes the right form on every system."""
    target = (BUILDER_ROOT / TODAY_DIR / "batch.html").resolve().as_uri()
    jobs = SCRAPER_DIR / "out" / "jobs.html"
    lines = ["<!doctype html><meta charset=utf-8>", "<title>Job Applications</title>",
             f'<meta http-equiv=refresh content="0; url={target}">',
             "<p>Opening today's applications…",
             f'<p><a href="{target}">Today\'s batch ({DAY})</a>']
    if jobs.exists():
        lines.append(f'<p><a href="{jobs.resolve().as_uri()}">All scraped jobs</a>')
    try:
        (BUILDER_ROOT / "applications" / "index.html").write_text("\n".join(lines) + "\n",
                                                                  encoding="utf-8")
        say(f"applications/index.html now opens {target}")
    except OSError:
        pass


def run() -> int:
    if not take_lock():
        return 0
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass
    if hasattr(signal, "SIGBREAK"):          # Ctrl+Break on Windows
        signal.signal(signal.SIGBREAK, _on_signal)
    end = End("failed", "the build stopped unexpectedly",
              "Press Retry. Finished applications are kept and skipped. "
              "Details are in the build log.", 1)
    try:
        main()
    except End as e:
        end = e
    except KeyboardInterrupt:
        end = End("stopped", "the build was interrupted",
                  "Press Build to continue. Finished applications are kept and skipped.", 130)
    except Exception as e:                                 # noqa: BLE001
        # Reached only by an exit nobody labelled: a bug here. The traceback
        # goes to the log so the Activity line has something behind it.
        import traceback
        _append(traceback.format_exc())
        end = End("failed", f"the build stopped unexpectedly ({type(e).__name__}: {e})",
                  "Press Retry. Finished applications are kept and skipped. "
                  "Details are in the build log.", 1)
    finally:
        # Ignore further signals while cleaning up, so a second Stop cannot
        # leave the lock behind.
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, signal.SIG_IGN)
            except (ValueError, OSError):
                pass
        if end.status == "stopped" and end.code in (130, 143):
            say(end.message)
        _stop_children()        # anything still running (a no-op on a normal end)
        write_outcome(end)
        release_lock()
    return end.code


if __name__ == "__main__":
    raise SystemExit(run())
