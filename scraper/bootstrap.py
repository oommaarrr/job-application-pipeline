"""
Make any entry point work with whatever Python it was started from.

A user who types `python serve.py` (or `python start.py`) on a fresh download
gets the system Python, which has none of the pipeline's packages. Until
25 September 2026 that ended in `ModuleNotFoundError: No module named 'psutil'`
and a dashboard that said "offline — run serve.py", which is what they had
just done. Now the entry point notices, sets the project up the first time
(the same setup.py, without questions), and restarts itself inside the
project's own environment.

Standard library only: this runs BEFORE anything third party can be imported.

    import bootstrap; bootstrap.ensure(__file__)     # first lines of a script
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
VENV = REPO / "scraper" / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
# What the pipeline imports at startup. If any is missing, the environment is
# out of date (an older checkout, a half-finished install) and setup fixes it.
NEEDED = ("psutil", "requests", "bs4")


def _in_venv() -> bool:
    try:
        return pathlib.Path(sys.prefix).resolve() == VENV.resolve()
    except OSError:
        return False


def _has_packages() -> bool:
    import importlib.util
    return all(importlib.util.find_spec(m) is not None for m in NEEDED)


def _setup() -> None:
    print("Setting up Job Pipeline (first run, or the code was updated). "
          "This takes a minute or two…", flush=True)
    base = sys.executable if not _in_venv() else getattr(sys, "_base_executable", sys.executable)
    # --skip-model: the bridge downloads the model in the background instead,
    # so the first start is not held up by a multi-GB download.
    # --from-start: we are about to start it anyway, so setup does not offer to.
    # Setup still ASKS about installing Git, Ollama and Claude Code, signing in
    # and the profile whenever a person is at this window (it checks that
    # itself); started in the background, it only checks.
    code = subprocess.call([base, str(REPO / "setup.py"), "--skip-model", "--from-start"])
    if code != 0 or not PY.exists():
        print("\nSetup did not finish; the reason is above. Fix it, then run this again.",
              flush=True)
        raise SystemExit(code or 1)


def ensure(script: str) -> None:
    """Return only when running inside the project's environment with every
    package present. Otherwise set it up, run `script` there, and exit with its
    exit code."""
    if _in_venv() and _has_packages():
        return
    if not PY.exists() or not _venv_ok():
        _setup()
    proc = subprocess.Popen([str(PY), os.path.abspath(script), *sys.argv[1:]])
    try:
        code = proc.wait()
    except KeyboardInterrupt:
        # Ctrl+C reached the child too; give it a moment to stop cleanly.
        try:
            code = proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            proc.kill()
            code = 130
    raise SystemExit(code)


def _venv_ok() -> bool:
    """Does the project's environment start and have every package?"""
    probe = "import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(m) " \
            f"for m in {NEEDED!r}) else 1)"
    try:
        return subprocess.call([str(PY), "-c", probe], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=60) == 0
    except (OSError, subprocess.SubprocessError):
        return False
