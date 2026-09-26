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
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request

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
    skip = {".git", ".venv", "venv", "applications", "logs", "inbox", "out", "history",
            "__pycache__", "local.env", "config_local.py", ".DS_Store", "_backups",
            "docs"}
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
    print(json.dumps({"loggedIn": True})); sys.exit(0)
prompt = args[args.index("-p") + 1] if "-p" in args else ""
parts = prompt.split()
want = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 3
shard, shards = os.environ.get("APPLY_SHARD", "1"), os.environ.get("APPLY_SHARDS", "1")
if mode == "limit": print("You've hit your limit · resets 5pm (Europe/Berlin)"); sys.exit(1)
if mode == "auth": print("Invalid API key · Please run /login"); sys.exit(1)
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
    shutil.copy(work / "scraper" / "rank_ollama.py", work / "scraper" / "rank_ollama_real.py")
    (work / "scraper" / "rank_ollama.py").write_text(FAKE_RANK, encoding="utf-8")
    (work / "scraper" / "check_live.py").write_text('print("fake: all open")\n', encoding="utf-8")
    # A filled-in profile, so the "still the example" guard lets the build run.
    me = work / "profiles" / "me"
    shutil.copytree(work / "profiles" / "example", me)
    for name in ("identity.md", "profile.md"):
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
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
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
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"models": models if models is not None
                        else [{"name": "llama3.1:latest"}]} if "tags" in self.path
                       else {"version": "selftest"})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._send({"response": json.dumps({
                "german_level": "none", "years_required": 2, "role_family": "ml",
                "is_management": False, "berlin": True, "remote_germany": False,
                "fit": 80, "reason": "selftest answer"})})

        def log_message(self, *a):
            pass
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
        py = work / "scraper" / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")
        self.py = py
        self.env = dict(os.environ, BRIDGE_PORT=str(port), NO_NOTIFY="1",
                        PATH=str(fakebin) + os.pathsep + os.environ.get("PATH", ""),
                        PYTHONUTF8="1", RETRY_GAP_S="1", AUTOBUILD="0")
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
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def up(self) -> bool:
        try:
            return self.get("/status", 2)[0] == 200
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
        self.bridge = subprocess.Popen([sys.executable, str(self.work / "start.py"), "--background"],
                                       cwd=self.work, env=self.env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=flags)

    def clean_day(self) -> None:
        shutil.rmtree(self.builder / "applications" / DAY, ignore_errors=True)
        shutil.rmtree(self.builder / "logs" / ".run.lock", ignore_errors=True)
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


def ps(py: pathlib.Path, work: pathlib.Path, code: str) -> str:
    """Run a snippet with the copy's venv and platform_util importable."""
    return subprocess.run([str(py), "-c", "import sys; sys.path.insert(0, r'%s')\n%s"
                           % (work / "scraper", textwrap.dedent(code))],
                          capture_output=True, text=True, encoding="utf-8",
                          env=dict(os.environ, PYTHONUTF8="1"), timeout=120).stdout.strip()


# ------------------------------------------------------------------ the tests
def pool_section(py: pathlib.Path, work: pathlib.Path) -> None:
    """
    One job of each kind, through the real rank_ollama.py and pool.py:
      Alpha    judged on an earlier day        -> dropped as a repeat
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
    for f in (sc / "inbox").glob("*.json"):
        f.unlink()
    (sc / "out" / "ollama_cache.json").unlink(missing_ok=True)
    hist = sc / "history"
    hist.mkdir(exist_ok=True)
    desc = "Python machine learning in Berlin, Straße. " * 30
    jobs = [{"url": f"https://www.linkedin.com/jobs/view/{900 + i}", "company": c,
             "title": "ML Engineer", "location": "Berlin", "source": "linkedin",
             "description": "" if c == "Delta" else desc}
            for i, c in enumerate(["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta"])]
    (sc / "inbox" / f"job-collector-{DAY}.json").write_text(json.dumps({"jobs": jobs}),
                                                            encoding="utf-8")
    (hist / "seen.csv").write_text("first_seen,company,title,judged\n"
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
    check(b.get("to_judge") == 3 and b.get("repeat") == 1 and b.get("applied") == 1
          and b.get("no_description") == 1 and b.get("collected") == 6,
          "every collected job in exactly one bucket (3 to judge, 1 repeat, 1 applied, 1 no description)",
          json.dumps(s1)[:400])
    r = subprocess.run([str(py), "rank_ollama_real.py", "--top", "5"], cwd=sc, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=300,
                       env=dict(os.environ, PYTHONUTF8="1"))
    rank = json.loads((sc / "out" / "ollama_rank.json").read_text(encoding="utf-8")) \
        if (sc / "out" / "ollama_rank.json").exists() else {}
    judged = rank.get("counts", {}).get("judged")
    check(r.returncode == 0 and judged == s1.get("usable") == 3,
          "the ranker reads exactly the number the dashboard shows", f"exit {r.returncode}, "
          f"ranker {judged}, dashboard {s1.get('usable')}: {(r.stdout + r.stderr)[-300:]}")
    check(rank.get("not_read") == {"repeat": 1, "applied": 1, "no_description": 1, "too_old": 0},
          "the ranker records why the rest were not read", str(rank.get("not_read")))
    seen = (hist / "seen.csv").read_text(encoding="utf-8")
    beta = next((l for l in seen.splitlines() if l.split(",")[1:2] == ["Beta"]), "")
    check(beta.endswith(DAY), "a role never judged before goes to the model, and is recorded as judged", beta)
    s2 = status()
    check(s2.get("usable") == 3, "ranking the same pool again the same day drops nothing",
          json.dumps(s2.get("breakdown")))

    # A history written before the judged column: filled in once from the cache.
    (hist / ".judged-backfilled").unlink()
    (hist / "seen.csv").write_text("first_seen,company,title\n"
                                   f"{_days_ago(3)},Epsilon,ML Engineer\n"
                                   f"{_days_ago(3)},Omega,ML Engineer\n", encoding="utf-8")
    status()
    seen = (hist / "seen.csv").read_text(encoding="utf-8")
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
    r2 = subprocess.run([str(py), "rank_ollama_real.py", "--top", "5"], cwd=sc,
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=300, env=dict(os.environ, PYTHONUTF8="1"))
    rank = json.loads((sc / "out" / "ollama_rank.json").read_text(encoding="utf-8"))
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
        py = work / "scraper" / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")
        check(py.exists(), "venv created")
        link = work / "builder" / ".venv"
        check((link / ("Scripts/python.exe" if IS_WIN else "bin/python")).exists(),
              "builder/.venv links to the scraper venv")
        if IS_WIN:
            check((work / "scraper" / ".venv" / "bin" / "python").exists(),
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
        for path in ("/dashboard", "/ranking", "/funnel", "/doctor", "/events", "/status"):
            try:
                code = e.get(path, 30)[0]
            except OSError as ex:
                code = str(ex)
            check(code == 200, f"GET {path}", str(code))
        doc = e.get("/doctor")[1]
        claude = next((c for c in doc.get("checks", []) if c["id"] == "claude"), {})
        check(claude.get("ok"), "doctor finds claude", json.dumps(claude))

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
        check(not (e.builder / "logs" / ".run.lock").exists(), "lock released after every run")
        report = e.builder / "applications" / DAY / "batch.html"
        e.run_build("2", "ok")
        check(report.exists() and "Firma Ü" in report.read_text(encoding="utf-8"),
              "report written, umlauts intact")

        code, o = e.run_build("2", "hang", STARTUP_GRACE_MIN="0.05", WATCHDOG_EVERY_S="1")
        log = (e.builder / "logs" / f"{DAY}.log").read_text(encoding="utf-8", errors="replace")
        check(o.get("status") == "failed" and "watchdog: no new documents" in log,
              "watchdog stops a hung Claude, run ends cleanly", str(o))
        left = ps(py, work, f"import platform_util as pu; print(len(pu.find_apply_batch_claudes(r'{e.builder}')))")
        check(left == "0", "no Claude left running after the watchdog", left)

        section("builds run by the bridge")
        e.clean_day()
        e.mode("ok")
        r = e.post("/build", {"top": 2})
        check(r.get("ok"), "POST /build starts a build", str(r))
        end = time.time() + 60
        while time.time() < end and e.get("/status")[1]["build"]["running"]:
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
        e.post("/build", {"top": 2})
        end = time.time() + 30
        found = "0"
        while time.time() < end:
            found = ps(py, work, f"import platform_util as pu; print(len(pu.find_apply_batch_claudes(r'{e.builder}')))")
            if found != "0":
                break
            time.sleep(1)
        check(found != "0", "build reached Claude")
        r = e.post("/build", {"top": 2})
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
        b = e.get("/status")[1]["build"]
        check(b.get("running"), "restarted bridge adopts the running build", str(b))
        r = e.post("/build/stop")
        check(r.get("ok") and not r.get("still_running"), "Stop ends the build", str(r))
        left = ps(py, work, f"""
            import pathlib, platform_util as pu
            b = pathlib.Path(r'{e.builder}')
            print(len(pu.find_apply_batch_claudes(b)) + len(pu.find_script_procs(b / 'run_batch.py')))
        """)
        check(left == "0", "nothing of the build is left running", left)
        check(not (e.builder / "logs" / ".run.lock").exists(), "Stop clears the run lock")
        e.clean_day()
        e.mode("ok")
        r = e.post("/build", {"top": 1})
        check(r.get("ok"), "a new build starts straight after a Stop", str(r))
        end = time.time() + 60
        while time.time() < end and e.get("/status")[1]["build"]["running"]:
            time.sleep(1)

        section("which jobs the ranker reads (real ranker, real pool rules)")
        pool_section(py, work)

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
