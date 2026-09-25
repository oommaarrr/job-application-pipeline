#!/usr/bin/env python3
"""
One-shot setup, the same on macOS, Linux and Windows. Safe to re-run: every
step checks before it acts.

    ./setup.sh          macOS / Linux
    setup.bat           Windows (double-click, or run it in a terminal)
    python setup.py     anywhere

    --skip-model        do not download the Ollama model (used by the self-test)

Uses only the standard library until the venv exists, because it is what
creates the venv.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
SCRAPER = ROOT / "scraper"
VENV = SCRAPER / ".venv"
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

PLAIN = IS_WIN and not sys.stdout.isatty()
if IS_WIN:
    os.system("")               # turns on colour codes in the Windows console
    for stream in (sys.stdout, sys.stderr):
        try:
            # Redirected on Windows, whatever reads the output decodes it with
            # the console code page, so plain ASCII is the only safe choice.
            stream.reconfigure(encoding="ascii" if PLAIN else "utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_ASCII = str.maketrans({"—": "-", "→": "->", "✓": "ok", "✗": "x"})


if PLAIN:
    _print = print

    def print(*args, **kw):                                  # noqa: A001
        _print(*(str(a).translate(_ASCII) for a in args), **kw)


def say(s: str) -> None:
    print(f"\n{s}" if PLAIN else f"\n\033[1m{s}\033[0m")


# Plain ASCII when the output goes to a file or a pipe on Windows: whatever
# reads it there decodes with the console code page, and ✓ turns into Γ£ô.
def ok(s: str) -> None:
    print(f"  [ok] {s}" if PLAIN else f"  \033[32m✓\033[0m {s}")


def no(s: str) -> None:
    print(f"  [!!] {s}" if PLAIN else f"  \033[31m✗\033[0m {s}")


def venv_python() -> pathlib.Path:
    return VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")


def run(args, **kw) -> subprocess.CompletedProcess:
    sys.stdout.flush()          # keep our lines in order with the child's
    return subprocess.run([str(a) for a in args], **kw)


def main() -> int:
    if sys.version_info < (3, 11):
        no(f"Python {sys.version.split()[0]} is too old — install Python 3.11 or newer")
        return 1

    py = venv_python()
    if "--in-venv" in sys.argv:
        return after_venv(py)

    say("1. Python environment")
    if py.exists() and run([py, "-c", "import sys"], capture_output=True).returncode != 0:
        # A venv whose base Python was removed or upgraded away. Rebuilding is
        # cheap and it is the only fix.
        no("scraper/.venv is broken (its Python is gone) — rebuilding it")
        shutil.rmtree(VENV, ignore_errors=True)
    if not py.exists():
        run([sys.executable, "-m", "venv", VENV], check=True)
        ok("created scraper/.venv")
    else:
        ok("scraper/.venv already exists")
    run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=False)
    for req in (SCRAPER / "requirements.txt", ROOT / "builder" / "requirements.txt"):
        if run([py, "-m", "pip", "install", "--quiet", "-r", req]).returncode != 0:
            no(f"installing {req.relative_to(ROOT)} failed — the error is above")
            return 1
    ok("dependencies installed")
    # The rest needs psutil, which is in the venv and not necessarily in the
    # Python that started this script, so carry on inside the venv.
    return run([py, __file__, "--in-venv"] + sys.argv[1:]).returncode


def after_venv(py: pathlib.Path) -> int:
    # One environment for both halves. The build runs `.venv/bin/python` from
    # inside builder/, so give it the same interpreter rather than a second
    # install: a symlink here, a junction on Windows (no admin rights needed).
    sys.path.insert(0, str(SCRAPER))
    import platform_util as pu
    pu.link_dir(VENV, ROOT / "builder" / ".venv", relative=True)
    ok("builder/.venv linked to the same environment")
    if IS_WIN:
        # Claude Code runs its commands in Git Bash on Windows, and every
        # instruction it follows says `.venv/bin/python`. A venv on Windows has
        # Scripts\python.exe instead, so add the path those instructions use.
        shim = VENV / "bin" / "python"
        shim.parent.mkdir(exist_ok=True)
        shim.write_bytes(b'#!/bin/sh\nexec "$(dirname "$0")/../Scripts/python.exe" "$@"\n')
        ok("added .venv/bin/python for Git Bash")

    say("2. Local model (Ollama)")
    ollama = pu.find_ollama()
    if not ollama:
        no("ollama not installed — https://ollama.com/download")
        no("the ranker cannot run without it")
    else:
        ok("ollama is installed")
        try:
            import config
            model = getattr(config, "OLLAMA_MODEL", "llama3.1")
        except Exception:                                  # noqa: BLE001
            model = "llama3.1"
        listed = run([ollama, "list"], capture_output=True, text=True,
                     encoding="utf-8", errors="replace")
        names = [line.split()[0] for line in listed.stdout.splitlines()[1:] if line.split()]
        want = model.split(":")[0]
        if any(n == model or n.split(":")[0] == want for n in names):
            ok(f"model '{model}' is present")
        elif "--skip-model" in sys.argv:
            print(f"  - model '{model}' not pulled (--skip-model)")
        elif listed.returncode != 0:
            no("Ollama is installed but not running. Open the Ollama app, then run setup again")
        else:
            print(f"  pulling {model} (this is a few GB, once)…")
            if run([ollama, "pull", model]).returncode == 0:
                ok(f"pulled {model}")

    say("   PDF optimiser (optional)")
    if shutil.which("qpdf"):
        ok("qpdf found")
    else:
        how = ("winget install qpdf.qpdf" if IS_WIN else "brew install qpdf" if IS_MAC
               else "sudo apt install qpdf")
        print("  - qpdf not installed. Documents still build; install it for smaller,")
        print(f"    faster-loading PDFs:  {how}")

    say("3. Your profile")
    me = ROOT / "profiles" / "me"
    if not me.is_dir():
        shutil.copytree(ROOT / "profiles" / "example", me)
        ok("created profiles/me (a copy of the example — it is not you yet)")
        cv = r"C:\path\to\your-cv.pdf" if IS_WIN else "~/path/to/your-cv.pdf"
        print("  Next, make it yours. Easiest: let Claude read your current CV:")
        print(f'      claude "/make-profile {cv}"')
        print("  Or edit profiles/me/identity.md and profiles/me/profile.md by hand.")
    else:
        ok("profiles/me already exists")

    say("4. Chrome extension")
    print("  Load it by hand, once:")
    print("    1. open chrome://extensions")
    print("    2. turn on Developer mode (top right)")
    print(f"    3. Load unpacked → {ROOT / 'extension'}")

    say("5. Claude Code CLI (only needed to WRITE documents)")
    claude = pu.find_claude()
    if claude:
        ok(f"claude found: {claude}")
    else:
        no(f"not installed — {pu.install_hint('claude')}")
    if IS_WIN:
        if pu.find_git_bash():
            ok("Git Bash found (Claude Code needs it on Windows)")
        else:
            no(f"Git for Windows not found — Claude Code needs it: {pu.install_hint('git-bash')}")

    say("Done. Start it with:")
    print(f"    {pu.install_hint('start')}")
    print("  It opens the dashboard in your browser. Work down the Setup panel there.")
    agent = r"scripts\install-agent.bat" if IS_WIN else (
        "scripts/install-agent.sh" if IS_MAC else "scripts/install-agent.py")
    print(f"  Optional: start it automatically at login with {agent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
