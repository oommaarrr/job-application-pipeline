#!/usr/bin/env python3
"""
Start Job Pipeline and open the dashboard. The same on macOS, Linux, Windows.

    ./start.sh                 macOS / Linux
    start.bat                  Windows
    python start.py            anywhere (the venv's python)
    python start.py --background
                               no browser, output to scraper/out/bridge.log.
                               What the Windows and Linux login entries run.

Keep the window open while you use it. Press Ctrl+C there to stop.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
SCRAPER = ROOT / "scraper"
sys.path.insert(0, str(SCRAPER))


def load_local_env() -> None:
    """Personal settings (gitignored), e.g. BRIDGE_PORT or AUTOBUILD=1."""
    f = ROOT / "local.env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip().removeprefix("export ").strip(),
                              v.strip().strip('"').strip("'"))


def ask_yes(question: str) -> bool:
    try:
        a = input(f"{question} [Y/n] ").strip().lower()
    except EOFError:
        return False
    return not a or a.startswith("y")


def remember_no_login_agent() -> None:
    """A "no" to start-at-login, kept in local.env so it is not asked again."""
    f = ROOT / "local.env"
    try:
        text = f.read_text(encoding="utf-8") if f.exists() else ""
        if "START_AT_LOGIN=" not in text:
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(("" if not text or text.endswith("\n") else "\n")
                         + "# Asked once; set to 1 (or delete) to be asked again.\n"
                         + "START_AT_LOGIN=0\n")
    except OSError:
        pass
    os.environ["START_AT_LOGIN"] = "0"


def agent_hint() -> str:
    return (r"scripts\install-agent.bat" if os.name == "nt"
            else "scripts/install-agent.sh" if sys.platform == "darwin"
            else "scraper/.venv/bin/python scripts/install-agent.py")


def answering(port: str) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2).close()
        return True
    except OSError:
        return False


def main() -> int:
    background = "--background" in sys.argv
    load_local_env()
    port = os.environ.get("BRIDGE_PORT", "8765")
    url = f"http://127.0.0.1:{port}/dashboard"
    # Any Python will do: the first run sets the project up by itself, and an
    # outdated environment is brought up to date, then this continues inside it.
    import bootstrap
    bootstrap.ensure(__file__)
    import platform_util as pu
    py = pu.venv_python(SCRAPER / ".venv")

    # A tool still missing (Git on Windows, Ollama, Claude Code)? With someone
    # at the window, offer setup again rather than leave it to the Setup panel
    # and a command typed by hand. Each question can still be answered no.
    missing = [n for n, ok in (("Git for Windows", not pu.IS_WIN or pu.find_git_bash()),
                               ("Ollama", pu.find_ollama()),
                               ("Claude Code", pu.find_claude())) if not ok]
    if missing and not background and sys.stdin is not None and sys.stdin.isatty():
        print(f"Not installed yet: {', '.join(missing)}. Running setup first.", flush=True)
        subprocess.call([str(py), str(ROOT / "setup.py"), "--skip-model", "--from-start"])
        load_local_env()                     # setup may have recorded an answer

    # The pipeline is a small local server: the dashboard is served by it and
    # the extension talks to it, so something has to keep it running. Starting
    # at login does that, and then this command is never needed again. Offered
    # every time until it is on, or until it is turned down once (START_AT_LOGIN=0
    # in local.env, which setup and this both write).
    if not background and sys.stdin is not None and sys.stdin.isatty() \
            and os.environ.get("START_AT_LOGIN", "").strip() != "0":
        agent = [str(py), str(ROOT / "scripts" / "install-agent.py")]
        if subprocess.call(agent + ["--status"], stdout=subprocess.DEVNULL) != 0:
            if ask_yes("Start Job Pipeline by itself at every login, in the background, so "
                       "the dashboard and the extension always work and you never need "
                       "to run this again?"):
                if subprocess.call(agent) == 0:
                    print(f"Opening {url}")
                    pu.open_path(url)
                    return 0
            else:
                remember_no_login_agent()
                print("OK. Run this again whenever you want it, or turn on start-at-login "
                      f"later with {agent_hint()}")

    # Already running (another window, or the login agent)? Just open it.
    if answering(port):
        if not background:
            print(f"Job Pipeline is already running. Opening {url}")
            pu.open_path(url)
        return 0

    log = None
    if background:
        (SCRAPER / "out").mkdir(exist_ok=True)
        log = open(SCRAPER / "out" / "bridge.log", "a", encoding="utf-8")
    else:
        print(f"Starting Job Pipeline on {url}")
        print("Keep this window open. Press Ctrl+C to stop.")
        threading.Timer(2.0, pu.open_path, args=(url,)).start()

    env = pu.utf8_env(dict(os.environ, PYTHONUNBUFFERED="1"))
    # If the bridge crashes, bring it back instead of leaving the dashboard
    # dead. Nothing is lost in a restart: every step's state lives on disk, and
    # a build or rank that was running carries on and is picked up again. A
    # clean exit (Ctrl+C, or the port already taken by another copy) ends it.
    restarts = 0
    while True:
        began = time.time()
        proc = subprocess.Popen([str(py), "serve.py"], cwd=SCRAPER, env=env,
                                stdout=log, stderr=subprocess.STDOUT if log else None,
                                **(pu.quiet_kwargs() if background else {}))
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            # The console sent Ctrl+C to the bridge as well; let it finish.
            try:
                proc.wait(timeout=10)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                proc.kill()
            return 0
        # 130 is Ctrl+C on POSIX; 0xC000013A is the same on Windows.
        if code in (0, 130, 3221225786, -2):
            return 0
        # Only quick crashes count: a bridge that ran for an hour and then
        # died is worth restarting however many times that happens.
        restarts = restarts + 1 if time.time() - began < 60 else 1
        if restarts > 5:
            print(f"The bridge keeps crashing (exit {code}). The error is above; "
                  "nothing on disk was lost.", file=log or sys.stdout, flush=True)
            return code
        print(f"The bridge stopped unexpectedly (exit {code}). Restarting in 3 seconds "
              f"({restarts}/5)...", file=log or sys.stdout, flush=True)
        time.sleep(3)


if __name__ == "__main__":
    raise SystemExit(main())
