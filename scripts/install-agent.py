#!/usr/bin/env python3
"""
Start Job Pipeline automatically at login, and restart it if it crashes.
Optional: without it, run ./start.sh (or start.bat) when you want it.

    macOS     scripts/install-agent.sh            (a launchd agent)
    Windows   scripts\\install-agent.bat           (a per-user Run entry, no admin)
    Linux     scraper/.venv/bin/python scripts/install-agent.py   (systemd --user)

    add --remove to stop it and stop starting at login.

After this the dashboard is always at http://127.0.0.1:8765/dashboard and the
Chrome extension always finds it. Settings in local.env (e.g. AUTOBUILD=1) are
read by the bridge itself, so they apply here too. Output goes to
scraper/out/bridge.log.
"""

from __future__ import annotations

import os
import pathlib
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRAPER = ROOT / "scraper"
sys.path.insert(0, str(SCRAPER))

import bootstrap                                            # noqa: E402
bootstrap.ensure(__file__)                                  # sets up if needed
import platform_util as pu                                  # noqa: E402

PORT = "8765"
LOG = SCRAPER / "out" / "bridge.log"


def _port() -> str:
    env = ROOT / "local.env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("BRIDGE_PORT="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("BRIDGE_PORT", PORT)


def answering() -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{_port()}/status", timeout=2).close()
        return True
    except OSError:
        return False


def wait_and_report() -> int:
    for _ in range(15):
        if answering():
            print(f"Running, and it will start at every login: "
                  f"http://127.0.0.1:{_port()}/dashboard")
            return 0
        time.sleep(1)
    print(f"Installed, but it is not answering yet. Its log: {LOG}")
    print(f"If another copy already uses port {_port()}, stop that one first.")
    return 0


# ------------------------------------------------------------------ macOS
LABEL = "com.jobpipeline.bridge"


def mac(remove: bool) -> int:
    plist = pathlib.Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
    if remove:
        plist.unlink(missing_ok=True)
        print("Removed. Start it by hand with ./start.sh from now on.")
        return 0
    py = pu.venv_python(SCRAPER / ".venv")
    plist.parent.mkdir(parents=True, exist_ok=True)
    home = pathlib.Path.home()
    with open(plist, "wb") as fh:
        plistlib.dump({
            "Label": LABEL,
            "ProgramArguments": [str(py), str(SCRAPER / "serve.py")],
            "WorkingDirectory": str(SCRAPER),
            "RunAtLoad": True,
            # Restart after a crash, not after a clean exit (a clean exit means
            # the port was already taken by another copy; retrying would loop).
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 10,
            "StandardOutPath": str(LOG),
            "StandardErrorPath": str(LOG),
            "EnvironmentVariables": {
                "PYTHONUNBUFFERED": "1",
                # A login agent gets a bare PATH; builds need ollama and claude.
                "PATH": f"{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:"
                        "/usr/bin:/bin:/usr/sbin:/sbin",
            },
        }, fh)
    subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
    return wait_and_report()


# ------------------------------------------------------------------ Windows
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "JobPipeline"


def _supervisors() -> list[int]:
    """The background start.py this installs (and the bridge under it)."""
    target = str(ROOT / "start.py")
    out = []
    for pid in pu.find_procs("start.py"):
        args = pu.cmdline(pid)
        if "--background" in args and any(pathlib.Path(a).resolve() == pathlib.Path(target)
                                           for a in args if a.endswith("start.py")):
            out.append(pid)
    return out


def windows(remove: bool) -> int:
    # A per-user Run entry rather than Task Scheduler: an at-logon task needs
    # admin rights to create, and the Run key does not. The restart-after-a-
    # crash that launchd gives on a Mac comes from start.py's own loop.
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
        if remove:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass
            for pid in _supervisors():
                pu.kill_tree(pid)
            print("Removed. Start it by hand with start.bat from now on.")
            return 0
        pyw = pu.venv_python(SCRAPER / ".venv", windowless=True)
        if not pyw.exists():
            pyw = pu.venv_python(SCRAPER / ".venv")
        command = f'"{pyw}" "{ROOT / "start.py"}" --background'
        winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, command)
    if not answering():
        # Start it now too, so there is no need to log out and back in.
        subprocess.Popen([str(pyw), str(ROOT / "start.py"), "--background"],
                         cwd=str(ROOT), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         **pu.detach_kwargs())
    return wait_and_report()


# ------------------------------------------------------------------ Linux
UNIT = "job-pipeline.service"


def linux(remove: bool) -> int:
    unit = pathlib.Path.home() / ".config" / "systemd" / "user" / UNIT
    if not shutil.which("systemctl"):
        return linux_autostart(remove)
    subprocess.run(["systemctl", "--user", "disable", "--now", UNIT], capture_output=True)
    if remove:
        unit.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        print("Removed. Start it by hand with ./start.sh from now on.")
        return 0
    py = pu.venv_python(SCRAPER / ".venv")
    home = pathlib.Path.home()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "[Unit]\nDescription=Job Pipeline bridge\n\n"
        "[Service]\n"
        f'WorkingDirectory={SCRAPER}\nExecStart="{py}" "{SCRAPER / "serve.py"}"\n'
        # Restart after a crash, not after a clean exit (port already taken).
        "Restart=on-failure\nRestartSec=10\n"
        f"Environment=PYTHONUNBUFFERED=1 PYTHONUTF8=1\n"
        f"Environment=PATH={home}/.local/bin:/usr/local/bin:/usr/bin:/bin\n"
        f"StandardOutput=append:{LOG}\nStandardError=append:{LOG}\n\n"
        "[Install]\nWantedBy=default.target\n", encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", UNIT], check=True)
    return wait_and_report()


def linux_autostart(remove: bool) -> int:
    """No systemd: the desktop's autostart folder, the XDG standard."""
    entry = pathlib.Path.home() / ".config" / "autostart" / "job-pipeline.desktop"
    if remove:
        entry.unlink(missing_ok=True)
        print("Removed. Start it by hand with ./start.sh from now on.")
        return 0
    py = pu.venv_python(SCRAPER / ".venv")
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("[Desktop Entry]\nType=Application\nName=Job Pipeline\n"
                     f'Exec="{py}" "{ROOT / "start.py"}" --background\n'
                     "X-GNOME-Autostart-enabled=true\nNoDisplay=true\n", encoding="utf-8")
    if not answering():
        subprocess.Popen([str(py), str(ROOT / "start.py"), "--background"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **pu.detach_kwargs())
    return wait_and_report()


def main() -> int:
    remove = "--remove" in sys.argv
    if not remove and not pu.venv_python(SCRAPER / ".venv").exists():
        print(f"Not set up yet. Run {pu.install_hint('setup')} first.")
        return 1
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if pu.IS_MAC:
        return mac(remove)
    if pu.IS_WIN:
        return windows(remove)
    return linux(remove)


if __name__ == "__main__":
    raise SystemExit(main())
