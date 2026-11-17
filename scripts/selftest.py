#!/usr/bin/env python3
"""
End-to-end self-test, the same on macOS, Linux and Windows.

    python scripts/selftest.py            (any Python 3.11+)

It copies the project to a temporary folder, runs the real setup there, starts
a real bridge on a spare port and drives real builds through it, so the parts
that differ per operating system (venvs, links, starting and stopping
processes, file locks, text encoding) are exercised for real. What it does NOT
use is anything that costs money or takes long:

  * Claude is replaced by a small fake `claude` that writes stub PDFs (or
    hangs, or reports a usage limit, depending on the scenario)
  * the Ollama ranker and the live-posting check are replaced by fakes
  * the Ollama model is not downloaded (setup --skip-model), and if Ollama is
    not running a stub answers its version check, so no install is needed

Your own checkout is never touched: no profile, log, inbox or history is read
or written. Everything happens in the temporary copy, which is deleted at the
end (kept with --keep, for poking at after a failure).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request

# Repeated names, kept in one place.
VENV = ".venv"
LOCAL_ENV = "local.env"
RANK_OLLAMA_REAL_PY = "rank_ollama_real.py"
PROFILE_MD = "profile.md"
JSON_TYPE = "application/json"
SCRIPTS_PYTHON_EXE = "Scripts/python.exe"
BIN_PYTHON = "bin/python"
STATUS_ROUTE = "/status"
START_PY = "start.py"
RUN_LOCK = ".run.lock"
JSON_GLOB = "*.json"
ML_ENGINEER = "ML Engineer"
SEEN_CSV = "seen.csv"
OLLAMA_RANK_JSON = "ollama_rank.json"
ARBEITNOW_SETTINGS_JSON = "arbeitnow_settings.json"
AI_ENGINEER = "AI Engineer"
GAMMA_GMBH = "Gamma GmbH"
RANKING_ROUTE = "/ranking"
DASHBOARD_ROUTE = "/dashboard"
MACHINE_LEARNING_ENGINEER = "Machine Learning Engineer"
SOFTWARE_ENGINEER = "Software Engineer"
ARBEITNOW_PLACES_JSON = "arbeitnow_places.json"
ARBEITNOW_SETTINGS_ROUTE = "/arbeitnow/settings"
PROGRESS_ROUTE = "/progress"
APPLIED_ROUTE = "/applied"
HTTPS_EVIL_EXAMPLE = "https://evil.example"
TRIGGER_ROUTE = "/trigger"
SEARCHES_LIST_ROUTE = "/searches/list"
SEARCHES_ADD_ROUTE = "/searches/add"
SEARCHES_ROUTE = "/searches"
SCRAPE_ROUTE = "/scrape"
PROFILE_CHAT_ROUTE = "/profile/chat"
SELFTEST_TRACKER = "Selftest Tracker"
PROFILE_CHAT_UNDO_ROUTE = "/profile/chat/undo"
README_MD = "README.md"
PROFILES_EXAMPLE_PROFILE_MD = "profiles/example/profile.md"
NEW_README = "new readme\n"
BUILD_ROUTE = "/build"

ROOT = pathlib.Path(__file__).resolve().parent.parent
IS_WIN = os.name == "nt"
DAY = dt.date.today().isoformat()
KEEP = "--keep" in sys.argv

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    results.append((bool(ok), label))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f"  — {detail}" if detail and not ok else ""), flush=True)
    return bool(ok)


def section(title: str) -> None:
    print(f"\n== {title}", flush=True)


# ------------------------------------------------------------------ the copy
def _ignore(dirpath: str, names: list[str]) -> set[str]:
    skip = {".git", VENV, "venv", "applications", "logs", "inbox", "out", "history",
            "__pycache__", LOCAL_ENV, "config_local.py", ".DS_Store", "_backups",
            "docs", "node_modules"}
    out = {n for n in names if n in skip or n.startswith(("applied.json", "purged-", ".!"))}
    here = pathlib.Path(dirpath)
    if here.name == "profiles":
        out |= {n for n in names if n != "example"}         # never copy a real profile
    if here.name == "builder":
        out |= {"profile"}                                   # a link to a real profile
    return out


FAKE_CLAUDE = r'''
import json, os, pathlib, subprocess, sys, time, datetime as dt
MODE_FILE = pathlib.Path(__file__).with_name("mode.txt")
mode = MODE_FILE.read_text().strip() if MODE_FILE.exists() else "ok"
args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": mode != "signedout"})); sys.exit(0)
if "--append-system-prompt" in args and "job-application profile" in args[args.index("--append-system-prompt") + 1]:
    # Edit profile (profile_chat.py): the message on stdin, run in the profile
    # folder. Records how it was called, then edits like Claude would.
    msg = sys.stdin.read().strip()
    with open(pathlib.Path(__file__).with_name("profile-calls.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"args": args, "cwd": os.getcwd(), "msg": msg}) + "\n")
    if mode == "auth": print("Invalid API key · Please run /login"); sys.exit(1)
    sid = args[args.index("--resume" if "--resume" in args else "--session-id") + 1]
    if msg.startswith("Add a project:"):
        name = msg.split(":", 1)[1].strip()
        p = pathlib.Path("reference") / "PROJECTS.md"
        p.write_text(p.read_text(encoding="utf-8") + f"| **{name}** | selftest Ü | any |\n", encoding="utf-8")
        reply = f"Added {name} to reference/PROJECTS.md."
    elif msg.startswith("Add a skill:"):
        p = pathlib.Path("profile.md")
        p.write_text(p.read_text(encoding="utf-8").replace("Strong: ", "Strong: " + msg.split(":", 1)[1].strip() + ", ", 1),
                     encoding="utf-8")
        reply = "Added it to Strong in profile.md."
    else:
        reply = "What are the dates?"
    print(json.dumps({"type": "result", "is_error": False, "result": reply, "session_id": sid}))
    sys.exit(0)
if "--system-prompt" in args and "job seeker's profile" in args[args.index("--system-prompt") + 1]:
    # Job preferences (preferences.py): the profile on stdin, one JSON object
    # back. The role comes from a "SELFTEST ROLE:" line when the profile has
    # one, and a marketing role is shaped by achievements, like Claude would.
    import re
    text = sys.stdin.read()
    with open(pathlib.Path(__file__).with_name("prefs-calls.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"args": args, "chars": len(text)}) + "\n")
    if mode == "prefsfail": print("Invalid API key · Please run /login"); sys.exit(1)
    m = re.findall(r"SELFTEST ROLE: (.+)", text)
    role = m[-1].strip() if m else "Machine Learning Engineer"
    ans = {"target_roles": [role, "Ärztliche Beratung"], "not_my_field": ["Computer vision"],
           "years_experience": 3, "max_years_asked": 4,
           "languages": [{"language": "English", "level": "C1"}, {"language": "German", "level": "a2"}],
           "places": [{"place": "Berlin", "country": "DE", "work": ["onsite", "hybrid", "remote"]},
                      {"place": "EU", "country": "", "work": ["remote"]}],
           "remote_anywhere": None, "relocate": False, "accept_management": False,
           "job_kinds": ["working_student"] if "Werkstudent" in role else ["regular", "Freelance"],
           "cv_shape": "achievements" if "Marketing" in role else "projects",
           "cv_max_pages": 1, "cv_state_years": False,
           "open_questions": [{"field": "accept_management",
                               "question": "Would you take a role that mainly manages a team?"},
                              {"field": "nonsense", "question": "  "}]}
    print(json.dumps({"type": "result", "is_error": False,
                      "result": "```json\n" + json.dumps(ans, ensure_ascii=False) + "\n```"}))
    sys.exit(0)
if "--output-format" in args:
    # The ranker's Claude engine: postings on stdin, answers by id. Like real
    # Haiku now and then, a batch leaves its last posting unanswered.
    import re
    ids = re.findall(r"=== POSTING (\S+) ===", sys.stdin.read())
    if mode == "rankhang": time.sleep(3600)       # a call that never answers
    keep = ids[:-1] if len(ids) > 1 else ids
    ans = [{"id": i, "role_match": "yes", "years_required": 2, "languages_required": [],
            "is_management": False, "location": "best", "job_kind": "regular", "fit": 80,
            "reason": "selftest"} for i in keep]
    print(json.dumps({"type": "result", "is_error": False, "result": json.dumps({"answers": ans})}))
    sys.exit(0)
prompt = args[args.index("-p") + 1] if "-p" in args else ""
parts = prompt.split()
want = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 3
shard, shards = os.environ.get("APPLY_SHARD", "1"), os.environ.get("APPLY_SHARDS", "1")
if mode == "limit": print("You've hit your limit · resets 5pm (Europe/Berlin)"); sys.exit(1)
if mode == "auth": print("Invalid API key · Please run /login"); sys.exit(1)
if not prompt.startswith("/apply-batch"):
    print("ok"); sys.exit(0)        # run_batch.py's check call before writing
if mode == "stop": print("BATCH-STOP: the profile has no projects"); sys.exit(0)
if mode == "hang": time.sleep(3600)
day = pathlib.Path("applications") / dt.date.today().isoformat()
built = []
for i in range(want):
    co = f"Firma Ü{shard}x{i}"
    d = day / co; d.mkdir(parents=True, exist_ok=True)
    for kind in ("CV", "CoverLetter"):
        (d / f"Test_Person_{kind}_{i}.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 9000)
    built.append({"company": co, "title": "ML Engineer (m/w/d)", "url": f"https://example.org/{shard}/{i}",
                  "files": {"CV": f"{co}/Test_Person_CV_{i}.pdf"}})
name = "batch.json" if shards == "1" else f"batch.shard-{shard}.json"
path = day / name
old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"built": []}
old["built"] += built; old["date"] = day.name
path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
if shards == "1":
    subprocess.run([sys.executable, "batch_report.py"], check=False)
print(f"built {want} (shard {shard}/{shards}) Ärger ✓")
'''

FAKE_RANK = r'''
"""SELFTEST FAKE: writes a ranking without touching Ollama."""
import json, os, pathlib
out = pathlib.Path(__file__).parent / "out"; out.mkdir(exist_ok=True)
jobs = [{"url": f"https://example.org/job/{i}", "company": f"Firma Ü{i}",
         "title": f"ML Engineer {i}"} for i in range(8)]
(out / "ranked.json").write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
(out / "ollama_rank.json").write_text(json.dumps({"health": {"strong": 0, "buildable": 8},
    "counts": {"scraped": 8, "judged": 8, "passed": 8, "sent_to_claude": 8}}), encoding="utf-8")
print("fake ranked 8 (Ümlaut)")
'''


def make_copy(tmp: pathlib.Path) -> pathlib.Path:
    work = tmp / "jp"
    shutil.copytree(ROOT, work, ignore=_ignore, symlinks=False)
    # The builds use a fake ranker; the real one is kept for the pool section.
    shutil.copy(work / "scraper" / "rank_ollama.py", work / "scraper" / RANK_OLLAMA_REAL_PY)
    (work / "scraper" / "rank_ollama.py").write_text(FAKE_RANK, encoding="utf-8")
    (work / "scraper" / "check_live.py").write_text('print("fake: all open")\n', encoding="utf-8")
    # A filled-in profile, so the "still the example" guard lets the build run.
    me = work / "profiles" / "me"
    shutil.copytree(work / "profiles" / "example", me)
    for name in ("identity.md", PROFILE_MD):
        f = me / name
        f.write_text(f.read_text(encoding="utf-8").replace("example.com", "selftest.invalid")
                     + "\n<!-- selftest -->\n", encoding="utf-8")
    inbox = work / "scraper" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    jobs = [{"url": f"https://www.linkedin.com/jobs/view/{i}", "title": f"ML Engineer {i}",
             "company": f"Firma Ü{i}", "description": "Python machine learning Straße " * 40,
             "source": "linkedin"} for i in range(6)]
    (inbox / f"job-collector-{DAY}.json").write_text(
        json.dumps({"jobs": jobs}, ensure_ascii=False), encoding="utf-8")
    return work


def make_fakebin(tmp: pathlib.Path, py: pathlib.Path) -> pathlib.Path:
    fb = tmp / "fakebin"
    fb.mkdir()
    (fb / "claude.py").write_text(FAKE_CLAUDE, encoding="utf-8")
    if IS_WIN:
        (fb / "claude.cmd").write_text(f'@"{py}" "%~dp0claude.py" %*\r\n', encoding="utf-8")
    else:
        sh = fb / "claude"
        sh.write_text(f'#!/bin/sh\nexec "{py}" "$(dirname "$0")/claude.py" "$@"\n')
        sh.chmod(0o755)
    return fb


def stub_ollama() -> None:
    """Answer Ollama's version check when no Ollama is running. The ranker is
    faked, so nothing else of Ollama is ever called."""
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 11434)) == 0:
            return                                     # a real one is running
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = (b'{"models":[{"name":"llama3.1:latest"}]}' if "tags" in self.path
                    else b'{"version":"selftest-stub"}')
            self.send_response(200)
            self.send_header("Content-Type", JSON_TYPE)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass                # quiet: the checks are the output, not every request
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 11434), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("  (no Ollama running: a stub answers its version check)")


def answering_ollama(port: int, models: list[dict] | None = None) -> None:
    """A stand-in Ollama on its own port that answers every question at once,
    so the REAL ranker can run without touching a real model."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", JSON_TYPE)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"models": models if models is not None
                        else [{"name": "llama3.1:latest"}]} if "tags" in self.path
                       else {"version": "selftest"})

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if "embed" in self.path:
                # The pre-filter: a gardener points one way, everything else
                # (target roles included) the other.
                texts = json.loads(body or b"{}").get("input") or []
                return self._send({"embeddings": [[0.0, 1.0] if "Gardener" in t else [1.0, 0.0]
                                                  for t in texts]})
            self._send({"response": json.dumps({
                "role_match": "yes", "years_required": 2, "languages_required": [],
                "is_management": False, "location": "best", "job_kind": "regular",
                "fit": 80, "reason": "selftest answer"})})

        def log_message(self, *a):
            pass                # quiet: the checks are the output, not every request
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------ helpers
class Env:
    def __init__(self, work: pathlib.Path, fakebin: pathlib.Path, port: int):
        self.work, self.fakebin, self.port = work, fakebin, port
        py = work / "scraper" / VENV / (SCRIPTS_PYTHON_EXE if IS_WIN else BIN_PYTHON)
        self.py = py
        self.env = dict(os.environ, BRIDGE_PORT=str(port), NO_NOTIFY="1",
                        PATH=str(fakebin) + os.pathsep + os.environ.get("PATH", ""),
                        PYTHONUTF8="1", RETRY_GAP_S="1", AUTOBUILD="0",
                        PREFS_EVERY_S="2", PREFS_LOCAL="0")
        self.env.pop("PROFILE_DIR", None)
        if IS_WIN:
            # The fake claude runs no shell commands, so it needs no Git Bash;
            # the runner only checks that one exists.
            self.env.setdefault("CLAUDE_CODE_GIT_BASH_PATH", sys.executable)
        self.builder = work / "builder"
        self.bridge = None

    def mode(self, m: str) -> None:
        (self.fakebin / "mode.txt").write_text(m)

    def get(self, path: str, timeout: float = 5):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=timeout) as r:
            body = r.read()
            return r.status, (json.loads(body) if body[:1] in (b"{", b"[") else body)

    def post(self, path: str, data: dict | None = None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=json.dumps(data or {}).encode(),
                                     headers={"Content-Type": JSON_TYPE})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def up(self) -> bool:
        try:
            return self.get(STATUS_ROUTE, 2)[0] == 200
        except OSError:
            return False

    def wait_up(self, seconds: float = 30) -> bool:
        end = time.time() + seconds
        while time.time() < end:
            if self.up():
                return True
            time.sleep(0.5)
        return False

    def start_bridge(self) -> None:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WIN else 0
        # The PLAIN Python running this test, not the project's: start.py must
        # hand itself over to the project's environment (bootstrap.py), which
        # is what a user typing `python start.py` or `python serve.py` gets.
        self.bridge = subprocess.Popen([sys.executable, str(self.work / START_PY), "--background"],
                                       cwd=self.work, env=self.env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=flags)

    def clean_day(self) -> None:
        shutil.rmtree(self.builder / "applications" / DAY, ignore_errors=True)
        shutil.rmtree(self.builder / "logs" / RUN_LOCK, ignore_errors=True)
        (self.builder / "logs" / "last-outcome.json").unlink(missing_ok=True)

    def outcome(self) -> dict:
        try:
            return json.loads((self.builder / "logs" / "last-outcome.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def built(self) -> int:
        d = self.builder / "applications" / DAY
        return sum(1 for c in d.iterdir() if c.is_dir()) if d.is_dir() else 0

    def run_build(self, target: str, mode: str, **extra) -> tuple[int, dict]:
        self.clean_day()
        self.mode(mode)
        env = dict(self.env, BUILD_MANUAL="1", **extra)
        code = subprocess.run([str(self.py), "run_batch.py", target], cwd=self.builder,
                              env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=300).returncode
        return code, self.outcome()


def claude_stops_section(py: pathlib.Path, sc: pathlib.Path, fakebin: pathlib.Path, port: int) -> None:
    """
    3 November 2026, a Windows laptop: Claude Code signed out, every ranking
    call timing out, and the ranker carrying on through all 128 jobs at
    "0 judged". Signed out, it must stop at once and say so; calls that never
    answer, it must give up on after two rounds, and leave no Claude running.
    """
    env = dict(os.environ, PYTHONUTF8="1", PATH=str(fakebin) + os.pathsep + os.environ.get("PATH", ""))

    def rank(mode: str) -> tuple[int, str, float]:
        (fakebin / "mode.txt").write_text(mode)
        t0 = time.time()
        r = subprocess.run([str(py), RANK_OLLAMA_REAL_PY, "--top", "5", "--engine", "claude", "--recheck"],
                           cwd=sc, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300, env=env)
        (fakebin / "mode.txt").write_text("ok")
        return r.returncode, r.stdout + r.stderr, time.time() - t0

    code, out, secs = rank("signedout")
    check(code == 3 and "RANKING STOPPED: Claude Code is signed out" in out and "claude auth login" in out
          and "a call failed" not in out,
          "signed out, the Claude ranker stops before any call and says to run claude auth login",
          f"exit {code}, {secs:.0f}s: {out[-300:]}")
    _write_local(sc, f'OLLAMA_URL = "http://127.0.0.1:{port}"\nCLAUDE_RANK_BATCH = 1\n'
                     "CLAUDE_RANK_CONCURRENCY = 2\nCLAUDE_RANK_TIMEOUT = 2\n")
    code, out, secs = rank("rankhang")
    time.sleep(1.5)
    left = ps(py, sc.parent, f"""
        import os, psutil
        print(sum(1 for p in psutil.process_iter(["pid", "cmdline"]) if p.info["pid"] != os.getpid()
                  and any({str(fakebin / "claude.py")!r} in (a or "") for a in (p.info["cmdline"] or []))))
    """)
    check(code == 3 and "got no answer" in out and "None has answered yet" in out
          and out.count("a call failed") == 4 and secs < 120 and left == "0",
          "calls that never answer: the ranker gives up after two rounds and leaves no Claude running",
          f"exit {code}, {secs:.0f}s, {left} left running: {out[-400:]}")


def ps(py: pathlib.Path, work: pathlib.Path, code: str) -> str:
    """Run a snippet with the copy's venv and platform_util importable."""
    return subprocess.run([str(py), "-c", "import sys; sys.path.insert(0, r'%s')\n%s"
                           % (work / "scraper", textwrap.dedent(code))],
                          capture_output=True, text=True, encoding="utf-8",
                          env=dict(os.environ, PYTHONUTF8="1"), timeout=120).stdout.strip()


# ------------------------------------------------------------------ the tests
def pool_section(py: pathlib.Path, work: pathlib.Path, fakebin: pathlib.Path) -> None:
    """
    One job of each kind, through the real rank_ollama.py and pool.py:
      Alpha    judged on an earlier day        -> goes to the model again
                                               (dropped only with DROP_REPEATS)
      Beta     collected earlier, never judged -> goes to the model
      Gamma    applied to                      -> dropped
      Delta    no description                  -> dropped
      Epsilon, Zeta  new                       -> go to the model
    and the dashboard's count must equal what the ranker reads.
    """
    sc = work / "scraper"
    port = free_port()
    answering_ollama(port)
    _write_local(sc, f'OLLAMA_URL = "http://127.0.0.1:{port}"\n')
    for f in (sc / "inbox").glob(JSON_GLOB):
        f.unlink()
    (sc / "out" / "ollama_cache.json").unlink(missing_ok=True)
    hist = sc / "history"
    hist.mkdir(exist_ok=True)
    desc = "Python machine learning in Berlin, Straße. " * 30
    jobs = [{"url": f"https://www.linkedin.com/jobs/view/{900 + i}", "company": c,
             "title": ML_ENGINEER, "location": "Berlin", "source": "linkedin",
             "description": "" if c == "Delta" else desc}
            for i, c in enumerate(["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta"])]
    (sc / "inbox" / f"job-collector-{DAY}.json").write_text(json.dumps({"jobs": jobs}),
                                                            encoding="utf-8")
    (hist / SEEN_CSV).write_text("first_seen,company,title,judged\n"
                                   "2026-01-02,Alpha,ML Engineer,2026-01-02\n"
                                   "2026-01-02,Beta,ML Engineer,\n".replace("2026-01-02", _days_ago(3)),
                                   encoding="utf-8")
    (hist / "applied.csv").write_text("date,company,title,url\n"
                                      f"{DAY},Gamma,ML Engineer,https://www.linkedin.com/jobs/view/902\n",
                                      encoding="utf-8")
    (hist / ".judged-backfilled").write_text("selftest\n", encoding="utf-8")

    def status() -> dict:
        out = subprocess.run([str(py), "pool_status.py"], cwd=sc, capture_output=True,
                             text=True, encoding="utf-8", timeout=120).stdout
        try:
            return json.loads(out)
        except ValueError:
            return {"error": out[-300:]}

    s1 = status()
    b = s1.get("breakdown", {})
    check(b.get("to_judge") == 4 and b.get("repeat") == 0 and b.get("applied") == 1
          and b.get("no_description") == 1 and b.get("collected") == 6,
          "every collected job in exactly one bucket (4 to judge, 1 applied, 1 no description); "
          "a job judged on an earlier day is judged again",
          json.dumps(s1)[:400])
    _write_local(sc, f'OLLAMA_URL = "http://127.0.0.1:{port}"\nDROP_REPEATS = True\n')
    b = status().get("breakdown", {})
    check(b.get("to_judge") == 3 and b.get("repeat") == 1,
          "DROP_REPEATS = True still drops a role judged on an earlier day", json.dumps(b))
    _write_local(sc, f'OLLAMA_URL = "http://127.0.0.1:{port}"\n')
    r = subprocess.run([str(py), RANK_OLLAMA_REAL_PY, "--top", "5"], cwd=sc, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=300,
                       env=dict(os.environ, PYTHONUTF8="1"))
    rank = json.loads((sc / "out" / OLLAMA_RANK_JSON).read_text(encoding="utf-8")) \
        if (sc / "out" / OLLAMA_RANK_JSON).exists() else {}
    judged = rank.get("counts", {}).get("judged")
    check(r.returncode == 0 and judged == s1.get("usable") == 4,
          "the ranker reads exactly the number the dashboard shows", f"exit {r.returncode}, "
          f"ranker {judged}, dashboard {s1.get('usable')}: {(r.stdout + r.stderr)[-300:]}")
    check(rank.get("not_read") == {"repeat": 0, "applied": 1, "no_description": 1, "too_old": 0},
          "the ranker records why the rest were not read", str(rank.get("not_read")))
    seen = (hist / SEEN_CSV).read_text(encoding="utf-8")
    beta = next((l for l in seen.splitlines() if l.split(",")[1:2] == ["Beta"]), "")
    check(beta.endswith(DAY), "a role never judged before goes to the model, and is recorded as judged", beta)
    s2 = status()
    check(s2.get("usable") == 4, "ranking the same pool again the same day drops nothing",
          json.dumps(s2.get("breakdown")))

    # The cache answers an unchanged posting against an unchanged profile, and
    # asks again once the profile changes.
    def rerank() -> str:
        return subprocess.run([str(py), RANK_OLLAMA_REAL_PY, "--top", "5"], cwd=sc,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=300, env=dict(os.environ, PYTHONUTF8="1")).stdout
    out1 = rerank()
    prof = work / "profiles" / "me" / PROFILE_MD
    before_prof = prof.read_text(encoding="utf-8")
    prof.write_text(before_prof + "\nNew project: a selftest.\n", encoding="utf-8")
    out2 = rerank()
    prof.write_text(before_prof, encoding="utf-8")
    check("4 judged (0 new, 4 cached)" in out1 and "4 judged (4 new, 0 cached)" in out2,
          "the same profile is answered from the cache; a changed profile is judged again",
          (out1[-200:] + " | " + out2[-200:]))

    # A cache from before the key carried the profile: carried over to the
    # current profile, not asked again (3 November 2026, "stuck on 0 judged").
    cache_f = sc / "out" / "ollama_cache.json"
    cache = json.loads(cache_f.read_text(encoding="utf-8"))
    cache_f.write_text(json.dumps({k.rsplit("|", 1)[0]: v for k, v in cache.items()}), encoding="utf-8")
    out3 = rerank()
    check("4 judged (0 new, 4 cached)" in out3 and "  0/4 judged" in out3,
          "an older cache is carried over on update, and the ranker says 0 of N from the start",
          out3[-300:])

    # The Claude engine (October 2026): the same real ranker, Claude answering
    # several postings per call. The stand-in leaves one posting out of each
    # batch, which must be asked about again on its own rather than lost.
    _write_local(sc, f'OLLAMA_URL = "http://127.0.0.1:{port}"\nCLAUDE_RANK_BATCH = 3\n')
    r = subprocess.run([str(py), RANK_OLLAMA_REAL_PY, "--top", "5", "--engine", "claude"],
                       cwd=sc, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300, env=dict(os.environ, PYTHONUTF8="1",
                                             PATH=str(fakebin) + os.pathsep + os.environ.get("PATH", "")))
    rank = json.loads((sc / "out" / OLLAMA_RANK_JSON).read_text(encoding="utf-8")) \
        if (sc / "out" / OLLAMA_RANK_JSON).exists() else {}
    c = rank.get("counts", {})
    read = [x for x in rank.get("passed", []) + rank.get("dropped", []) if "reason" in x and x.get("verdict") != "far-from-target"]
    check(r.returncode == 0 and rank.get("engine") == "claude" and c.get("judged") == 4
          and not c.get("failed") and len(read) == 4 and all(x["reason"] == "selftest" for x in read),
          "Claude ranks in batches and asks again about a posting a batch left out",
          f"exit {r.returncode}, counts {c}: {(r.stdout + r.stderr)[-400:]}")
    claude_stops_section(py, sc, fakebin, port)
    _write_local(sc, f'OLLAMA_URL = "http://127.0.0.1:{port}"\n')

    # The pre-filter: with target roles set, a job far from them is set aside
    # before any model reads it, and what is read plus what is set aside is
    # still exactly what the dashboard counts as "to judge".
    settings = sc / "out" / ARBEITNOW_SETTINGS_JSON
    settings.write_text(json.dumps({"searches": [{"name": ML_ENGINEER, "on": True,
                                    "looking_for": "machine learning engineer, Python"}]}),
                        encoding="utf-8")
    inbox_f = sc / "inbox" / f"job-collector-{DAY}.json"
    before_inbox = inbox_f.read_text(encoding="utf-8")
    inbox_f.write_text(json.dumps({"jobs": jobs + [{
        "url": "https://www.linkedin.com/jobs/view/999", "company": "Gartenbau", "title": "Gardener",
        "location": "Berlin", "source": "linkedin", "description": "Gardener " * 60}]}), encoding="utf-8")
    s3 = status()
    r = subprocess.run([str(py), RANK_OLLAMA_REAL_PY, "--top", "5", "--engine", "local"],
                       cwd=sc, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300, env=dict(os.environ, PYTHONUTF8="1"))
    rank = json.loads((sc / "out" / OLLAMA_RANK_JSON).read_text(encoding="utf-8"))
    c = rank.get("counts", {})
    far = [x for x in rank.get("dropped", []) if x.get("verdict") == "far-from-target"]
    check(r.returncode == 0 and c.get("set_aside") == 1 and len(far) == 1
          and far[0]["company"] == "Gartenbau"
          and c.get("judged", 0) + c.get("set_aside", 0) == s3.get("usable"),
          "the pre-filter sets aside a job far from the target roles, and the counts still add up",
          f"exit {r.returncode}, counts {c}, dashboard {s3.get('usable')}: {(r.stdout + r.stderr)[-300:]}")
    settings.unlink(missing_ok=True)
    inbox_f.write_text(before_inbox, encoding="utf-8")

    # A history written before the judged column: filled in once from the cache.
    (hist / ".judged-backfilled").unlink()
    (hist / SEEN_CSV).write_text("first_seen,company,title\n"
                                   f"{_days_ago(3)},Epsilon,ML Engineer\n"
                                   f"{_days_ago(3)},Omega,ML Engineer\n", encoding="utf-8")
    status()
    seen = (hist / SEEN_CSV).read_text(encoding="utf-8")
    eps = next((l for l in seen.splitlines() if ",Epsilon," in l), "")
    om = next((l for l in seen.splitlines() if ",Omega," in l), "")
    check(eps.endswith(_days_ago(3)) and om.endswith(","),
          "an old history is backfilled: judged only where the model has an answer", seen[-300:])

    # PR #1: llama3.1 is not installed but another local model is. The ranker
    # must use that one (never an embedding model or an Ollama cloud model,
    # which would send job data off the machine), and nothing is downloaded.
    port2 = free_port()
    answering_ollama(port2, [{"name": "nomic-embed-text:latest",
                              "details": {"family": "nomic-bert"}},
                             {"name": "gpt-oss:120b-cloud", "remote_host": "https://ollama.com"},
                             {"name": "qwen3:8b"}])
    _write_local(sc, f'OLLAMA_URL = "http://127.0.0.1:{port2}"\n')
    r2 = subprocess.run([str(py), RANK_OLLAMA_REAL_PY, "--top", "5"], cwd=sc,
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=300, env=dict(os.environ, PYTHONUTF8="1"))
    rank = json.loads((sc / "out" / OLLAMA_RANK_JSON).read_text(encoding="utf-8"))
    check(rank.get("model") == "qwen3:8b",
          "an installed model is used instead of downloading llama3.1 (not embedding, not cloud)",
          f"model {rank.get('model')}, exit {r2.returncode}: {(r2.stdout + r2.stderr)[-500:]}")
    _write_local(sc, f'OLLAMA_URL = "http://127.0.0.1:{port2}"\n'
                                        "OLLAMA_USE_INSTALLED = False\n")
    out = subprocess.run([str(py), "-c", "import config, ollama_model as m; "
                          "print(m.resolve(config))"], cwd=sc, capture_output=True, text=True,
                         timeout=60).stdout.strip()
    check(out == "None", "OLLAMA_USE_INSTALLED = False insists on llama3.1 (download needed)", out)
    _write_local(sc, None)


def recovery_section(py: pathlib.Path, work: pathlib.Path, port: int) -> None:
    """
    A session cut off by a usage limit leaves finished applications on disk
    that no batch record lists. On 25 September 2026 six of them went missing
    from the Applications page and were built again the next day. The merge
    must record every finished one, and only finished ones.
    """
    b = work / "builder"
    day = b / "applications" / "2026-01-02"
    pdf = b"%PDF-1.4\n" + b"0" * 9000

    def app(name: str, entry: dict | None = None, finished: bool = True) -> None:
        d = day / name
        d.mkdir(parents=True, exist_ok=True)
        (d / f"cv_{name.lower()}.json").write_text("{}", encoding="utf-8")
        if finished:
            (d / f"Alex_Rivera_CV_{name}.pdf").write_bytes(pdf)
            (d / f"Alex_Rivera_CoverLetter_{name}.pdf").write_bytes(pdf)
        if entry:
            (d / "entry.json").write_text(json.dumps(entry), encoding="utf-8")

    def row(company: str, title: str, url: str, folder: str) -> dict:
        return {"company": company, "title": title, "url": url, "why": "", "flags": [],
                "files": {"CV": f"{folder}/Alex_Rivera_CV_{folder}.pdf",
                          "cover letter": f"{folder}/Alex_Rivera_CoverLetter_{folder}.pdf"}}

    app("Alpha")
    app("Confidential")
    app("Beta", {"company": "Beta", "title": ML_ENGINEER, "url": "https://example.com/beta"})
    app("Gamma")
    app("Delta", finished=False)
    alpha = row("Alpha", AI_ENGINEER, "https://example.com/alpha", "Alpha")
    # Recorded on Windows: backslashes. Must still count as Alpha's folder.
    alpha["files"] = {k: v.replace("/", "\\") for k, v in alpha["files"].items()}
    (day / "batch.json").write_text(json.dumps({"date": day.name, "built": [
        alpha,
        row("Confidential", AI_ENGINEER, "https://example.com/conf-a", "Confidential")],
        "dropped": []}), encoding="utf-8")
    # A second shard's fragment: another role from another "Confidential".
    (day / "batch.shard-2.json").write_text(json.dumps({"built": [
        row("Confidential", "Data Engineer", "https://example.com/conf-b", "Confidential2")],
        "dropped": []}), encoding="utf-8")
    (b / "shortlist.json").write_text(json.dumps([
        {"company": GAMMA_GMBH, "title": "Applied AI Engineer", "url": "https://example.com/gamma"}]),
        encoding="utf-8")

    def merge() -> dict:
        subprocess.run([str(py), "merge_batches.py", str(day)], cwd=b, capture_output=True,
                       text=True, encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"), timeout=60)
        return json.loads((day / "batch.json").read_text(encoding="utf-8"))

    got = merge()
    names = sorted(e["company"] for e in got["built"])
    check(names == ["Alpha", "Beta", "Confidential", "Confidential", GAMMA_GMBH],
          "every finished application is recorded, an unfinished one is not", str(names))
    gamma = next((e for e in got["built"] if e["company"] == GAMMA_GMBH), {})
    check(gamma.get("url") == "https://example.com/gamma",
          "a recovered application gets its posting link back", str(gamma))
    check(len(merge()["built"]) == 5, "recovering twice adds nothing")
    (b / "shortlist.json").unlink(missing_ok=True)

    subprocess.run([str(py), "batch_report.py", day.name], cwd=b, capture_output=True, timeout=60)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/report/{day.name}/batch.html",
                                    timeout=10) as r:
            html = r.read().decode("utf-8", "replace")
    except OSError as e:
        html = str(e)
    check(f'<base href="/report/{day.name}/">' in html and GAMMA_GMBH in html,
          "an earlier day's batch opens from the Applications page", html[:200])

    # One top bar on every page: the brand and the same three tabs.
    pages = {"dashboard": DASHBOARD_ROUTE, "ranking": RANKING_ROUTE}
    htmls = {"applications": html}
    for name, path in pages.items():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
                htmls[name] = r.read().decode("utf-8", "replace")
        except OSError as e:
            htmls[name] = str(e)
    same = all('class="topbar"' in h and "Job Pipeline" in h and 'aria-current="page"' in h
               and all(t in h for t in (">Dashboard<", ">Applications<", ">Fit ranking<"))
               for h in htmls.values())
    check(same, "Dashboard, Applications and Fit ranking share one top bar",
          ", ".join(k for k, h in htmls.items() if 'class="topbar"' not in h))

    # A recovered role counts as built: the pool must not offer it again.
    left = ps(py, work, """
        import pool
        print(sorted(j["company"] for j in pool.split([
            {"company": "Beta", "title": "ML Engineer", "url": "https://example.com/other",
             "description": "x"}])["applied"]))""")
    check(left == "['Beta']", "a recovered application is never handed to Claude again", left)
    shutil.rmtree(day, ignore_errors=True)


# ------------------------------------------------------------------ Arbeitnow
# A stand-in embedding model: one dimension per topic, so texts about the same
# thing point the same way, the way a real model's vectors do. A real model is
# far too big to download in a test.
AN_TOPICS = [
    {"ai", "llm", "llms", "agent", "agents", "rag", "genai", "prompt", "language"},
    {"machine", "learning", "ml", "pytorch", "models", "model", "mlops", "training", "deploying"},
    {"engineer", "engineering", "developer", "software", "python", "production", "systems", "backend"},
    {"sales", "account", "customers", "quota", "crm", "deals"},
    {"nurse", "patients", "care", "hospital", "clinic"},
    {"chef", "kitchen", "restaurant", "cooking", "food"},
    {"accountant", "accounting", "ledger", "tax", "audit", "finance"},
    {"designer", "figma", "brand", "visual", "ux"},
    {"warehouse", "logistics", "driver", "shipping", "forklift"},
]


def _an_vec(text: str) -> list[float]:
    import math
    import re
    import zlib
    text = re.sub(r"^search_(document|query): ", "", text)
    v = [0.0] * (len(AN_TOPICS) + 6)
    for w in re.findall(r"[a-zäöüß]+", text.lower()):
        for i, t in enumerate(AN_TOPICS):
            if w in t:
                v[i] += 1.0
                break
        else:
            v[len(AN_TOPICS) + zlib.crc32(w.encode()) % 6] += 0.15
    v[-1] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1
    return [x / n for x in v]


AN_PLACES = {"Mainz": ["DE"], "Paris": ["FR"], "Lyon": ["FR"], "Zug": ["CH"],
             "Basel": ["CH"], "Graz": ["AT"], "Konz": ["DE"]}


def arbeitnow_ollama(port: int) -> None:
    """A stand-in Ollama for the Arbeitnow step: embeddings, the questions the
    settings are written from, location answers (one batch deliberately
    broken), and ranker answers for everything else."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, obj, raw: str | None = None):
            body = (raw if raw is not None else json.dumps(obj)).encode()
            self.send_response(200)
            self.send_header("Content-Type", JSON_TYPE)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"models": [{"name": "llama3.1:latest"},
                                   {"name": "nomic-embed-text:latest"}]}
                       if "tags" in self.path else {"version": "selftest"})

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path.endswith("/api/embed"):
                return self._send({"embeddings": [_an_vec(t) for t in req.get("input") or []]})
            p = req.get("prompt") or ""
            ans: dict
            if "job locations from Arbeitnow" in p:
                locs = [json.loads(l) for l in p.split("Locations:", 1)[1].split("\n") if l.strip()]
                if any("BREAK" in l for l in locs):
                    return self._send(None, json.dumps({"response": '{"places": [{"text": "Ly'}))
                ans = {"places": [{"text": l, "countries": AN_PLACES.get(l, [])} for l in locs]}
            elif "You set up job searches" in p:
                ans = {"searches": [
                    {"name": AI_ENGINEER, "looking_for": "Engineer building LLM apps, AI agents "
                                                           "and RAG systems in production with Python"},
                    {"name": "Research Scientist", "looking_for": "Research on new learning methods"},
                    {"name": MACHINE_LEARNING_ENGINEER,
                     "looking_for": "Machine learning engineer training and deploying models "
                                    "with PyTorch, MLOps"}],
                    "student_roles": False}
            elif "Does this profile say" in p:
                ruled = "Research Scientist" in p
                ans = {"ruled_out": ruled, "because": "research roles do not count" if ruled else ""}
            elif "Copy, word for word" in p:
                ans = {"quote": "Based in Berlin; remote elsewhere in the EU is fine."}
            elif "wrote this about where they would work" in p:
                # Atlantis is not in the quote, so its country must be ignored.
                ans = {"places": [{"name": "Berlin", "countries": ["DE"], "office": True},
                                  {"name": "Atlantis", "countries": ["FR"], "office": True},
                                  {"name": "the EU", "countries": [], "office": False}],
                       "remote_abroad": True}
            else:
                ans = {"role_match": "yes", "years_required": 2, "languages_required": [],
                       "is_management": False, "location": "best", "job_kind": "regular",
                       "fit": 80, "reason": "selftest answer"}
            self._send({"response": json.dumps(ans)})

        def log_message(self, *a):
            pass                # quiet: the checks are the output, not every request
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def arbeitnow_feed(port: int) -> dict:
    """
    A stand-in Arbeitnow API, three pages:
      page 1 opens with a posting from 60 days ago (the 28 September case that
             ended the old scraper after one page), then this week's jobs
      page 2 answers 429 the first time
      page 3 is the last page
    Returns a dict the test reads: how often each page was asked for.
    """
    import http.server
    import threading
    now = int(time.time())
    hits: dict = {"pages": [], "429": 0}

    def job(title, desc, loc="Berlin", remote=False, types=(), age_days=0):
        slug = f"{title}-{loc}-{len(hits)}-{abs(hash((title, loc, desc))) % 10**8}"
        return {"slug": slug, "company_name": "Beispiel GmbH", "title": title,
                "description": f"<p>{desc}</p>", "remote": remote,
                "url": f"https://www.arbeitnow.com/jobs/{slug}", "tags": [],
                "job_types": list(types), "location": loc,
                "created_at": now - age_days * 86400 - 60}
    core = [
        job(SOFTWARE_ENGINEER, "We build llm agents and rag pipelines in production with python. "
            "Our agents use prompt engineering and language models."),
        job(MACHINE_LEARNING_ENGINEER, "Train and deploy machine learning models with pytorch, "
            "mlops, model training in production.", "Mainz"),
        job("Werkstudent Machine Learning", "machine learning models pytorch training for students",
            types=["Working student"]),
        job(ML_ENGINEER, "machine learning models pytorch mlops deploying", "Paris"),
        job(AI_ENGINEER, "llm agents rag genai python production", "Paris", remote=True),
    ]
    fill = [("Sales Manager", "sales account customers quota crm deals"),
            ("Account Executive", "sales deals crm customers quota"),
            ("Nurse", "nurse patients care hospital clinic"),
            ("ICU Nurse", "patients hospital care nurse"),
            ("Chef", "chef kitchen restaurant cooking food"),
            ("Cook", "kitchen cooking food restaurant"),
            ("Accountant", "accountant accounting ledger tax audit finance"),
            ("Tax Advisor", "tax audit accounting finance"),
            ("Product Designer", "designer figma brand visual ux"),
            ("Brand Designer", "brand visual designer figma"),
            ("Warehouse Worker", "warehouse logistics forklift shipping"),
            ("Driver", "driver shipping logistics")]
    towns = ["Lyon", "Zug", "Basel", "Graz", "Konz", "BREAK Office", "Remote", ""]
    filler = [job(t, f"{d} team number {k} office", towns[(k * 3 + i) % len(towns)])
              for k in range(4) for i, (t, d) in enumerate(fill)]
    stale = job("AI Engineer (old)", "llm agents rag", age_days=60)
    pages = {1: [stale] + core + filler[:16], 2: filler[16:40], 3: filler[40:]}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            page = int((self.path.split("page=", 1) + ["1"])[1].split("&")[0] or 1)
            hits["pages"].append(page)
            if page == 2 and not hits["429"]:
                hits["429"] += 1
                self.send_response(429)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            rows = pages.get(page, [])
            body = json.dumps({"data": rows, "links": {
                "next": f"/api?page={page + 1}" if page < 3 else None}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", JSON_TYPE)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass                # quiet: the checks are the output, not every request
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return hits


def arbeitnow_section(e: "Env", py: pathlib.Path, work: pathlib.Path) -> None:
    """
    The real arbeitnow.py and bridge, against a fake feed and a fake Ollama:
    settings written from the profile (with a planted wrong search and a
    planted wrong country), paging past a stale first job, a 429, placing
    locations with one broken answer, matching by meaning, and the dashboard
    endpoints.
    """
    sc = work / "scraper"
    oport, fport = free_port(), free_port()
    arbeitnow_ollama(oport)
    hits = arbeitnow_feed(fport)
    _write_local(sc, f'OLLAMA_URL = "http://127.0.0.1:{oport}"\n'
                     f'ARBEITNOW_API = "http://127.0.0.1:{fport}/api"\n'
                     "ARBEITNOW_RATE_WAIT = 1\nARBEITNOW_PAGE_DELAY = 0\n")
    for name in (ARBEITNOW_SETTINGS_JSON, "arbeitnow_feed.json", ARBEITNOW_PLACES_JSON,
                 "arbeitnow_vectors.json", "arbeitnow_vectors.bin"):
        (sc / "out" / name).unlink(missing_ok=True)
    for f in (sc / "inbox").glob(JSON_GLOB):
        f.unlink()
    # Restart the bridge so it reads config_local.py: the supervisor brings it
    # back by itself.
    serve = ps(py, work, f"""
        import platform_util as pu
        print(pu.find_script_procs(__import__('pathlib').Path(r'{sc / 'serve.py'}'))[0])
    """)
    ps(py, work, f"import psutil; psutil.Process({serve}).kill()")
    time.sleep(1)
    check(e.wait_up(60), "bridge back up with the Arbeitnow test settings")

    def run() -> tuple[int, str]:
        r = subprocess.run([str(py), "arbeitnow.py"], cwd=sc, env=dict(e.env),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
        return r.returncode, r.stdout + r.stderr

    code, log = run()
    check(code == 0, "arbeitnow.py runs", log[-800:])
    s = json.loads((sc / "out" / ARBEITNOW_SETTINGS_JSON).read_text(encoding="utf-8")) \
        if (sc / "out" / ARBEITNOW_SETTINGS_JSON).exists() else {}
    check(s.get("made_by") == "model"
          and [x["name"] for x in s.get("searches", [])] == [AI_ENGINEER, MACHINE_LEARNING_ENGINEER],
          "searches written from the profile; the one the profile rules out is left out",
          json.dumps(s)[:400])
    check(s.get("countries") == ["DE"] and s.get("remote_elsewhere") is True
          and s.get("student_roles") is False,
          "the location and student-job rules follow the preferences Claude wrote",
          json.dumps({k: s.get(k) for k in ("countries", "remote_elsewhere", "student_roles")}))
    check(3 in hits["pages"] and hits["429"] == 1,
          "reads past a stale first job and waits out a 429", str(hits))
    places = json.loads((sc / "out" / ARBEITNOW_PLACES_JSON).read_text(encoding="utf-8")) \
        if (sc / "out" / ARBEITNOW_PLACES_JSON).exists() else {}
    check(places.get("Mainz") == ["DE"] and places.get("Zug") == ["CH"]
          and places.get("BREAK Office") == [] and "could not place" in log,
          "locations placed; a broken answer costs only its own half", json.dumps(places)[:300])
    inbox = next(iter(sorted((sc / "inbox").glob(JSON_GLOB))), None)
    jobs = json.loads(inbox.read_text(encoding="utf-8")).get("jobs", []) if inbox else []
    got = {(j["title"], j["location"]) for j in jobs}
    want = {(SOFTWARE_ENGINEER, "Berlin, Germany"), (MACHINE_LEARNING_ENGINEER, "Mainz, Germany"),
            (AI_ENGINEER, "Paris, France (Remote)")}
    check(got == want, "kept by meaning: a plain 'Software Engineer' with LLM work, not the "
          "working-student or on-site-abroad jobs", str(sorted(got)))
    q = {j["title"]: j.get("query") for j in jobs}
    check(q.get(SOFTWARE_ENGINEER) == "Arbeitnow: AI Engineer"
          and q.get(MACHINE_LEARNING_ENGINEER) == "Arbeitnow: Machine Learning Engineer",
          "each job is credited to the search it matched", str(q))

    before = list(hits["pages"])
    code, log2 = run()
    again = hits["pages"][len(before):]
    check(code == 0 and "nothing new on two pages" in log2 and "reading" not in log2
          and len(again) == 2,
          "a second pull stops once nothing is new and reads no job twice",
          f"pages {again}; {log2[-400:]}")

    # The dashboard's side.
    st = e.get(ARBEITNOW_SETTINGS_ROUTE)[1]
    check(st.get("ok") and st["settings"]["searches"][0]["name"] == AI_ENGINEER
          and not st.get("suggesting"), "GET /arbeitnow/settings", json.dumps(st)[:300])
    pv = e.post("/arbeitnow/preview", {"settings": s})
    p = pv.get("preview") or {}
    check(p.get("kept") == 3 and p["levels"]["wide"] >= p["levels"]["balanced"] >= p["levels"]["close"],
          "preview matches the pull", json.dumps(pv)[:400])
    anywhere = e.post("/arbeitnow/preview", {"settings": {**s, "countries": [], "student_roles": True}})
    check((anywhere.get("preview") or {}).get("kept") == 5,
          "preview follows the rules: anywhere, with working-student jobs", json.dumps(anywhere)[:300])
    saved = e.post(ARBEITNOW_SETTINGS_ROUTE, {"settings": {
        "searches": [{"name": "  Nurse ", "looking_for": "ICU nurse in a hospital"}, {"name": ""}],
        "countries": ["Deutschland", "Narnia", "ch"], "strictness": "extreme", "days": 5}})
    ss = saved.get("settings") or {}
    check(ss.get("made_by") == "you" and [x["name"] for x in ss.get("searches", [])] == ["Nurse"]
          and ss.get("countries") == ["DE", "CH"] and ss.get("strictness") == "balanced"
          and ss.get("days") == 7, "settings from the page are cleaned before saving", json.dumps(ss))
    active = ps(py, work, "from profile_dir import profile_file; print(profile_file())")
    pathlib.Path(active).write_text("# Changed\nA different profile.\n", encoding="utf-8")
    st = e.get(ARBEITNOW_SETTINGS_ROUTE)[1]
    check(not st.get("suggesting") and st.get("profile_changed")
          and st["settings"]["searches"][0]["name"] == "Nurse",
          "settings someone saved are never rewritten; a changed profile is only pointed out",
          json.dumps(st)[:300])
    dash = e.get(DASHBOARD_ROUTE)[1]
    dash = dash.decode("utf-8", "replace") if isinstance(dash, bytes) else str(dash)
    check('id="anDlg"' in dash and e.get("/web/arbeitnow.js")[0] == 200,
          "the dashboard has the Arbeitnow dialog")
    # After an erase the step lines start empty, and each step's last result
    # is kept separately as "before the erase", so the page never shows a
    # three-day-old result as the current one over empty cards.
    e.post("/reset", {})
    pg = e.get(PROGRESS_ROUTE)[1]
    check(pg.get("erased_at") and "build" not in pg.get("steps", {})
          and (pg.get("before_erase") or {}).get("build", {}).get("status") == "ok",
          "after an erase, results from before it are shown as such, not as current",
          json.dumps({k: pg.get(k) for k in ("erased_at", "steps", "before_erase")})[:400])
    e.post(ARBEITNOW_SETTINGS_ROUTE, {"settings": {"searches": []}})
    code, log3 = run()
    check(code == 1 and "no Arbeitnow search" in log3, "no search set: a clear failure",
          log3[-300:])
    _write_local(sc, None)


def origin_section(e: "Env") -> None:
    """
    Only the bridge's own pages and the extension may change anything from a
    browser. POST /applied with no URLs answers 400 when let in and 403 when
    refused, and changes nothing either way.
    """
    def ask(method: str, path: str, headers: dict, data: bytes | None = b"{}"):
        req = urllib.request.Request(f"http://127.0.0.1:{e.port}{path}", method=method,
                                     data=data if method == "POST" else None,
                                     headers={"Content-Type": JSON_TYPE, **headers})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers.get("Access-Control-Allow-Origin")
        except urllib.error.HTTPError as ex:
            return ex.code, ex.headers.get("Access-Control-Allow-Origin")

    code, acao = ask("POST", APPLIED_ROUTE, {"Origin": HTTPS_EVIL_EXAMPLE})
    check(code == 403 and acao is None, "a POST from another website is refused", f"{code} {acao}")
    own = f"http://127.0.0.1:{e.port}"
    for origin in (own, f"http://localhost:{e.port}", "chrome-extension://abc"):
        code, acao = ask("POST", APPLIED_ROUTE, {"Origin": origin})
        check(code == 400 and acao == origin, f"a POST from {origin} is accepted", f"{code} {acao}")
    code, _ = ask("POST", APPLIED_ROUTE, {})
    check(code == 400, "a POST with no Origin (curl, scripts) is accepted", str(code))
    code, _ = ask("POST", APPLIED_ROUTE, {"Origin": "null"})
    check(code == 403, "a POST from Origin null (sandboxed frame, file://) is refused", str(code))
    code, _ = ask("POST", APPLIED_ROUTE, {"Origin": own, "Content-Type": "text/plain"})
    check(code == 403, "a POST that is not JSON is refused (it would skip the CORS preflight)", str(code))
    code, _ = ask("GET", TRIGGER_ROUTE, {"Origin": HTTPS_EVIL_EXAMPLE})
    check(code == 403, "GET /trigger (hands out a queued run) from another website is refused", str(code))
    code, _ = ask("GET", TRIGGER_ROUTE, {"Sec-Fetch-Site": "cross-site"})
    check(code == 403, "GET /trigger from another site's <img> (no Origin) is refused", str(code))
    code, acao = ask("GET", STATUS_ROUTE, {"Origin": HTTPS_EVIL_EXAMPLE})
    check(code == 200 and acao is None, "another website gets no CORS header to read answers with",
          f"{code} {acao}")
    code, acao = ask("OPTIONS", APPLIED_ROUTE, {"Origin": HTTPS_EVIL_EXAMPLE}, None)
    check(acao is None, "a preflight from another website is not approved", f"{code} {acao}")


def searches_section(e: "Env", py: pathlib.Path, work: pathlib.Path) -> None:
    """
    Saved searches kept by the pipeline and edited on the dashboard (28
    September 2026): the endpoints, the old list format, erasing, and the
    extension's side of it through scripts/extension_test.mjs when Node is here.
    """
    sc = work / "scraper"
    st = e.get(SEARCHES_LIST_ROUTE)[1]
    # A new install starts with one example per site (any_person_section
    # checks that); by now the bridge has written this person's preferences,
    # so the untouched examples have become searches for their own roles.
    check(st.get("ok") and [q["label"] for q in st.get("searches", [])]
          == ["Machine Learning Engineer in Berlin", "Ärztliche Beratung in Berlin"]
          and all("linkedin.com" in q["url"] for q in st["searches"]),
          "the first-install examples became searches for the person's own roles and place",
          json.dumps(st)[:300])
    examples = json.loads(ps(py, work, """
        import json, saved_searches as ss
        print(json.dumps(ss.EXAMPLES))
    """))
    e.post("/searches/save", {"searches": examples})
    st = e.get(SEARCHES_LIST_ROUTE)[1]
    check(not st["extension"]["connected"], "no extension has checked in yet",
          json.dumps(st.get("extension")))

    r = e.post(SEARCHES_ADD_ROUTE, {"url": "https://www.linkedin.com/jobs/search/?keywords=Data%20Engineer"
                                        "&location=Berlin&currentJobId=42&trk=x"})
    saved = e.get(SEARCHES_LIST_ROUTE)[1]["searches"]
    check(r.get("outcome") == "added" and "currentJobId" not in saved[-1]["url"]
          and saved[-1]["label"] == "LinkedIn: Data Engineer",
          "a search is added, cleaned and named", json.dumps(r) + json.dumps(saved[-1]))
    r = e.post(SEARCHES_ADD_ROUTE, {"url": "https://www.linkedin.com/jobs/search/?keywords=Data+Engineer&location=Berlin"})
    check(r.get("outcome") == "duplicate", "the same search twice is refused", json.dumps(r))
    r = e.post(SEARCHES_ADD_ROUTE, {"url": "https://www.xing.com/jobs/search?keywords=x"})
    check(r.get("ok") is False and "not LinkedIn" in r.get("why", ""),
          "a site the extension cannot read is refused", json.dumps(r))

    r = e.post("/searches/save", {"searches": [{"label": "bad", "url": "http://www.linkedin.com/jobs"}]})
    check(r.get("ok") is False and len(e.get(SEARCHES_LIST_ROUTE)[1]["searches"]) == 4,
          "an unusable list is refused whole, nothing is lost", json.dumps(r))
    keep = [{**q, "enabled": i != 0} for i, q in enumerate(saved)]
    r = e.post("/searches/save", {"searches": keep, "pages": 2})
    active = e.get(SEARCHES_ROUTE)[1]["searches"]
    check(r.get("ok") and r.get("pages") == 2 and len(active) == 3
          and all(q["url"] != saved[0]["url"] for q in active),
          "edits save; a switched-off search is kept but not run", json.dumps(r)[:300])
    r = e.post(SEARCHES_ROUTE, {"searches": [], "all": [{"label": "old", "url": "https://www.stepstone.de/jobs?what=old"}]})
    check(r.get("ignored") and len(e.get(SEARCHES_LIST_ROUTE)[1]["searches"]) == 4,
          "an old extension's mirror cannot overwrite the list", json.dumps(r))
    r = e.post("/searches/merge", {"searches": [{"label": "Mine", "url": "https://www.stepstone.de/jobs?what=Mine"},
                                                {"label": "dup", "url": saved[1]["url"]}]})
    check(r.get("added") == 1, "a list the extension kept itself is merged in once, without duplicates",
          json.dumps(r))

    # The old format: a bare list, as the extension used to mirror it.
    out = ps(py, work, r"""
        import json, pathlib, tempfile, saved_searches as ss
        ss.FILE = pathlib.Path(tempfile.mkdtemp()) / "searches.json"
        ss.FILE.write_text(json.dumps([{"label": "Old", "url": "https://de.indeed.com/jobs?q=x"},
                                       {"label": "Off", "url": "https://www.stepstone.de/jobs?what=y", "enabled": False}]))
        st = ss.load(); disk = json.loads(ss.FILE.read_text())
        print(disk["version"], len(st["searches"]), len(ss.active(st)), st["searches"][1]["enabled"])
    """)
    check(out == "2 2 1 False", "an old list file is read and rewritten in the new shape", out)

    ext = e.get("/searches/sync")[1]["extension"]
    check(ext.get("connected"), "the extension's check-in shows as connected", json.dumps(ext))

    r = e.post(TRIGGER_ROUTE, {})
    check(r.get("ok") and r.get("searches") == 4, "Run scrape queues the searches that are on", json.dumps(r))
    e.get(TRIGGER_ROUTE)

    # A run cut off after two searches continues with the other two, even for
    # an extension that forgot its place (29 September 2026).
    on = [q["url"] for q in e.get(SEARCHES_ROUTE)[1]["searches"]]
    e.post(SCRAPE_ROUTE, {"running": True, "done": 0, "total": 4})
    for u in on[:2]:
        e.post("/search-result", {"label": "x", "url": u, "host": "linkedin.com", "ok": True, "loaded": 25})
    e.post(SCRAPE_ROUTE, {"running": True, "done": 2, "total": 4})
    e.post("/scrape/stop", {})
    e.post(SCRAPE_ROUTE, {"running": False, "done": 2, "total": 4})
    r = e.post(TRIGGER_ROUTE, {})
    skip = e.get(TRIGGER_ROUTE)[1].get("skip")
    e.post(SCRAPE_ROUTE, {"running": True, "done": 0, "total": 2})
    p = e.get(PROGRESS_ROUTE)[1]
    check(r.get("continuing") == 2 and sorted(skip or []) == sorted(on[:2])
          and p["scrape"]["done"] == 2 and p["scrape"]["total"] == 4 and p["searches_done"] == 2,
          "Run scrape continues a cut-off run from the pipeline's record, keeping its finished rows",
          json.dumps({"r": r, "skip": skip, "scrape": p["scrape"], "rows": p["searches_done"]}))
    e.post(SCRAPE_ROUTE, {"running": False, "done": 2, "total": 2})
    r = e.post(TRIGGER_ROUTE, {})
    e.get(TRIGGER_ROUTE)
    check(r.get("continuing") == 0, "once every search is done, the next Run scrape starts fresh", json.dumps(r))

    seen = e.get(SEARCHES_LIST_ROUTE)[1]["reset_at"]
    e.post("/reset", {"hard": False})
    r = e.post("/ingest", {"jobs": [{"url": "https://www.linkedin.com/jobs/view/9", "title": "Old", "company": "C"}],
                           "reset_seen": seen if seen is not None else 0})
    check(r.get("stale") and r.get("added") == 0, "an erased pool resent by the extension is refused", json.dumps(r))

    html = e.get(DASHBOARD_ROUTE)[1]
    html = html.decode("utf-8", "replace") if isinstance(html, bytes) else str(html)
    check('id="srDlg"' in html and 'id="btnSearches"' in html and 'id="btnEraseAll"' in html
          and e.get("/web/searches.js")[0] == 200,
          "the dashboard has the searches window and Start over")

    # Both sides fingerprint the same code files, or the dashboard would call a
    # current extension stale (serve.EXT_CODE, background.js CODE_FILES).
    bg = (work / "extension" / "background.js").read_text(encoding="utf-8")
    listed = re.search(r"const CODE_FILES = \[([^\]]*)\]", bg)
    in_js = re.findall(r'"([^"]+)"', listed.group(1)) if listed else []
    in_py = ps(py, work, "import serve; print(','.join(serve.EXT_CODE))").split(",")
    code_files = sorted(f.name for f in (work / "extension").iterdir()
                        if f.is_file() and f.suffix in (".js", ".json", ".html"))
    check(in_js == in_py and sorted(in_js) == code_files,
          "the extension and the pipeline fingerprint the same code files", f"{in_js} {in_py} {code_files}")

    node = shutil.which("node")
    if node:
        r = subprocess.run([node, str(work / "scripts" / "extension_test.mjs"), str(e.port),
                            str(work / "extension" / "background.js")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        check(r.returncode == 0, "the extension pulls searches, moves its own list, clears after an erase, "
                                 "adds a page and continues a cut-off run", (r.stdout + r.stderr)[-1500:])
        # The dashboard's search builder, loaded in Node (25 October 2026): a
        # search in another country must carry that country's geoId, or say
        # plainly that LinkedIn will search the profile's country instead.
        js = textwrap.dedent(r'''
            const u = require(require("path").resolve(process.argv[1]));
            const map = u.geoMapOf({"netherlands": "123456789"});
            const f = {kw: "Data Engineer", radius: 0, posted: 7, remote: false};
            const li = (loc) => u.BUILD.linkedin({...f, loc}, u.geoFor(loc, map));
            const geo = (url) => new URL(url).searchParams.get("geoId");
            const out = {
              germany: geo(li("Germany")), deutschland: geo(li("Deutschland")),
              egypt: geo(li("Egypt")), saudi: geo(li("Saudi Arabia")),
              learned: geo(li("The Netherlands")),
              unknownWarns: /profile/.test(u.placeIssue(li("France"), map).warn) && !geo(li("France")),
              fixed: geo(u.placeIssue("https://www.linkedin.com/jobs/search/?keywords=x&location=Egypt", map).fix),
              mismatch: geo(u.placeIssue("https://www.linkedin.com/jobs/search/?keywords=x&location=Egypt&geoId=101282230", map).fix),
              fine: u.placeIssue(li("Germany"), map).warn === "",
              indeed: [u.indeedCountry("Netherlands"), u.indeedCountry("Paris, France"), u.indeedCountry("UK"), u.indeedCountry("Cairo")],
              indeedWrong: !!u.placeIssue("https://de.indeed.com/jobs?q=x&l=Netherlands", map).warn,
              stepstone: !!u.placeIssue("https://www.stepstone.de/jobs?what=x&where=Spain", map).warn
                         && !u.placeIssue("https://www.stepstone.de/jobs?what=x&where=Berlin", map).warn,
            };
            console.log(JSON.stringify(out));
        ''')
        r = subprocess.run([node, "-e", js, str(work / "web" / "searches.js")], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        try:
            o = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            o = {}
        check(o.get("germany") == o.get("deutschland") == "101282230" and o.get("egypt") == "106155005"
              and o.get("saudi") == "100459316" and o.get("learned") == "123456789",
              "the search builder pins known and learned countries by geoId, however they are spelled",
              (r.stdout + r.stderr)[-600:])
        check(o.get("unknownWarns") and o.get("fixed") == "106155005" and o.get("mismatch") == "106155005"
              and o.get("fine"),
              "an unknown place is warned about, a missing or wrong geoId is filled in", json.dumps(o))
        check(o.get("indeed") == ["nl", "fr", "uk", ""] and o.get("indeedWrong") and o.get("stepstone"),
              "Indeed follows the typed country; Indeed and StepStone warn when it is another country's",
              json.dumps(o))
    else:
        print("  (Node is not installed, so the extension's own checks are skipped)")

    # Restart now: start.py runs this bridge, as ./start.sh does, so the bridge
    # exits and comes straight back as a new process on the code on disk.
    who = lambda: ps(py, work, f"""
        import platform_util as pu, pathlib
        print(pu.find_script_procs(pathlib.Path(r'{sc / 'serve.py'}')))""")
    before, p = who(), e.get(PROGRESS_ROUTE)[1]
    r = e.post("/restart", {})
    back = False
    for _ in range(60):
        time.sleep(0.5)
        # Back means a new process answering and none of the old ones left:
        # the old one answers for half a second after it says yes.
        now = who()
        try:
            back = (now not in ("", "[]") and not set(json.loads(now)) & set(json.loads(before))
                    and e.get(STATUS_ROUTE, timeout=2)[0] == 200)
        except (OSError, ValueError):
            back = False
        if back:
            break
    check(p.get("can_restart") and r.get("ok") and back,
          "Restart now brings the bridge back as a new process", json.dumps({"r": r, "before": before}))


def build_stage_check(py: pathlib.Path, work: pathlib.Path) -> None:
    """
    Which step a running build is on: from run_batch's logs/.stage, else from
    the whole current attempt of the log, never from its last 16 lines.
    """
    out = ps(py, work, r"""
        import json, os, subprocess, sys, serve
        logs = serve.BUILDER / "logs"
        log = logs / f"{serve._build_day()}.log"
        (logs / ".stage").unlink(missing_ok=True)
        noise = "\n".join(f"Claude wrote line {i} of its notes" for i in range(60))
        log.write_text("[10:00] === batch attempt 1/3 ===\n[10:00] ranking 40 jobs with Claude\n"
                       "  12/40 judged\n[10:01] 9 roles passed and are going to Claude\n" + noise + "\n",
                       encoding="utf-8")
        a = serve._build_stage()
        log.write_text(log.read_text(encoding="utf-8") + "[11:00] === batch attempt 2/3 ===\n"
                       "[11:00] ranking 40 jobs with Claude\n", encoding="utf-8")
        b = serve._build_stage()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        (logs / ".stage").write_text(json.dumps({"stage": "writing", "pid": child.pid}), encoding="utf-8")
        c = serve._build_stage()
        child.kill(); child.wait()
        d = serve._build_stage()           # the writer is gone: the log decides
        (logs / ".stage").unlink(missing_ok=True); log.unlink(missing_ok=True)
        e = serve._judged_pairs("  12/40  · 80 24/7 Support Engineer\n  13/40  ✗ 10 Ops (24/7)")
        print(json.dumps([a, b, c, d, e]))
    """)
    try:
        a, b, c, d, e = json.loads(out.splitlines()[-1])
    except (ValueError, IndexError):
        a = b = c = d = e = out
    check(a == "writing", "a build stays on writing however much Claude has logged since the hand-off", str(a))
    check(b == "ranking", "a new attempt that ranks again shows ranking", str(b))
    check(c == "writing" and d == "ranking", "run_batch's stage file decides while its build is alive",
          f"{c} {d}")
    check(e == [["12", "40"], ["13", "40"]], "a 24/7 in a job title is not read as progress", str(e))
    step_row_check(work)
    global_names_check(work)


def step_row_check(work: pathlib.Path) -> None:
    """
    The dashboard's four steps through a whole run, as /progress reports it,
    through the real web/steps.js in Node. 3 November 2026: pressing Build put
    the running line back on Rank while the build ranked and checked postings,
    and Done stayed ticked from the last build during a new one.
    """
    node = shutil.which("node")
    if not node:
        print("  (Node is not installed, so the step row check is skipped)")
        return
    last = {"rank_run": {"result": {"buildable": 9}}, "build": {"finished": "2026-11-02T10:00:00"},
            "build_progress": {"built": 4, "target": 4}, "searches_total": 3, "searches_done": 3}
    run = [  # (what is happening, the reading, which step must be running)
        ("idle, nothing yet", {"phase": "idle"}, None),
        ("scraping", {"phase": "scraping", "searches_total": 3, "searches_done": 1}, "scrape"),
        ("ranking on its own", {"phase": "ranking", "searches_total": 3, "searches_done": 3}, "rank"),
        ("ranked, idle", {"phase": "idle", "searches_total": 3, "searches_done": 3,
                          "rank_run": {"result": {"buildable": 9}}}, None),
        ("Build pressed, starting", {"phase": "building", "build_stage": "starting",
                                     "rank_run": {"result": {"buildable": 9}}}, "build"),
        ("the build ranks first", {"phase": "ranking", "build_stage": "ranking",
                                   "rank_run": {"result": {"buildable": 9}}}, "build"),
        ("the build writes", {"phase": "building", "build_stage": "writing",
                              "rank_run": {"result": {"buildable": 9}}}, "build"),
        ("built", {"phase": "done", **last}, None),
        ("a new build, after a finished one", {"phase": "building", "build_stage": "ranking", **last}, "build"),
        ("a build waiting on a scrape", {"phase": "scraping", "build_stage": "starting"}, "scrape"),
        ("a new scrape after a finished build", {"phase": "scraping", **last,
                                                 "searches_done": 1}, "scrape"),
    ]
    js = ("const {stepStates,currentStep}=require(process.argv[1]);"
          "const run=JSON.parse(process.argv[2]);"
          "console.log(JSON.stringify(run.map(([,d])=>stepStates(d))));"
          "console.log(JSON.stringify(run.map(([,d])=>currentStep(stepStates(d)))));")
    r = subprocess.run([node, "-e", js, str(work / "web" / "steps.js"), json.dumps(run)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    try:
        got = json.loads(r.stdout.strip().splitlines()[-2])
        here = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        got, here = [], []
    wrong = [f"{label}: {st}" for (label, _, want), st in zip(run, got) if not _steps_right(st, want)]
    done = dict(zip([x[0] for x in run], got))
    if done.get("built", {}).get("done") != "done":
        wrong.append(f"built: Done is not ticked {done.get('built')}")
    if done.get("the build writes", {}).get("rank") != "done":
        wrong.append(f"writing: Rank is not ticked {done.get('the build writes')}")
    check(len(got) == len(run) and not wrong,
          "the step row follows the run: one running step, never back to Rank during a build, "
          "nothing ticked after it", "; ".join(wrong)[:600] or (r.stdout + r.stderr)[-300:])
    # Where the person is: the running step says Now, a finished step's
    # successor says Next, a fresh pipeline says Start here.
    marks = dict(zip([x[0] for x in run], [tuple(h) for h in here]))
    want_marks = {"idle, nothing yet": ("scrape", "Start here"), "scraping": ("scrape", "Now"),
                  "ranked, idle": ("build", "Next"), "the build writes": ("build", "Now"),
                  "built": ("done", "All done")}
    off = {k: marks.get(k) for k, v in want_marks.items() if marks.get(k) != v}
    check(not off, "the stepper marks where the person is: Start here, Now, Next, All done",
          json.dumps(off) or (r.stdout + r.stderr)[-300:])


def _steps_right(st: dict, want: str | None) -> bool:
    """Exactly `want` is running (or nothing is), and every step after it is pending."""
    order = ["scrape", "rank", "build", "done"]
    active = [k for k in order if st.get(k) == "active"]
    later = order[order.index(want) + 1:] if want else []
    return active == ([want] if want else []) and all(st.get(k) == "pending" for k in later)


def global_names_check(work: pathlib.Path) -> None:
    """
    The dashboard's scripts share one global scope, so two of them declaring
    the same top-level name is a SyntaxError that kills the second one, and
    ESLint, which reads one file at a time, cannot see it.
    """
    page = (work / "web" / "dashboard.html").read_text(encoding="utf-8")
    seen: dict[str, str] = {}
    clash = []
    for src in re.findall(r'<script src="/web/([\w.-]+\.js)"', page):
        text = (work / "web" / src).read_text(encoding="utf-8")
        # Code inside a file's own (function(){ ... })() is private to it.
        text = re.sub(r"^\(function ?\(\) ?\{.*^\}\)\(\);", "", text, flags=re.M | re.S)
        for name in re.findall(r"^(?:let|const|var|function|class)\s+([A-Za-z_$][\w$]*)", text, re.M):
            if name in seen and seen[name] != src:
                clash.append(f"{name} in {seen[name]} and {src}")
            seen.setdefault(name, src)
    check(len(seen) > 5 and not clash, "no two dashboard scripts declare the same top-level name",
          "; ".join(clash) or f"{len(seen)} names")


def topbar_check(e: "Env", when: str) -> None:
    """
    The three pages share one top bar: the nav in the middle with the same
    three links, page status on the left, the theme button last on the right,
    and the shared theme script. Read from the pages as the bridge serves them.
    """
    bars = {}
    for path in (DASHBOARD_ROUTE, "/report/", RANKING_ROUTE):
        html = e.get(path, 30)[1]
        html = html.decode("utf-8", "replace") if isinstance(html, bytes) else str(html)
        # From <body on: the report inlines topbar.css, whose comment shows the markup.
        m = re.search(r'<header class="topbar">(.*?)</header>', html[html.find("<body"):], re.S)
        bar = m.group(1) if m else ""
        order = [bar.find(k) for k in ('class="tb-left"', 'class="nav"', 'class="tb-right"')]
        links = re.findall(r'class="ico (i-\w+)" aria-hidden="true"></span>([^<]+)</a>', bar)
        bars[path] = (all(i >= 0 for i in order) and order == sorted(order),
                      links, re.search(r'id="themeBtn"[^>]*></button>\s*</div>\s*$', bar) is not None,
                      'src="/web/theme.js"' in html or "const THEMES" in html)
    first = bars[DASHBOARD_ROUTE][1]
    check(all(b[0] for b in bars.values()) and len(first) == 3 and all(b[1] == first for b in bars.values()),
          f"all three pages have the same top bar, nav in the middle ({when})", json.dumps(bars)[:400])
    check(all(b[2] and b[3] for b in bars.values()),
          f"every page has the theme button, last on the right, and the theme script ({when})",
          json.dumps({k: v[2:] for k, v in bars.items()}))


def profile_chat_section(e: "Env", py: pathlib.Path, work: pathlib.Path) -> None:
    """
    Edit profile: what you type goes to Claude (the fake), which edits the
    profile; the window shows the change and Undo puts it back.
    """
    me = work / "profiles" / "me"
    calls = e.fakebin / "profile-calls.jsonl"
    calls.unlink(missing_ok=True)
    e.mode("ok")
    # The Arbeitnow section replaces profile.md; start from a full one.
    (me / PROFILE_MD).write_text((work / "profiles" / "example" / PROFILE_MD).read_text(encoding="utf-8")
                                   + "\n<!-- selftest -->\n", encoding="utf-8")

    def settle(seconds: float = 30) -> dict:
        end = time.time() + seconds
        d = e.get(PROFILE_CHAT_ROUTE)[1]
        while d.get("running") and time.time() < end:
            time.sleep(0.3)
            d = e.get(PROFILE_CHAT_ROUTE)[1]
        return d

    code, d = e.get(PROFILE_CHAT_ROUTE)
    check(code == 200 and d.get("folder") == "profiles/me" and not d.get("blocked")
          and PROFILE_MD in d.get("files", []),
          "Edit profile opens on profiles/me", json.dumps(d)[:300])
    status, page = e.get(DASHBOARD_ROUTE)
    js_code = e.get("/web/profile-chat.js")[0]
    check(b'id="btnProfile"' in page and js_code == 200,
          "the dashboard has the Edit profile button and its script", f"{status} {js_code}")
    check(b'id="checkUpdate"' in page and b'id="verInfo"' in page,
          "the dashboard has Check for updates, next to the version", str(status))

    projects = me / "reference" / "PROJECTS.md"
    original = projects.read_text(encoding="utf-8")
    r = e.post(PROFILE_CHAT_ROUTE, {"message": "Add a project: Selftest Tracker"})
    d = settle()
    last = (d.get("messages") or [{}])[-1]
    ch = last.get("changes") or []
    check(r.get("ok") and last.get("role") == "claude" and SELFTEST_TRACKER in last.get("text", "")
          and SELFTEST_TRACKER in projects.read_text(encoding="utf-8"),
          "a message is sent to Claude and its edit lands in the profile", json.dumps(d)[:400])
    check([c["file"] for c in ch] == ["reference/PROJECTS.md"] and ch[0]["plus"] == 1
          and "+| **Selftest Tracker**" in ch[0]["diff"],
          "the reply lists the file it changed, with the diff", json.dumps(ch)[:400])

    e.post(PROFILE_CHAT_ROUTE, {"message": "Add a skill: Rust"})
    d = settle()
    profile_md = (me / PROFILE_MD).read_text(encoding="utf-8")
    seen = [json.loads(ln) for ln in calls.read_text(encoding="utf-8").splitlines()] if calls.exists() else []
    first = seen[0]["args"] if seen else []
    second = seen[1]["args"] if len(seen) > 1 else []
    sid = first[first.index("--session-id") + 1] if "--session-id" in first else None
    check(len(seen) == 2 and sid and second[second.index("--resume") + 1] == sid
          and "Strong: Rust, " in profile_md,
          "a second message continues the same conversation (--resume)", json.dumps(seen)[:500])
    check(all(c["args"][c["args"].index("--tools") + 1] == "Read,Edit,Write,Glob,Grep"
              and c["args"][c["args"].index("--permission-mode") + 1] == "acceptEdits"
              and "claude-sonnet" in c["args"][c["args"].index("--model") + 1]
              and pathlib.Path(c["cwd"]).resolve() == me.resolve()
              # cmd.exe (claude.cmd on Windows) cuts an argument at a line break.
              and "\n" not in c["args"][c["args"].index("--append-system-prompt") + 1]
              and c["msg"] in ("Add a project: Selftest Tracker", "Add a skill: Rust") for c in seen),
          "Claude runs on Sonnet in the profile folder, with file tools only, no shell, a one-line prompt",
          json.dumps([c["args"] for c in seen])[:500])

    r = e.post(PROFILE_CHAT_UNDO_ROUTE, {})
    check(r.get("ok") and "Strong: Rust" not in (me / PROFILE_MD).read_text(encoding="utf-8")
          and SELFTEST_TRACKER in projects.read_text(encoding="utf-8"),
          "Undo puts back only the last change", json.dumps(r)[:300])
    # A file changed by hand after Claude's edit is not overwritten by Undo.
    projects.write_text(projects.read_text(encoding="utf-8") + "edited by hand\n", encoding="utf-8")
    r = e.post(PROFILE_CHAT_UNDO_ROUTE, {})
    check(not r.get("ok") and "reference/PROJECTS.md" in r.get("why", "")
          and "edited by hand" in projects.read_text(encoding="utf-8"),
          "Undo refuses to overwrite a file edited by hand since", json.dumps(r)[:300])
    projects.write_text(original + "| **Selftest Tracker** | selftest Ü | any |\n", encoding="utf-8")
    r = e.post(PROFILE_CHAT_UNDO_ROUTE, {})
    check(r.get("ok") and projects.read_text(encoding="utf-8") == original,
          "Undo again restores the file exactly", json.dumps(r)[:300])
    r = e.post(PROFILE_CHAT_UNDO_ROUTE, {})
    check(not r.get("ok"), "Undo with nothing left says so", json.dumps(r)[:200])

    e.post(PROFILE_CHAT_ROUTE, {"message": "Add my new job"})
    d = settle()
    last = (d.get("messages") or [{}])[-1]
    check(last.get("role") == "claude" and not last.get("changes") and not d.get("can_undo"),
          "a question back from Claude changes nothing", json.dumps(last)[:300])

    e.mode("auth")
    e.post(PROFILE_CHAT_ROUTE, {"message": "Add a skill: Go"})
    d = settle()
    e.mode("ok")
    last = (d.get("messages") or [{}])[-1]
    check(last.get("role") == "error" and "signed out" in last.get("text", "")
          and "Go," not in (me / PROFILE_MD).read_text(encoding="utf-8"),
          "a signed-out Claude is reported, with the fix", json.dumps(last)[:300])

    r = e.post("/profile/chat/new", {})
    d = e.get(PROFILE_CHAT_ROUTE)[1]
    check(r.get("ok") and d.get("messages") == [], "New chat starts an empty conversation", json.dumps(d)[:200])

    req = urllib.request.Request(f"http://127.0.0.1:{e.port}/profile/chat", method="POST",
                                 data=b'{"message":"Add a skill: Evil"}',
                                 headers={"Content-Type": JSON_TYPE, "Origin": HTTPS_EVIL_EXAMPLE})
    try:
        code = urllib.request.urlopen(req, timeout=10).status
    except urllib.error.HTTPError as ex:
        code = ex.code
    check(code == 403 and "Evil" not in (me / PROFILE_MD).read_text(encoding="utf-8"),
          "another website cannot send Claude a message", str(code))
    blocked = ps(py, work, "import profile_chat as pc; print(bool(pc._editable(pc.REPO / 'profiles' / 'example')))")
    check(blocked == "True", "the fictional example profile is never edited", blocked)


PREFERENCES_ROUTE = "/preferences"
PREFS_JSON = "preferences.json"


def verdict_section(py: pathlib.Path, work: pathlib.Path) -> None:
    """
    The ranker's verdict reads the person's preferences, not one engineer's
    constants. User journeys: a senior salesperson, a native German speaker, a
    student who only wants Werkstudent jobs, an engineer who wants regular jobs,
    and someone with no preferences at all. Every drop is one of the person's
    own limits; nothing is decided by a title. Pure functions, no model.
    """
    out = ps(py, work, r'''
        import json, rank_ollama_real as R
        sales = {"target_roles": ["Account Executive", "Head of Sales"], "not_my_field": ["retail sales"],
                 "years_experience": 8, "max_years_asked": None,
                 "languages": [{"language": "English", "level": "native"}],
                 "places": [{"place": "Berlin", "country": "DE", "work": ["onsite", "hybrid", "remote"]}],
                 "remote_anywhere": False, "accept_management": True, "job_kinds": []}
        german = {**sales, "languages": [{"language": "German", "level": "native"},
                                         {"language": "English", "level": "c1"}],
                  "max_years_asked": 4, "accept_management": False}
        student = {"target_roles": ["Werkstudent Marketing"], "not_my_field": [], "years_experience": 0,
                   "max_years_asked": None, "languages": [{"language": "German", "level": "native"}],
                   "places": [{"place": "München", "country": "DE", "work": ["onsite", "hybrid"]}],
                   "remote_anywhere": False, "accept_management": True,
                   "job_kinds": ["working_student", "internship"]}
        engineer = {**sales, "target_roles": ["AI Engineer"], "accept_management": False,
                    "job_kinds": ["regular", "freelance"]}
        nothing = R.prefs_mod.defaults()
        def ans(**kw):
            a = {"role_match": "yes", "years_required": None, "languages_required": [],
                 "is_management": False, "location": "best", "job_kind": "regular", "fit": 80, "reason": "r"}
            a.update(kw)
            return R.normalise(a)
        anything = ans(is_management=True, years_required=15, job_kind="gig",
                       languages_required=[{"language": "Japanese", "level": "native"}])
        res = {
          "sales_head": R.verdict(ans(is_management=True, years_required=10), sales)[0],
          "sales_needs_de": R.verdict(ans(languages_required=[{"language": "German", "level": "C1"}]), sales),
          "native_de_ok": R.verdict(ans(languages_required=[{"language": "German", "level": "native"}]), german)[0],
          "own_years_limit": R.verdict(ans(years_required=5), german),
          "own_mgmt": R.verdict(ans(is_management=True), german)[0],
          "place": R.verdict(ans(location="no"), sales)[0],
          "place_open": R.verdict(ans(location="no"), {**sales, "places": []})[0],
          "off": R.verdict(ans(role_match="no", reason="retail"), sales),
          "no_langs_listed": R.verdict(ans(languages_required=[{"language": "German", "level": "c1"}]),
                                       {**sales, "languages": []})[0],
          "student_ws": R.verdict(ans(job_kind="working_student"), student)[0],
          "student_intern": R.verdict(ans(job_kind="internship"), student)[0],
          "student_regular": R.verdict(ans(job_kind="regular"), student),
          "eng_intern": R.verdict(ans(job_kind="internship"), engineer),
          "eng_gig": R.verdict(ans(job_kind="gig"), engineer)[0],
          "eng_freelance": R.verdict(ans(job_kind="freelance"), engineer)[0],
          "eng_head": R.verdict(ans(is_management=True), engineer)[0],
          "nothing_set": R.verdict(anything, nothing)[0],
          "level_unknown": R.verdict(ans(languages_required=[{"language": "English", "level": "c1"}]),
                                     {**engineer, "languages": R.prefs_mod.normalise({"languages": [
                                         {"language": "German", "level": "B1"},
                                         {"language": "English", "level": None}]})["languages"]}),
          "level_unknown_text": R.preferences_text({**engineer, "languages": [{"language": "English", "level": ""}]}),
          "no_title_rule": not hasattr(R, "not_a_job") and not hasattr(R, "NOT_A_JOB"),
          "score": R.score(ans(years_required=10), sales),
          "score_close": R.score(ans(role_match="close", location="ok"), sales),
          "junk": R.normalise({"role_match": "maybe", "location": 3, "languages_required": "German",
                               "job_kind": "summer job"}),
          "old_gig": R.normalise({"gig_work": True})["job_kind"],
          "prompt": "Roles they want: Account Executive; Head of Sales" in R.candidate_text("P", sales)
                    and "Languages they speak: English native" in R.preferences_text(sales)
                    and "Kinds of job they want: working-student job, internship" in R.preferences_text(student)
                    and "Kinds of job they want: any kind" in R.preferences_text(sales),
        }
        print(json.dumps(res))
    ''')
    try:
        r = json.loads(out)
    except ValueError:
        check(False, "the verdict functions run", out[-600:])
        return
    check(r["sales_head"] == "passed" and r["no_title_rule"],
          "a Head of Sales role asking 10 years passes for a senior salesperson; no rule reads "
          "the title", json.dumps(r)[:300])
    check(r["sales_needs_de"][0] == "language" and "German C1" in r["sales_needs_de"][1]
          and r["native_de_ok"] == "passed" and r["no_langs_listed"] == "passed",
          "languages are held against the person's own levels (and none listed rules nothing out)",
          json.dumps(r)[:300])
    check(r["own_years_limit"][0] == "years" and "your limit is 4" in r["own_years_limit"][1]
          and r["own_mgmt"] == "management",
          "the years limit and management rule are the person's own", json.dumps(r)[:300])
    check(r["student_ws"] == "passed" and r["student_intern"] == "passed"
          and r["student_regular"][0] == "job-kind" and "regular job" in r["student_regular"][1]
          and "working-student job" in r["student_regular"][1],
          "journey, a student: Werkstudent jobs and internships pass, a regular job is not what "
          "they want, said plainly", json.dumps(r)[:400])
    check(r["eng_intern"][0] == "job-kind" and "internship" in r["eng_intern"][1]
          and r["eng_gig"] == "job-kind" and r["eng_freelance"] == "passed"
          and r["eng_head"] == "management",
          "journey, an engineer: internships and gig work are not the kinds they picked, "
          "contract work is", json.dumps(r)[:400])
    check(r["level_unknown"] == ["passed", ""]
          and "English (level not stated)" in r["level_unknown_text"],
          "a language Claude found without a level is kept, and never drops a job",
          json.dumps([r["level_unknown"], r["level_unknown_text"][-300:]]))
    check(r["nothing_set"] == "passed",
          "journey, no preferences at all: management, 15 years, gig work and a native Japanese "
          "requirement all pass (an unset limit rules nothing out)", json.dumps(r)[:300])
    check(r["place"] == "location" and r["place_open"] == "passed"
          and r["off"] == ["off-lane", "retail"],
          "place and not-their-field come from the model's judgement", json.dumps(r)[:300])
    check(r["score"] == 80 + 15 + 12 - 8 * 2 and r["score_close"] == 80,
          "the score rewards their roles and first-choice place, less per year over theirs",
          json.dumps(r)[:200])
    check(r["junk"]["role_match"] == "close" and r["junk"]["location"] == "ok"
          and r["junk"]["languages_required"] == [] and r["junk"]["job_kind"] == "regular"
          and r["old_gig"] == "gig" and r["prompt"],
          "an unreadable answer rules nothing out; the prompt carries the preferences and kinds",
          json.dumps(r)[:300])


def build_rules_section(py: pathlib.Path, work: pathlib.Path) -> None:
    """
    Nothing in the build refuses a role or a document by a fixed rule: the CV
    spread check only reports, the PDF builder rewrites dashes and notes a short
    page, the shortlist prints the person's own limits, and the instructions
    Claude builds by drop a role only for one of those limits. Arbeitnow's
    rules follow the same preferences. Journeys: a salesperson's achievement CV
    and a student's half-page CV both build.
    """
    b = work / "builder"
    tmp = work / "selftest-build"
    tmp.mkdir(exist_ok=True)
    sales = {"name": "Sam Doyle", "tagline": "Account Executive",
             "contact": ["Berlin, Germany", "sam@example.com"],
             "summary": "Closed EUR 2.1M in new ARR in 2025 — 132% of quota.",
             "sections": [{"type": "experience", "title": "Experience", "items": [
                 {"role": "Account Executive", "org": "Acme", "meta": "2021 – 2025 · Berlin",
                  "bullets": ["<b>132%</b> of quota in 2025, top of a team of 12 -- the largest book.",
                              "Grew the DACH mid-market book from EUR 1.2M to EUR 2.1M ARR."]}]},
                 {"type": "skills", "title": "Skills", "items": [["Sales", "MEDDIC · Salesforce"]]}]}
    student = {**sales, "name": "Lea Brandt", "tagline": "Werkstudentin Marketing",
               "summary": "Studies business in Munich.",
               "sections": [{"type": "experience", "title": "Erfahrung", "items": [
                   {"role": "Praktikantin Marketing", "org": "Beispiel GmbH", "meta": "2025",
                    "bullets": ["Ran the newsletter for 3,000 readers."]}]}]}
    (tmp / "cv_sales.json").write_text(json.dumps(sales, ensure_ascii=False), encoding="utf-8")
    (tmp / "cv_student.json").write_text(json.dumps(student, ensure_ascii=False), encoding="utf-8")

    def run(*args) -> subprocess.CompletedProcess:
        return subprocess.run([str(py), *args], cwd=b, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120,
                              env=dict(os.environ, PYTHONUTF8="1"))

    r = run("check_spread.py", str(tmp / "cv_sales.json"), str(tmp / "cv_student.json"))
    check(r.returncode == 0 and "0 with a bold label" in r.stdout and "FAILED" not in r.stdout,
          "the spread check only reports: an achievement CV with no project labels and a "
          "one-bullet student CV both pass", r.stdout[-400:] + r.stderr[-300:])
    pdfs = {}
    for name in ("sales", "student"):
        out = tmp / f"{name}.pdf"
        r = run("build_docs.py", "cv", str(tmp / f"cv_{name}.json"), str(out))
        pdfs[name] = (r, out)
    rs, out = pdfs["sales"]
    text = ps(py, work, f"""
        from pypdf import PdfReader
        print(" ".join(p.extract_text() for p in PdfReader(r"{out}").pages))
    """)
    check(rs.returncode == 0 and out.exists() and "rewrote dashes" in rs.stdout
          and "—" not in text and "–" not in text and "--" not in text
          and "2021-2025" in text.replace(" ", "") and "12, the largest" in text,
          "dashes are rewritten, not refused (a range keeps a hyphen, the rest a comma)",
          rs.stdout[-300:] + rs.stderr[-300:] + text[:300])
    rs, out = pdfs["student"]
    check(rs.returncode == 0 and out.exists() and "fills about" in rs.stdout and "pages: 1" in rs.stdout,
          "journey, a student with one internship: a short CV is built with a note, not refused",
          rs.stdout[-300:] + rs.stderr[-300:])

    lim = ps(py, work, f"""
        import sys
        sys.path.insert(0, r"{b}")
        import shortlist
        print(shortlist.limits())
    """)
    check("Kinds of job:" in lim and "Roles that mainly manage a team:" in lim
          and "Most years a posting may ask for:" in lim,
          "the shortlist prints the person's limits for the build to judge by", lim[:400])

    batch = (b / ".claude" / "commands" / "apply-batch.md").read_text(encoding="utf-8")
    skill = (b / ".claude" / "skills" / "cv-builder" / "SKILL.md").read_text(encoding="utf-8")
    mk = (work / ".claude" / "commands" / "make-profile.md").read_text(encoding="utf-8")
    ex = (work / "profiles" / "example" / PROFILE_MD).read_text(encoding="utf-8")
    check("Head of, Director" not in batch and "breaks one of those limits" in batch
          and "There is no other reason to drop a role" in batch
          and "four or more distinct projects" not in skill and "at most two\n  bullets per project" not in skill
          and "A limit that is not set rules nothing out" in skill
          and "two hard rules" not in mk and "kinds of job" in mk and "hard rules" not in ex,
          "the build instructions drop a role only for the person's own limits; no fixed "
          "management, project-count or hard-rule text is left")

    rules = ps(py, work, r'''
        import json, preferences as P, arbeitnow_match as am
        base = P.defaults()
        def with_(**kw):
            P.load = lambda folder=None: {"exists": True, "example": False,
                                          "preferences": {**base, **kw}}
            return am.rules_from_preferences()
        out = {
          "student": with_(job_kinds=["working_student"],
                           places=[{"place": "München", "country": "DE", "work": ["onsite"]}]),
          "engineer": with_(job_kinds=["regular"],
                            places=[{"place": "Berlin", "country": "DE", "work": ["onsite", "hybrid", "remote"]},
                                    {"place": "EU", "country": "", "work": ["remote"]}]),
          "mover": with_(job_kinds=["regular"], relocate=True,
                         places=[{"place": "Dubai", "country": "AE", "work": ["onsite"]}]),
          "open": with_(),
        }
        P.load = lambda folder=None: {"exists": False, "example": False, "preferences": base}
        out["none"] = am.rules_from_preferences()
        print(json.dumps(out))
    ''')
    try:
        r = json.loads(rules)
    except ValueError:
        check(False, "Arbeitnow's rules from the preferences run", rules[-400:])
        return
    check(r["student"] == {"countries": ["DE"], "remote_elsewhere": False, "student_roles": True}
          and r["engineer"] == {"countries": ["DE"], "remote_elsewhere": True, "student_roles": False}
          and r["mover"]["countries"] == [] and r["open"]["student_roles"] is True
          and r["open"]["countries"] == [] and r["none"] is None,
          "Arbeitnow follows the preferences: student jobs for a student, no country limit for "
          "someone who would relocate, nothing ruled out when nothing is set", json.dumps(r))


def any_person_section(py: pathlib.Path, work: pathlib.Path) -> None:
    """
    The blind review's findings (17 November 2026), each held as a check:
    language names in any language, a posting's own language judged by the
    work, career changers kept for the roles they want, the pre-filter
    stepping aside for a field it was not tuned on, CVs up to three pages,
    letters outside Western European scripts, Arbeitnow's student rule and its
    rules following Preferences, and first-install searches for the person's
    own roles.
    """
    out = ps(py, work, r'''
        import json, os, pathlib, tempfile
        import preferences as P, rank_ollama_real as R, arbeitnow_match as am, saved_searches as ss, config
        res = {}
        de = P.normalise({"languages": [{"language": "Deutsch", "level": "native"},
                                        {"language": "Français", "level": "b2"},
                                        {"language": "German", "level": "a1"}]})
        res["lang_names"] = [len(de["languages"]), P.level_of(de, "German"), P.level_of(de, "allemand"),
                             P.level_of(de, "French"), P.level_of(de, "Englisch (verhandlungssicher)")]
        prefs = {**P.defaults(), **de}
        v = R.normalise({"role_match": "yes", "fit": 80, "languages_required": [{"language": "Deutsch", "level": "c1"}]})
        res["deutsch_ok"] = R.verdict(v, prefs)[0]
        res["prompt_lang"] = "the level the work" in R.ANSWER_FIELDS and "requires it at c1 unless" not in R.ANSWER_FIELDS
        res["trainee_regular"] = "trainee or graduate programme, which is \"regular\"" in R.ANSWER_FIELDS
        res["changer_yes"] = R.verdict(R.normalise({"role_match": "yes", "fit": 15}), P.defaults())[0]
        res["changer_close"] = R.verdict(R.normalise({"role_match": "close", "fit": 15}), P.defaults())[0]
        res["yes_other_field"] = R.verdict(R.normalise({"role_match": "yes", "fit": 5}), P.defaults())[0]
        res["lang_compound"] = [P.language_key(x) for x in ("Business English", "Swiss German",
                                "Deutschkenntnisse", "Mandarin Chinese", "Cantonese", "Serbian")]
        res["lang_keep"] = len(P.normalise({"languages": [{"language": "Mandarin", "level": "b1"},
                               {"language": "Cantonese", "level": "native"},
                               {"language": "Serbian", "level": "native"},
                               {"language": "Croatian", "level": "b1"}]})["languages"])
        res["version"] = R.PROMPT_VERSION
        old_fit = config.MIN_FIT
        config.MIN_FIT = config.CLAUDE_MIN_FIT
        res["strong_claude"] = R.strong_line({"places": [{"place": "X"}]})
        config.MIN_FIT = old_fit
        rows = ss.from_preferences({"target_roles": ["Data Scientist (production ML)"],
                                    "places": [{"place": "Berlin", "country": "DE", "work": ["onsite"]}]})
        res["seed_clean"] = ["keywords=Data+Scientist&" in rows[0]["url"], "geoId=106967730" in rows[0]["url"]]
        res["strong"] = [R.strong_line({"places": []}), R.strong_line({"places": [{"place": "X"}]})]
        res["pages"] = [P.normalise({"cv_max_pages": x})["cv_max_pages"] for x in (3, "5", "x", 2)]
        # The pre-filter steps aside when it would set aside most of the pool.
        R.target_roles = lambda: ["Pflegefachkraft"]
        R.VEC_CACHE = pathlib.Path(tempfile.mkdtemp()) / "vec.json"
        config.EMBED_PREFILTER_FLOOR = 0.62
        far = lambda texts: [[1.0, 0.0] if t.startswith("search_query") else [0.0, 1.0] for t in texts]
        am.embed = far
        pool = [{"url": f"https://example.org/{i}", "title": f"Pflege {i}", "description": "x" * 300}
                for i in range(4)]
        kept, aside, note = R.prefilter(pool)
        res["prefilter_all"] = [len(kept), len(aside), "does not fit your field" in note]
        am.embed = lambda texts: [[1.0, 0.0] if t.startswith("search_query") or "Pflege 0" not in t
                                  else [0.0, 1.0] for t in texts]
        R.VEC_CACHE = pathlib.Path(tempfile.mkdtemp()) / "vec.json"
        kept, aside, note = R.prefilter(pool)
        res["prefilter_some"] = [len(kept), len(aside)]
        # Arbeitnow: whole job types only; titles never drop a job.
        s = am.normalise({"searches": [{"name": "x", "looking_for": "x"}], "student_roles": False})
        now = __import__("time").time()
        def ro(title, types=()):
            return am.rule_out({"created_at": now, "title": title, "types": list(types), "location": ""},
                               s, {}, now)
        res["student_rule"] = [ro("Sales Trainee (m/w/d)", ["Trainee"]), ro("Leiter Ausbildung Pflege"),
                               ro("Account Manager", ["International"]), ro("Werkstudent Marketing"),
                               ro("Marketing", ["Working student"]), ro("X", ["Intern"])]
        # Arbeitnow's rules keep following Preferences after a Save that left them alone.
        am.SETTINGS = pathlib.Path(tempfile.mkdtemp()) / "an.json"
        rules = {"countries": ["DE"], "remote_elsewhere": False, "student_roles": False}
        am.rules_from_preferences = lambda: dict(rules)
        a = am.save({"searches": [{"name": "Nurse", "looking_for": "nurse"}], **rules})
        rules["student_roles"] = True            # the person ticks working student in Preferences
        res["rules_follow"] = [a["rules_by"], am.load()["student_roles"]]
        b = am.save({"searches": [{"name": "Nurse", "looking_for": "nurse"}], "countries": ["FR"],
                     "remote_elsewhere": False, "student_roles": True})
        rules["countries"] = ["DE", "AT"]
        res["rules_own"] = [b["rules_by"], am.load()["countries"]]
        # Saved before any preferences existed, rules left at their defaults: they follow later.
        am.rules_from_preferences = lambda: None
        c = am.save({"searches": [{"name": "Nurse", "looking_for": "nurse"}]})
        am.rules_from_preferences = lambda: dict(rules)
        res["rules_before_prefs"] = [c["rules_by"], am.load()["countries"]]
        # Saved by hand before rules_by existed, rules as the preferences have them: follow.
        am._write(am.SETTINGS, {**am.normalise({"searches": [{"name": "N", "looking_for": "n"}],
                                               **rules}), "made_by": "you"})
        am.load()                                # the first read decides, and writes it down
        rules["student_roles"] = False
        res["rules_legacy"] = am.load()["student_roles"]
        # First-install searches give way to the person's own.
        ss.FILE = pathlib.Path(tempfile.mkdtemp()) / "searches.json"
        first = [r["label"] for r in ss.load()["searches"]]
        n = ss.seed_from_preferences({"target_roles": ["Account Executive", "Head of Sales"],
                                      "places": [{"place": "Dubai", "country": "AE", "work": ["onsite"]}]})
        after = ss.load()["searches"]
        again = ss.seed_from_preferences({"target_roles": ["Account Executive", "Head of Sales"],
                                          "places": [{"place": "Abu Dhabi", "country": "AE", "work": ["onsite"]}]})
        moved = [r["label"] for r in ss.load()["searches"]]
        off = ss.load()["searches"]
        off[0]["enabled"] = False                # switched off by the person: theirs now
        ss.save(searches=off)
        res["seed_off"] = ss.seed_from_preferences({"target_roles": ["Nurse"], "places": []})
        ss.save(searches=[*ss.load()["searches"], {"label": "Mine", "url": "https://www.linkedin.com/jobs/search/?keywords=Mine"}])
        kept = ss.seed_from_preferences({"target_roles": ["Nurse"], "places": []})
        res["seed"] = [len(first), n, [r["label"] for r in after], "location=Dubai" in after[0]["url"],
                       again, moved, kept]
        res["host"] = ss.clean_url("https://ae.linkedin.com/jobs/search/?keywords=x")
        import profile_chat as pc
        pa = json.dumps({"made_at": "1", "preferences": {"x": 1}})
        res["chat_prefs"] = [len(pc.changes({"preferences.json": pa},
                                            {"preferences.json": json.dumps({"made_at": "2", "preferences": {"x": 2}})})),
                             len(pc.changes({"preferences.json": pa},
                                            {"preferences.json": json.dumps({"made_at": "1", "preferences": {"x": 3}})}))]
        fr = {"languages": [{"language": "French", "level": "native"}, {"language": "English", "level": "b1"}]}
        res["lang_or"] = [R.language_gap({"languages_required": [{"language": n, "level": "c1"}]}, fr)
                          for n in ("French or English", "English and French")]
        # The example's own preferences, copied into someone's folder, are not theirs.
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "profile.md").write_text("# A nurse in Dubai", encoding="utf-8")
        (d / P.FILE_NAME).write_text((P.EXAMPLE / P.FILE_NAME).read_text(encoding="utf-8"), encoding="utf-8")
        st = P.load(d)
        res["example_copy"] = [st["exists"], st["preferences"]["target_roles"], P.due(d)]
        print(json.dumps(res, ensure_ascii=False))
    ''')
    try:
        r = json.loads(out)
    except ValueError:
        check(False, "the any-person checks run", out[-800:])
        return
    check(r["lang_names"] == [2, "native", "native", "b2", "none"] and r["deutsch_ok"] == "passed",
          "language names match in any language: Deutsch is German, Français is French",
          json.dumps(r["lang_names"]) + r["deutsch_ok"])
    check(r["prompt_lang"] and r["trainee_regular"],
          "a posting's own language is judged by the work, not a fixed C1; trainee programmes are "
          "regular jobs")
    check(r["yes_other_field"] == "weak-fit",
          "a role they asked for with a fit near zero (a different field) is still dropped",
          r["yes_other_field"])
    check(r["lang_compound"] == ["english", "german", "german", "chinese", "cantonese", "serbian"]
          and r["lang_keep"] == 4,
          "compound language names match; Mandarin and Cantonese, Serbian and Croatian stay apart",
          json.dumps([r["lang_compound"], r["lang_keep"]]))
    check(r["version"] == "6" and r["strong_claude"] == 80 and r["seed_clean"] == [True, True],
          "a changed prompt re-judges cached jobs; the strong line allows for Claude's lower scores; "
          "seeded searches drop notes in brackets and carry a known place id",
          json.dumps([r["version"], r["strong_claude"], r["seed_clean"]]))
    check(r["rules_before_prefs"] == ["preferences", ["DE", "AT"]] and r["rules_legacy"] is False,
          "Arbeitnow rules saved before Preferences existed, or before this change, still follow them",
          json.dumps([r["rules_before_prefs"], r["rules_legacy"]]))
    check(r["changer_yes"] == "passed" and r["changer_close"] == "weak-fit",
          "journey, a career changer: a role they asked for is not dropped on fit alone",
          json.dumps([r["changer_yes"], r["changer_close"]]))
    check(r["strong"] == [83, 95] and r["pages"] == [3, 3, 1, 2],
          "the strong line allows for no places; a CV may be up to three pages",
          json.dumps([r["strong"], r["pages"]]))
    check(r["prefilter_all"] == [4, 0, True] and r["prefilter_some"] == [3, 1],
          "the local pre-filter steps aside when it would set aside most of a pool",
          json.dumps([r["prefilter_all"], r["prefilter_some"]]))
    check(r["student_rule"] == ["", "", "", "", "student", "student"],
          "Arbeitnow drops student jobs only by the employer's own job type, never by title words",
          json.dumps(r["student_rule"]))
    check(r["rules_follow"] == ["preferences", True] and r["rules_own"] == ["you", ["FR"]],
          "Arbeitnow's rules keep following Preferences after a save that left them as they were; "
          "rules set differently are the person's", json.dumps([r["rules_follow"], r["rules_own"]]))
    check(r["seed"][0] == 3 and r["seed"][1] == 2
          and r["seed"][2] == ["Account Executive in Dubai", "Head of Sales in Dubai"]
          and r["seed"][3] and r["seed"][4] == 2
          and r["seed"][5] == ["Account Executive in Abu Dhabi", "Head of Sales in Abu Dhabi"]
          and r["seed"][6] == 0,
          "journey, a salesperson in Dubai: the ML examples become searches for their own roles, "
          "follow a corrected place, and a list they changed is left alone", json.dumps(r["seed"]))
    check(r["seed_off"] == 0, "a seeded search the person switched off is never written over",
          str(r["seed_off"]))
    check(r["host"] == "https://www.linkedin.com/jobs/search/?keywords=x"
          and r["lang_or"][0] == "" and "English C1" in r["lang_or"][1],
          "a LinkedIn country address is read on www; 'French or English' needs only one of them",
          json.dumps([r["host"], r["lang_or"]]))
    check(r["chat_prefs"] == [0, 1],
          "Edit profile shows its own edits to preferences.json, not the pipeline's rewrite "
          "that lands while Claude is working", json.dumps(r["chat_prefs"]))
    check(r["example_copy"] == [False, [], "missing"],
          "journey, first install with Claude unavailable: the example's copied preferences are "
          "not used as the person's (nothing is ruled out, and they are still due)",
          json.dumps(r["example_copy"]))

    b = work / "builder"
    tmp = work / "selftest-build"
    tmp.mkdir(exist_ok=True)
    cjk = {"name": "张伟 Zhang Wei", "tagline": "日本語 · 한국어", "contact": ["Berlin"],
           "summary": "Office in 北京.", "sections": []}
    (tmp / "cv_cjk.json").write_text(json.dumps(cjk, ensure_ascii=False), encoding="utf-8")
    rc = subprocess.run([str(py), "build_docs.py", "cv", str(tmp / "cv_cjk.json"), str(tmp / "cjk.pdf")],
                        cwd=b, capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=120, env=dict(os.environ, PYTHONUTF8="1"))
    ctext = ps(py, work, f"""
        from pypdf import PdfReader
        print(PdfReader(r"{tmp / 'cjk.pdf'}").pages[0].extract_text())
    """) if (tmp / "cjk.pdf").exists() else ""
    check(rc.returncode == 0 and ("张伟" in ctext or "no font on this computer has these letters" in rc.stdout),
          "Chinese, Japanese and Korean render when a font has them, and a missing letter is said, "
          "never silently dropped", rc.stdout[-300:] + rc.stderr[-200:] + ctext[:100])
    cv = {"name": "Łukasz Żółć", "tagline": "Satış Müdürü", "contact": ["Jiří Dvořák · Москва"],
          "summary": "Grew revenue at Ünal Ltd.", "sections": []}
    (tmp / "cv_intl.json").write_text(json.dumps(cv, ensure_ascii=False), encoding="utf-8")
    rr = subprocess.run([str(py), "build_docs.py", "cv", str(tmp / "cv_intl.json"), str(tmp / "intl.pdf")],
                        cwd=b, capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=120, env=dict(os.environ, PYTHONUTF8="1"))
    text = ps(py, work, f"""
        from pypdf import PdfReader
        print(PdfReader(r"{tmp / 'intl.pdf'}").pages[0].extract_text())
    """) if (tmp / "intl.pdf").exists() else ""
    check(rr.returncode == 0 and (("Łukasz Żółć" in text and "Москва" in text)
                                  or "no font with these letters" in rr.stdout),
          "Polish, Turkish, Czech and Cyrillic names render as letters, not boxes (or it says so)",
          rr.stdout[-300:] + rr.stderr[-300:] + text[:200])
    js = (work / "web" / "preferences.js").read_text(encoding="utf-8")
    skill = (b / ".claude" / "skills" / "cv-builder" / "SKILL.md").read_text(encoding="utf-8")
    page = (work / "web" / "dashboard.html").read_text(encoding="utf-8")
    batch = (b / ".claude" / "commands" / "apply-batch.md").read_text(encoding="utf-8")
    check('st.languages.push({language:"",level:""})' in js and 'name="pfPages" value="3"' in page
          and "when the person can work in it" in skill and 'id="anUsePrefs"' in page
          and "Rows copied word for word from the\nfictional example" in batch,
          "a new language starts with no level; three pages can be chosen; documents are written "
          "in a language the person can work in")


def preferences_section(e: "Env", py: pathlib.Path, work: pathlib.Path) -> None:
    """
    preferences.json: the bridge has Claude (the fake) write it from the
    profile on its own, rewrites it when the profile changes, never writes over
    what the person changed, says plainly when it cannot, and never touches the
    example profile's copy.
    """
    me = work / "profiles" / "me"
    f = me / PREFS_JSON
    calls = e.fakebin / "prefs-calls.jsonl"
    example = work / "profiles" / "example" / PREFS_JSON
    example_before = example.read_bytes() if example.exists() else b""
    e.mode("ok")

    def doc() -> dict:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def brief() -> dict:
        return (e.get(PROGRESS_ROUTE)[1] or {}).get("preferences") or {}

    def wait(cond, seconds: float = 40) -> bool:
        end = time.time() + seconds
        while time.time() < end:
            try:
                if cond():
                    return True
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.4)
        return False

    # 1. On its own: the bridge has been up a while, so the file is there, and
    # written for the profile as it is now (the sections before this one edit
    # it, and each edit is caught up within seconds).
    sig = ps(py, work, f'''
        import pathlib, preferences as P
        print(P.profile_sig(pathlib.Path(r"{me}")))
    ''')
    ok = wait(lambda: brief().get("state") == "ready" and doc().get("profile_sig") == sig)
    d = doc()
    check(ok and d.get("version") == 2 and set(d.get("made_by", {}).values()) == {"model"}
          and d.get("model") == "claude-sonnet-5-5" and d.get("reviewed") is False,
          "the bridge writes profiles/me/preferences.json from the profile by itself",
          json.dumps(d)[:400])
    p = d.get("preferences") or {}
    check(p.get("target_roles", [""])[0] == "Machine Learning Engineer"
          and p.get("languages") == [{"language": "English", "level": "c1"},
                                     {"language": "German", "level": "a2"}]
          and p.get("remote_anywhere") is False and p.get("cv_shape") == "projects"
          and p.get("job_kinds") == ["regular", "freelance"],
          "Claude's answer is cleaned into the fields (levels, kinds, defaults for nulls, fenced JSON)",
          json.dumps(p)[:300])
    qs = brief().get("questions") or []
    check(len(qs) == 1 and qs[0]["field"] == "accept_management" and "manages a team" in qs[0]["question"],
          "Claude's open questions reach the dashboard (an empty one is dropped)", json.dumps(qs))
    rows = [json.loads(x) for x in calls.read_text(encoding="utf-8").splitlines()] if calls.exists() else []
    a = rows[-1]["args"] if rows else []
    check(bool(rows) and "--tools" in a and a[a.index("--tools") + 1] == "" and "--model" in a
          and a[a.index("--model") + 1] == "claude-sonnet-5-5" and rows[-1]["chars"] > 500,
          "one lean Sonnet call: no tools, the profile on stdin", json.dumps(rows[-1:])[:300])
    b = brief()
    check(b.get("state") == "ready" and b.get("reviewed") is False,
          "the dashboard is told to ask the person to check them", json.dumps(b))
    n_calls = len(rows)
    time.sleep(5)
    rows = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    check(len(rows) == n_calls, "nothing changed, nothing asked again", f"{len(rows)} calls, was {n_calls}")

    code, g = e.get(PREFERENCES_ROUTE)
    check(code == 200 and g.get("exists") and not g.get("example") and g.get("has_profile")
          and len(g.get("choices", {}).get("countries", [])) > 150
          and g.get("made_by", {}).get("target_roles") == "model",
          "GET /preferences: values, who set them, the form's choices", json.dumps(g)[:300])

    # 2. The person's edit is theirs.
    r = e.post(PREFERENCES_ROUTE, {"preferences": {**p, "target_roles": ["Account Executive"],
                                                    "years_experience": 7}})
    d = doc()
    check(r.get("ok") and d["preferences"]["target_roles"] == ["Account Executive"]
          and d["made_by"]["target_roles"] == "you" and d["made_by"]["years_experience"] == "you"
          and d["made_by"]["places"] == "model" and d.get("reviewed") is True,
          "Save makes a changed field the person's and marks the set as checked", json.dumps(d)[:400])
    check(brief().get("reviewed") is True and brief().get("questions") == [],
          "after Save the dashboard stops asking, questions included", json.dumps(brief()))

    # 3. A hand edit of the file (or the Edit profile chat) counts as theirs too.
    d["preferences"]["max_years_asked"] = 9
    d["preferences"]["accept_management"] = True
    f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    check(e.get(PREFERENCES_ROUTE)[1].get("made_by", {}).get("max_years_asked") == "you",
          "a field changed in the file by hand is the person's")

    # 4. The profile changes: Claude writes the rest again, the person's fields stay.
    pm = me / PROFILE_MD
    pm.write_text(pm.read_text(encoding="utf-8") + "\nSELFTEST ROLE: Head of Marketing\n", encoding="utf-8")
    ok = wait(lambda: doc()["preferences"]["cv_shape"] == "achievements" and brief().get("state") == "ready")
    d = doc()
    check(ok and d["preferences"]["target_roles"] == ["Account Executive"]
          and d["preferences"]["years_experience"] == 7 and d["preferences"]["max_years_asked"] == 9
          and d["preferences"]["cv_shape"] == "achievements" and d.get("changed") == ["cv_shape"]
          and d.get("reviewed") is False,
          "a changed profile rewrites the model's fields and keeps the person's", json.dumps(d)[:500])
    check(brief().get("changed") == ["cv_shape"], "the dashboard says what changed", json.dumps(brief()))
    check(d.get("questions") == [] and brief().get("questions") == [],
          "a question about a field the person already set is not asked again", json.dumps(d)[:300])

    # 5. Fill from my profile: everything again, the person's edits included.
    r = e.post("/preferences/refresh", {"reset": True})
    ok = r.get("started") and wait(lambda: doc()["preferences"]["target_roles"][0] == "Head of Marketing"
                                   and brief().get("state") == "ready")
    d = doc()
    check(ok and d["preferences"]["target_roles"][0] == "Head of Marketing"
          and set(d["made_by"].values()) == {"model"},
          "Fill from my profile rewrites every field from the profile", json.dumps(d)[:300])

    # 6. Claude cannot answer: said plainly, nothing half-written, no call every 2 s.
    sig2 = ps(py, work, f'''
        import pathlib, preferences as P
        print(P.profile_sig(pathlib.Path(r"{me}")))
    ''')
    e.mode("prefsfail")
    time.sleep(0.5)
    before = len(calls.read_text(encoding="utf-8").splitlines())
    f.unlink()
    ok = wait(lambda: brief().get("state") == "failed")
    b = brief()
    check(ok and "signed out" in b.get("error", "") and "claude auth login" in b.get("fix", "")
          and not f.exists(),
          "a signed-out Claude is reported with the fix, and no file is written", json.dumps(b))
    time.sleep(6)
    after = len(calls.read_text(encoding="utf-8").splitlines())
    check(after - before == 1, "after a failure the same profile is not asked again by itself",
          f"{after - before} calls")
    ev = e.get("/events")[1].get("events", [])
    check(any(x.get("step") == "preferences" and x.get("status") == "failed" and x.get("fix") for x in ev),
          "the failure is in the activity list with what to do")
    # Filled in by hand meanwhile: only what the person set is theirs, and the
    # rest is still Claude's to fill in once it can.
    form = {**g["preferences"], "target_roles": ["Chief of Staff"], "places": [],
            "languages": [], "not_my_field": [], "years_experience": None,
            "max_years_asked": None, "remote_anywhere": False, "accept_management": True,
            "cv_shape": "achievements", "cv_max_pages": 1, "cv_state_years": False}
    e.post(PREFERENCES_ROUTE, {"preferences": form})
    d = doc()
    check(d.get("made_by", {}).get("target_roles") == "you" and d["made_by"]["places"] == "model"
          and d.get("profile_sig") is None,
          "filled in by hand without Claude: only the fields set are the person's", json.dumps(d)[:300])
    e.mode("ok")
    r = e.post("/preferences/refresh", {})
    ok = r.get("started") and wait(lambda: brief().get("state") == "ready" and doc().get("profile_sig") == sig2)
    d = doc()
    check(ok and d["preferences"]["target_roles"] == ["Chief of Staff"]
          and d["preferences"]["places"] and d["made_by"]["places"] == "model",
          "Try again fills in the rest once Claude answers, and keeps what the person set",
          json.dumps(d)[:400])

    # 7. A damaged file is kept aside and written again.
    f.write_text("{not json", encoding="utf-8")
    ok = wait(lambda: doc().get("version") == 2)
    check(ok and any(x.name.startswith(PREFS_JSON + ".damaged-") for x in me.iterdir()),
          "a damaged preferences.json is kept aside and written again from the profile")

    # 8. The example profile's copy ships with the code and is never written.
    check(example.exists() and example.read_bytes() == example_before,
          "the example profile's preferences.json is left exactly as shipped")
    code, _ = e.get("/web/preferences.js")
    status, page = e.get(DASHBOARD_ROUTE)
    check(code == 200 and b'id="pfDlg"' in page and b'id="btnPrefs"' in page
          and b"/web/preferences.js" in page,
          "the dashboard has the Preferences button, window and script")

    # 9. The local model takes over when Claude cannot answer (a stand-in Ollama).
    port = free_port()
    answering_ollama(port)
    out = ps(py, work, f'''
        import os, pathlib, config, preferences as P
        config.OLLAMA_URL = "http://127.0.0.1:{port}"
        def no(prompt): raise P.GenerateError("Claude is signed out", "sign in")
        P.ask_claude = no
        os.environ.pop("PREFS_LOCAL", None)
        values, model, asked = P.generate(pathlib.Path(r"{me}"), log=lambda m: None)
        print(model, values["accept_management"], values["target_roles"])
    ''')
    check(out.startswith("local:") and "True []" in out,
          "without Claude, the local model writes them (and an empty answer rules nothing out)", out)


def updater_section(py: pathlib.Path, work: pathlib.Path) -> None:
    """
    Update now (scraper/updater.py) against a stand-in GitHub, in a throwaway
    copy: the new version replaces the program's files, adds and removes
    files, never touches the user's, keeps a backup, refuses a download that
    is not this project, and refuses a git clone with local edits.
    """
    import http.server
    import threading
    import zipfile
    import io
    root = pathlib.Path(tempfile.mkdtemp()) / "jp"
    (root / "scraper").mkdir(parents=True)
    real = (work / "scraper" / "updater.py").read_text(encoding="utf-8")
    old = {START_PY: "print('old')\n", "scraper/serve.py": "# old\n", README_MD: "old readme\n",
           "scripts/old_tool.py": "# goes away\n", "scraper/requirements.txt": "psutil\n",
           "scraper/updater.py": real, PROFILES_EXAMPLE_PROFILE_MD: "example, old\n"}
    mine = {"profiles/me/profile.md": "MY PROFILE\n", "scraper/out/jobs.json": "[1]\n",
            LOCAL_ENV: "X=1\n"}
    for rel, text in {**old, **mine}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / ".installed.json").write_text(json.dumps({"sha": "a" * 40, "files": sorted(old)}),
                                          encoding="utf-8")

    def zipped(files: dict) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for rel, text in files.items():
                z.writestr(f"job-application-pipeline-{'b' * 7}/{rel}", text)
        return buf.getvalue()
    new = {START_PY: "print('new')\n", "scraper/serve.py": "# new\n", README_MD: NEW_README,
           "scripts/new_tool.py": "# new\n", "scraper/requirements.txt": "psutil\n",
           "scraper/updater.py": real, PROFILES_EXAMPLE_PROFILE_MD: "example, new\n",
           LOCAL_ENV: "SHOULD NOT LAND\n"}
    served = {"zip": zipped(new)}

    class GitHub(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if "/commits/main" in self.path:
                body = json.dumps({"sha": "b" * 40, "commit": {"committer": {"date": "2026-10-25"}}}).encode()
            elif "/compare/" in self.path:
                body = json.dumps({"status": "ahead", "ahead_by": 2, "commits": [
                    {"commit": {"message": "Fix one\n\nmore"}}, {"commit": {"message": "Fix two"}}]}).encode()
            else:
                body = served["zip"]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass                # quiet: the checks are the output, not every request
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), GitHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    env = dict(os.environ, UPDATE_API=f"http://127.0.0.1:{port}", UPDATE_CHECK="1",
               UPDATE_ZIP=f"http://127.0.0.1:{port}/zip/{{sha}}", PYTHONUTF8="1")

    def run(code: str) -> dict:
        out = subprocess.run([str(py), "-c", "import sys, json; sys.path.insert(0, r'%s')\n"
                              "import updater\n%s" % (root / "scraper", code)],
                             capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
        try:
            return json.loads(out.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"error": (out.stdout + out.stderr)[-400:]}
    try:
        c = run("print(json.dumps(updater.check()))")
        check(c.get("available") and c.get("behind") == 2 and c.get("changes") == ["Fix two", "Fix one"],
              "an update is found on GitHub, with what changed", json.dumps(c)[:300])
        a = run("print(json.dumps(updater.apply(log=lambda m: None)))")
        rd = lambda rel: (root / rel).read_text(encoding="utf-8") if (root / rel).exists() else None
        backups = list((root / "_backups").glob("update-*/README.md"))
        check(a.get("ok") and rd(README_MD) == NEW_README and rd("scripts/new_tool.py")
              and rd("scripts/old_tool.py") is None and rd(PROFILES_EXAMPLE_PROFILE_MD) == "example, new\n"
              and backups and backups[0].read_text(encoding="utf-8") == "old readme\n"
              and json.loads(rd(".installed.json"))["sha"] == "b" * 40,
              "Update now replaces, adds and removes program files, keeping the old ones",
              json.dumps(a)[:300])
        check(rd("profiles/me/profile.md") == "MY PROFILE\n" and rd("scraper/out/jobs.json") == "[1]\n"
              and rd(LOCAL_ENV) == "X=1\n",
              "an update never touches the profile, the collected jobs or local.env")
        served["zip"] = zipped({README_MD: "not this project\n"})
        a = run("print(json.dumps({'r': repr(updater.apply(log=lambda m: None))}))")
        check("does not look like Job Pipeline" in json.dumps(a) and rd(README_MD) == NEW_README,
              "a download that is not this project changes nothing", json.dumps(a)[:300])
    finally:
        srv.shutdown()

    git = shutil.which("git")
    if git:
        g = pathlib.Path(tempfile.mkdtemp()) / "clone"
        (g / "scraper").mkdir(parents=True)
        (g / "scraper" / "updater.py").write_text(real, encoding="utf-8")
        (g / README_MD).write_text("x\n", encoding="utf-8")
        for args in (["init", "-q"], ["add", "-A"],
                     ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
            subprocess.run([git, *args], cwd=g, capture_output=True)
        (g / README_MD).write_text("edited\n", encoding="utf-8")
        out = subprocess.run([str(py), "-c", "import sys, json; sys.path.insert(0, r'%s')\n"
                              "import updater; print(json.dumps(updater.apply(log=lambda m: None)))"
                              % (g / "scraper")], capture_output=True, text=True, encoding="utf-8",
                             env=dict(env), timeout=120).stdout
        check('"ok": false' in out and README_MD in out and (g / README_MD).read_text() == "edited\n",
              "a git clone with local edits is not updated, and the edited files are named", out[-300:])


def _write_local(sc: pathlib.Path, text: str | None) -> None:
    """Write (or remove) the copy's config_local.py, and drop its compiled
    copy: Python checks that by size and a timestamp in whole seconds, so two
    same-length rewrites within one second would load the old settings."""
    f = sc / "config_local.py"
    if text is None:
        f.unlink(missing_ok=True)
    else:
        f.write_text(text, encoding="utf-8")
    for pyc in (sc / "__pycache__").glob("config_local*.pyc"):
        pyc.unlink(missing_ok=True)


def _days_ago(n: int) -> str:
    return (dt.date.today() - dt.timedelta(days=n)).isoformat()


def find_chrome() -> str | None:
    """Chrome or Chromium on this machine, or None. GitHub's runners have it on
    all three systems; a machine without it skips the collector checks."""
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    for p in (r"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              r"/Applications/Chromium.app/Contents/MacOS/Chromium",
              os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
              os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
              os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")):
        if p and os.path.isfile(p):
            return p
    return None


def collector_section(work: pathlib.Path) -> None:
    """
    The LinkedIn collector on a LinkedIn-like page in headless Chrome
    (25 October 2026). The page (scripts/linkedin_test_page.html) is slow on
    purpose: the list appears seconds after the job-details pane and every row
    of its virtualised list fills in only some time after it scrolls into view.
    The collector used to start on the first card, which is the pane's, pick
    the pane as the scroller and come back short; it must now wait for the page
    and read all 25, and never the jobs linked in the messaging overlay. Chrome runs with a virtual clock, so the timings are the
    same on a fast laptop and a slow CI machine and the counts are exact.
    """
    chrome = find_chrome()
    if not chrome:
        print("  (Chrome is not installed, so the collector checks are skipped)")
        return
    import http.server
    import threading
    site = pathlib.Path(tempfile.mkdtemp(prefix="jp-li-"))
    for sub in ("search", "search-results"):
        d = site / "jobs" / sub
        d.mkdir(parents=True)
        shutil.copy(work / "scripts" / "linkedin_test_page.html", d / "index.html")
        shutil.copy(work / "extension" / "content.js", d / "content.js")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(site), **k)

        def log_message(self, *a):
            pass                # quiet: the checks are the output, not every request

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def run(path: str, query: str) -> dict:
        prof = tempfile.mkdtemp(prefix="jp-chrome-")
        args = [chrome, "--headless=new", "--disable-gpu", "--no-first-run",
                "--no-default-browser-check", f"--user-data-dir={prof}",
                # The content script only wakes up on linkedin.com.
                f"--host-resolver-rules=MAP www.linkedin.com 127.0.0.1:{port}",
                "--window-size=1280,800", "--virtual-time-budget=240000", "--dump-dom",
                f"http://www.linkedin.com:{port}/jobs/{path}/?{query}"]
        if sys.platform.startswith("linux"):
            args.insert(1, "--no-sandbox")       # CI containers have no user namespaces
        try:
            r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=180)
            out = r.stdout
        except subprocess.TimeoutExpired:
            out = ""
        finally:
            shutil.rmtree(prof, ignore_errors=True)
        import html as _html
        import re as _re
        m = _re.search(r'data-result="([^"]*)"', out)
        try:
            return json.loads(_html.unescape(m.group(1))) if m else {"error": "no result in the page"}
        except ValueError:
            return {"error": "unreadable result"}

    try:
        cases = [
            ("search", "list=4000&fill=600", 25, "a slow page: list late, rows fill in 0.6s"),
            ("search", "list=8000&fill=2500", 25, "a very slow page: list after 8s, rows fill in 2.5s"),
            ("search-results", "layout=hashed&list=4000&fill=1500", 25,
             "the hashed-class layout, rows keyed by componentkey"),
            ("search-results", "layout=refs&list=4000&fill=1500", 25,
             "the job-card-component-ref layout, no job links in the cards"),
            ("search", "n=12&json=12", 12, "a 12-result search, counted from the page data"),
            ("search", "n=7&fill=1000", 7, "a short last page with no count on it"),
            # November 2026, Windows: searches in another country stopped at 12
            # and 7 of a 25-card list, on a count that was not the list's.
            ("search", "n=25&json=12&fill=1000", 25,
             "a 25-card list whose page data says 12 results"),
            ("search-results", "layout=refs&n=25&count=7&fill=1500", 25,
             "a 25-card list whose header says 7 results"),
        ]
        for path, query, want, label in cases:
            r = run(path, query)
            # chatJobs: links to other jobs in the messaging overlay, never results.
            check(r.get("count") == want and r.get("parsedOk") is True and not r.get("err")
                  and r.get("chatJobs") == 0,
                  f"LinkedIn collector reads {want} of {want}: {label}", json.dumps(r))
    finally:
        srv.shutdown()
        shutil.rmtree(site, ignore_errors=True)


def main() -> int:
    print(f"Job Pipeline self-test on {sys.platform}, Python {sys.version.split()[0]}")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="jp-selftest-"))
    print(f"working in {tmp}")
    e = None
    try:
        work = make_copy(tmp)

        section("setup")
        r = subprocess.run([sys.executable, str(work / "setup.py"), "--skip-model", "--no-prompt"],
                           cwd=work, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=dict(os.environ, PYTHONUTF8="1"), timeout=900)
        check(r.returncode == 0, "setup finishes", (r.stdout + r.stderr)[-600:])
        py = work / "scraper" / VENV / (SCRIPTS_PYTHON_EXE if IS_WIN else BIN_PYTHON)
        check(py.exists(), "venv created")
        link = work / "builder" / VENV
        check((link / (SCRIPTS_PYTHON_EXE if IS_WIN else BIN_PYTHON)).exists(),
              "builder/.venv links to the scraper venv")
        if IS_WIN:
            check((work / "scraper" / VENV / "bin" / "python").exists(),
                  ".venv/bin/python shim for Git Bash")

        fakebin = make_fakebin(tmp, py)
        e = Env(work, fakebin, free_port())

        section("platform helpers")
        out = ps(py, work, r'''
            import pathlib, tempfile, platform_util as pu
            t = pathlib.Path(tempfile.mkdtemp())
            (t / "a").mkdir(); (t / "a" / "f.txt").write_text("one")
            (t / "c").mkdir(); (t / "c" / "f.txt").write_text("two")
            pu.link_dir(t / "a", t / "b"); r1 = (t / "b" / "f.txt").read_text()
            pu.link_dir(t / "c", t / "b"); r2 = (t / "b" / "f.txt").read_text()
            print(r1, r2, (t / "a" / "f.txt").exists())
        ''')
        check(out == "one two True", "link_dir creates and replaces a link, keeps the target", out)

        # Four processes append to the applied log at once; the lock must keep
        # every row (no lost update, no torn file).
        writer = textwrap.dedent(r'''
            import sys; sys.path.insert(0, sys.argv[1])
            import ledger
            for i in range(25):
                ledger.add_applied([{"url": f"https://example.org/{sys.argv[2]}/{i}",
                                     "company": "Größe GmbH", "title": "Engineer"}])
        ''')
        procs = [subprocess.Popen([str(py), "-c", writer, str(work / "scraper"), str(n)],
                                  env=dict(os.environ, PYTHONUTF8="0" if IS_WIN else "1"))
                 for n in range(4)]
        for p in procs:
            p.wait(timeout=120)
        csv = work / "scraper" / "history" / "applied.csv"
        rows = csv.read_text(encoding="utf-8").splitlines() if csv.exists() else []
        check(len(rows) == 101, "file lock: 4 writers x 25 rows, none lost", f"{len(rows) - 1} rows")
        check(any("Größe" in r for r in rows), "umlauts survive the applied log")
        csv.unlink(missing_ok=True)

        r = subprocess.run([str(py), "test_gate.py"], cwd=work / "scraper", capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ, PYTHONUTF8="1"), timeout=120)
        check(r.returncode == 0, "gate tests", r.stdout[-400:])

        section("bridge")
        stub_ollama()
        e.start_bridge()
        began = time.time()
        # Generous on purpose: the first start on a fresh CI machine loads a
        # just-installed venv, and a cold macOS runner took about a minute.
        up = e.wait_up(120)
        log = work / "scraper" / "out" / "bridge.log"
        check(up, f"plain `python start.py` sets itself up and starts the bridge on {e.port} "
                  f"({time.time() - began:.0f}s)",
              log.read_text(encoding="utf-8", errors="replace")[-1500:] if log.exists() else "no log")
        # start.py asks to turn on start-at-login until it is on; that needs a
        # status query that works on every system without installing anything.
        st = subprocess.run([str(e.py), str(work / "scripts" / "install-agent.py"), "--status"],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
        check(st.returncode in (0, 1) and "at login" in st.stdout,
              "start-at-login status can be read", (st.stdout + st.stderr)[-400:])
        for path in (DASHBOARD_ROUTE, RANKING_ROUTE, "/funnel", "/doctor", "/events", STATUS_ROUTE):
            try:
                code = e.get(path, 30)[0]
            except OSError as ex:
                code = str(ex)
            check(code == 200, f"GET {path}", str(code))
        doc = e.get("/doctor")[1]
        claude = next((c for c in doc.get("checks", []) if c["id"] == "claude"), {})
        check(claude.get("ok"), "doctor finds claude", json.dumps(claude))
        origin_section(e)
        topbar_check(e, "before any batch")

        section("builds run by hand")
        cases = [
            ("single session builds 2", "2", "ok", "ok", 2),
            ("two parallel sessions build 5", "5", "ok", "ok", 5),
            ("usage limit is a stop, not a failure", "2", "limit", "stopped", 0),
            ("signed-out Claude is reported", "2", "auth", "failed", 0),
            ("BATCH-STOP is respected", "2", "stop", "stopped", 0),
        ]
        # The words each outcome must contain, so a run that failed earlier for
        # another reason (no Ollama, say) cannot pass as the expected failure.
        words = {"ok": "built today", "limit": "usage limit", "auth": "signed out",
                 "stop": "on purpose"}
        for label, target, mode, want_status, want_built in cases:
            code, o = e.run_build(target, mode)
            check(o.get("status") == want_status and e.built() == want_built
                  and words[mode] in o.get("message", ""), label,
                  f"exit {code}, outcome {o}, built {e.built()}")
        check(not (e.builder / "logs" / RUN_LOCK).exists(), "lock released after every run")
        report = e.builder / "applications" / DAY / "batch.html"
        e.run_build("2", "ok")
        check(report.exists() and "Firma Ü" in report.read_text(encoding="utf-8"),
              "report written, umlauts intact")
        topbar_check(e, "with a batch")
        build_stage_check(py, work)

        code, o = e.run_build("2", "hang", STARTUP_GRACE_MIN="0.05", WATCHDOG_EVERY_S="1")
        log = (e.builder / "logs" / f"{DAY}.log").read_text(encoding="utf-8", errors="replace")
        check(o.get("status") == "failed" and "watchdog: no new documents" in log,
              "watchdog stops a hung Claude, run ends cleanly", str(o))
        left = ps(py, work, f"import platform_util as pu; print(len(pu.find_apply_batch_claudes(r'{e.builder}')))")
        check(left == "0", "no Claude left running after the watchdog", left)

        section("builds run by the bridge")
        e.clean_day()
        e.mode("ok")
        r = e.post(BUILD_ROUTE, {"top": 2})
        check(r.get("ok"), "POST /build starts a build", str(r))
        end = time.time() + 60
        while time.time() < end and e.get(STATUS_ROUTE)[1]["build"]["running"]:
            time.sleep(1)
        check(e.outcome().get("status") == "ok" and e.built() == 2, "bridge build finishes",
              str(e.outcome()))
        ev = e.get("/events")[1]
        ev = ev.get("events", ev) if isinstance(ev, dict) else ev
        check(any(x.get("step") == "build" and x.get("status") == "ok" for x in ev),
              "outcome reaches the Activity log")

        # A second build while one runs is refused, then Stop ends everything.
        e.clean_day()
        e.mode("hang")
        e.post(BUILD_ROUTE, {"top": 2})
        end = time.time() + 30
        found = "0"
        while time.time() < end:
            found = ps(py, work, f"import platform_util as pu; print(len(pu.find_apply_batch_claudes(r'{e.builder}')))")
            if found != "0":
                break
            time.sleep(1)
        check(found != "0", "build reached Claude")
        r = e.post(BUILD_ROUTE, {"top": 2})
        check(not r.get("ok"), "a second build is refused while one runs", str(r))
        by_hand = subprocess.run([str(py), "run_batch.py", "2"], cwd=e.builder,
                                 env=dict(e.env, BUILD_MANUAL="1"), capture_output=True,
                                 text=True, encoding="utf-8", timeout=60)
        check("already running" in by_hand.stdout, "run lock refuses a by-hand run too",
              by_hand.stdout[-200:])

        # Kill the bridge itself: the build must survive and be adopted again.
        serve = ps(py, work, f"""
            import platform_util as pu
            print(pu.find_script_procs(__import__('pathlib').Path(r'{work / 'scraper' / 'serve.py'}'))[0])
        """)
        ps(py, work, f"import psutil; psutil.Process({serve}).kill()")
        time.sleep(1)
        check(e.wait_up(40), "supervisor restarts a crashed bridge")
        b = e.get(STATUS_ROUTE)[1]["build"]
        check(b.get("running"), "restarted bridge adopts the running build", str(b))
        r = e.post("/build/stop")
        check(r.get("ok") and not r.get("still_running"), "Stop ends the build", str(r))
        left = ps(py, work, f"""
            import pathlib, platform_util as pu
            b = pathlib.Path(r'{e.builder}')
            print(len(pu.find_apply_batch_claudes(b)) + len(pu.find_script_procs(b / 'run_batch.py')))
        """)
        check(left == "0", "nothing of the build is left running", left)
        check(not (e.builder / "logs" / RUN_LOCK).exists(), "Stop clears the run lock")
        e.clean_day()
        e.mode("ok")
        r = e.post(BUILD_ROUTE, {"top": 1})
        check(r.get("ok"), "a new build starts straight after a Stop", str(r))
        end = time.time() + 60
        while time.time() < end and e.get(STATUS_ROUTE)[1]["build"]["running"]:
            time.sleep(1)

        section("a batch cut off by a usage limit keeps every finished application")
        recovery_section(py, work, e.port)

        section("which jobs the ranker reads (real ranker, real pool rules)")
        pool_section(py, work, fakebin)

        section("the verdict follows the person's preferences, not fixed rules")
        verdict_section(py, work)

        section("the build: no fixed rule refuses a role or a document")
        build_rules_section(py, work)

        section("anyone, any field: the blind review's findings")
        any_person_section(py, work)

        section("Arbeitnow: searches by meaning, rules, and the dashboard panel")
        arbeitnow_section(e, py, work)

        section("saved searches: kept by the pipeline, run by the extension")
        searches_section(e, py, work)

        section("Edit profile: tell Claude what to change, see it, undo it")
        profile_chat_section(e, py, work)

        section("job preferences: written from the profile, kept in step, yours when you change them")
        preferences_section(e, py, work)

        section("LinkedIn collector: waits for the page, then reads every card (headless Chrome)")
        collector_section(work)

        section("Update now: a new version from GitHub, the user's files kept")
        updater_section(py, work)

    finally:
        section("cleanup")
        if e is not None and e.bridge is not None:
            ps(e.py, e.work, f"import platform_util as pu; pu.kill_tree({e.bridge.pid})")
        if KEEP:
            print(f"kept {tmp}")
        else:
            for _ in range(5):
                shutil.rmtree(tmp, ignore_errors=True)
                if not tmp.exists():
                    break
                time.sleep(1)
            print("removed the temporary copy" if not tmp.exists() else f"could not fully remove {tmp}")

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    for label in failed:
        print(f"  FAILED: {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
