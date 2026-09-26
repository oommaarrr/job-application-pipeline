#!/usr/bin/env python3
"""
One-shot setup, the same on macOS, Linux and Windows. Safe to re-run: every
step checks before it acts.

    ./setup.sh          macOS / Linux
    setup.bat           Windows (double-click, or run it in a terminal)
    python setup.py     anywhere

    --skip-model        do not download the Ollama model (used by the self-test)
    --no-prompt         never ask anything; only check and report (CI, scripts)
    --from-start        run by start.py on a first start: do not offer to start it

Setup offers to install what is missing (Ollama, Claude Code, Git for Windows),
signs you in to Claude and creates your profile from your CV. It asks before
each of those, and every one can be skipped and done later by hand.

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


# Asking is only possible with a person at the keyboard. On Windows `< nul`
# still looks like a terminal, so the flag and CI are checked explicitly.
PROMPT = ("--no-prompt" not in sys.argv and not os.environ.get("CI")
          and sys.stdin is not None and sys.stdin.isatty())


def ask(question: str, default_yes: bool = True) -> bool:
    if not PROMPT:
        return False
    try:
        a = input(f"  {question} [{'Y/n' if default_yes else 'y/N'}] ").strip().lower()
    except EOFError:
        return False
    return default_yes if not a else a.startswith("y")


def install(what: str, windows: list[str] | None = None, posix: str | None = None) -> bool:
    """Run the official installer for a missing tool, after asking."""
    cmd = windows if IS_WIN else (["bash", "-c", posix] if posix else None)
    if not cmd or not ask(f"{what} is not installed. Install it now?"):
        return False
    print(f"  installing {what}…")
    return run(cmd).returncode == 0


def add_to_path(folder: pathlib.Path) -> None:
    """Make a freshly installed tool callable by name from new terminals.

    The Claude Code installer puts claude.exe in %USERPROFILE%\\.local\\bin
    and does not always put that folder on PATH, so `claude` was "not
    recognised" right after installing it, and the natural reaction was to
    install it again. Windows: the per-user PATH in the registry (no admin).
    macOS/Linux: printed, because editing someone's shell profile silently is
    worse than one line of instructions."""
    folder = folder.resolve()
    here = [pathlib.Path(p).resolve() for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if folder in here:
        return
    os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
    if not IS_WIN:
        print(f"  - {folder} is not on your PATH. Add this line to ~/.zshrc or ~/.bashrc:")
        print(f'      export PATH="{folder}:$PATH"')
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        try:
            cur, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            cur, kind = "", winreg.REG_EXPAND_SZ
        parts = [x for x in cur.split(";") if x]
        if any(os.path.normcase(os.path.expandvars(x).rstrip("\\")) ==
               os.path.normcase(str(folder)) for x in parts):
            return
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(parts + [str(folder)]))
    try:                                      # tell open programs PATH changed
        import ctypes
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 2, 5000, None)
    except Exception:                                        # noqa: BLE001
        pass
    ok(f"added {folder} to your PATH (new terminal windows will find it)")


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
        install("Ollama",
                windows=["winget", "install", "-e", "--id", "Ollama.Ollama", "--source", "winget",
                         "--accept-package-agreements", "--accept-source-agreements"],
                posix=("brew install ollama" if IS_MAC and shutil.which("brew")
                       else None if IS_MAC else "curl -fsSL https://ollama.com/install.sh | sh"))
        ollama = pu.find_ollama()
    if not ollama:
        no("ollama not installed — https://ollama.com/download")
        no("the ranker cannot run without it")
    else:
        ok("ollama is installed")
        import ollama_model as om
        try:
            import config
        except Exception:                                  # noqa: BLE001
            config = None
        model = om.wanted(config)
        url = getattr(config, "OLLAMA_URL", om.DEFAULT_URL)
        entries = om.tags(url)
        # An already-installed model is used as it is: nothing is downloaded
        # when Ollama has one (scraper/ollama_model.py).
        chosen = om.resolve(config, entries) if entries is not None else None
        if chosen == model:
            ok(f"model '{model}' is present")
        elif chosen:
            ok(f"using '{chosen}', already installed in Ollama (nothing to download)")
            print("    To use another, set OLLAMA_MODEL in scraper/config_local.py")
        elif "--skip-model" in sys.argv:
            print(f"  - model '{model}' not pulled (--skip-model)")
        elif entries is None:
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

    say("3. Claude Code (writes the documents)")
    claude = claude_step(pu)

    say("4. Your profile")
    profile_step(claude)

    say("5. Chrome extension (optional)")
    print("  Load it by hand, once:")
    print("    1. open chrome://extensions")
    print("    2. turn on Developer mode (top right)")
    print(f"    3. Load unpacked → {ROOT / 'extension'}")

    # Offer to keep it running, so the extension never finds it offline, or at
    # least to start it now. Both optional; both can be done later.
    agent_cmd = [str(py), str(ROOT / "scripts" / "install-agent.py")]
    from_start = "--from-start" in sys.argv
    # The dashboard and the extension only work while it runs, so this is the
    # answer that makes ./start.sh (start.bat) a one-time thing.
    if ask("Start Job Pipeline by itself every time you log in (in the background), "
           "so you never have to start it by hand?"):
        if run(agent_cmd).returncode == 0:
            return 0
    else:
        if PROMPT:
            _remember_no_login_agent()
        if not from_start and ask("Start it now?"):
            return run([str(py), str(ROOT / "start.py")]).returncode
    if from_start:
        return 0

    say("Done. Start it with:")
    print(f"    {pu.install_hint('start')}")
    print("  It opens the dashboard in your browser. Work down the Setup panel there.")
    agent = r"scripts\install-agent.bat" if IS_WIN else (
        "scripts/install-agent.sh" if IS_MAC else "scripts/install-agent.py")
    print(f"  Optional: start it automatically at login with {agent}")
    return 0


def _remember_no_login_agent() -> None:
    """Kept in local.env, so start.py does not ask the same question again."""
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


def claude_step(pu) -> str | None:
    """Git Bash (Windows), then Claude Code on PATH, then signed in."""
    if IS_WIN and not pu.find_git_bash():
        # Claude Code runs every command through Git Bash on Windows; without
        # it claude will not even start, so this comes first.
        install("Git for Windows",
                windows=["winget", "install", "-e", "--id", "Git.Git", "--source", "winget",
                         "--accept-package-agreements", "--accept-source-agreements"])
        if pu.find_git_bash():
            ok("Git Bash found")
        else:
            no(f"Git for Windows not found — Claude Code needs it: {pu.install_hint('git-bash')}")
            return None
    elif IS_WIN:
        ok("Git Bash found (Claude Code needs it on Windows)")

    claude = pu.find_claude()
    if not claude:
        install("Claude Code",
                windows=["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                         "irm https://claude.ai/install.ps1 | iex"],
                posix="curl -fsSL https://claude.ai/install.sh | bash")
        claude = pu.find_claude()
    if not claude:
        no(f"not installed — {pu.install_hint('claude')}")
        return None
    ok(f"claude found: {claude}")
    add_to_path(pathlib.Path(claude).parent)

    signed = None
    try:
        out = run([claude, "auth", "status"], capture_output=True, text=True,
                  encoding="utf-8", errors="replace", timeout=30).stdout
        import json
        signed = bool(json.loads(out).get("loggedIn"))
    except Exception:                                        # noqa: BLE001
        pass
    if signed:
        ok("signed in to Claude")
    elif ask("Sign in to Claude now? (opens your browser)"):
        run([claude, "auth", "login"])
    else:
        no("not signed in yet — run: claude auth login")
    return claude


def profile_step(claude: str | None) -> None:
    """Create profiles/me, and fill it from the user's CV if they want."""
    me, example = ROOT / "profiles" / "me", ROOT / "profiles" / "example"
    if not me.is_dir():
        shutil.copytree(example, me)
        ok("created profiles/me (a copy of the example — it is not you yet)")
    still_example = all((me / n).is_file() and (example / n).is_file()
                        and (me / n).read_bytes() == (example / n).read_bytes()
                        for n in ("profile.md", "identity.md"))
    if not still_example:
        ok("profiles/me is filled in")
        return
    if claude and PROMPT:
        print("  Claude can fill it in from your current CV (PDF, Word or text).")
        try:
            cv = input("  Path to your CV (drag the file here), or Enter to skip: ")
        except EOFError:
            cv = ""
        cv = cv.strip().strip('"').strip("'")
        if cv and pathlib.Path(os.path.expanduser(cv)).is_file():
            run([claude, f"/make-profile {os.path.expanduser(cv)}"], cwd=ROOT)
            return
        if cv:
            no(f"no file at {cv}")
    example_cv = r"C:\Users\you\Downloads\my-cv.pdf" if IS_WIN else "~/Downloads/my-cv.pdf"
    print("  Make it yours later: in this folder run")
    print(f'      claude "/make-profile {example_cv}"')
    print("  Or edit profiles/me/identity.md and profiles/me/profile.md by hand.")


if __name__ == "__main__":
    raise SystemExit(main())
