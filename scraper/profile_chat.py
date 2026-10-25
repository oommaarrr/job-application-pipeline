"""
Change the profile by telling Claude what to change, from the dashboard.

October 2026: adding a project or a new job meant finding the right file in
profiles/me, the right section in it, and keeping identity.md, profile.md and
reference/PROJECTS.md in step by hand. Now the dashboard's Edit profile window
sends what you type to Claude (Sonnet, through the Claude Code sign-in the build
already uses), which edits the files itself and says what it changed.

  - Claude runs in the profile folder with file tools only (no shell), and its
    edits are accepted inside that folder only.
  - Every message continues the same conversation (--resume), so "and add the
    dates" after "add my thesis project" works.
  - Before each message the profile's text files are copied, so the window can
    show exactly what changed, file by file, and Undo puts the last change back.
  - The fictional example profile is never edited: it ships with the code.

One message at a time. Standard library only.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import pathlib
import re
import subprocess
import threading
import uuid

import config
import platform_util as pu
from profile_dir import REPO, profile_dir

MODEL = getattr(config, "CLAUDE_PROFILE_MODEL", "claude-sonnet-5-5")
TIMEOUT = getattr(config, "CLAUDE_PROFILE_TIMEOUT", 300)
TEXT = {".md", ".txt", ".json", ".csv", ".yaml", ".yml", ".toml"}
MAX_FILE = 512 * 1024            # larger "text" files are not snapshotted or shown

AUTH_RE = re.compile(r"OAuth access token|authentication_error|Failed to authenticate|"
                     r"Invalid API key|Please run /login|Not logged in", re.I)
LIMIT_RE = re.compile(r"hit your limit|usage limit|rate limit|quota", re.I)

SYSTEM = """\
You keep one person's job-application profile up to date. The files are in the
current folder:

- profile.md: what the job ranker judges every posting against. Target role,
  location, languages, a short experience summary, strengths, what is not their
  lane, and the hard rules.
- identity.md: the facts the CV builder copies word for word. Name, contact
  line, Employment history (employer, title, city, dates) and Education.
- reference/PROJECTS.md: one table row per project. What it proves, with
  numbers, and which kind of role it should lead for.
- reference/projects/*.md: longer write-ups, one file per project.
- Any other file here belongs to the profile too; read it before relying on it.

How to work:
- Change only what the person asks for. Do not reword, reorder or tidy
  anything else.
- Put each fact everywhere it belongs, and keep the files consistent: a new job
  goes into identity.md's Employment history, and into profile.md's Experience
  when it changes the summary. A new project gets a row in PROJECTS.md, and a
  file in reference/projects/ when there is more to say than one row.
- Keep the existing headings, order and table columns: the ranker and the CV
  builder read them.
- Never invent a number, date, employer, tool or result. If something the files
  need is missing, ask one short question and change nothing until it is answered.
- Only the files in this folder. Never anything outside it.

When you are done, answer in one to four short sentences: which file changed,
and what you added or changed. No preamble."""


class ChatError(Exception):
    pass


_lock = threading.Lock()
_state: dict = {"running": False, "session": None, "messages": [], "error": "",
                "started_at": None}
_undo: list[dict] = []            # one entry per message that changed files, newest last


# ------------------------------------------------------------------ files
def _editable(folder: pathlib.Path) -> str:
    """Why this profile cannot be edited here, or "" when it can."""
    if folder.resolve() == (REPO / "profiles" / "example").resolve():
        return ("the active profile is the fictional example, which ships with the code. "
                "Make your own first: in the project folder run "
                "claude \"/make-profile path/to/your-cv.pdf\", then restart the pipeline.")
    if not folder.is_dir():
        return f"there is no profile folder at {folder}"
    return ""


def snapshot(folder: pathlib.Path) -> dict[str, str]:
    """Every text file in the profile, {relative path: content}."""
    out: dict[str, str] = {}
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in TEXT or any(
                part.startswith(".") for part in p.relative_to(folder).parts):
            continue
        try:
            if p.stat().st_size > MAX_FILE:
                continue
            out[p.relative_to(folder).as_posix()] = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return out


def changes(before: dict[str, str], after: dict[str, str]) -> list[dict]:
    """What a message did, per file: added, changed or removed, with a diff."""
    out = []
    for rel in sorted(set(before) | set(after)):
        a, b = before.get(rel), after.get(rel)
        if a == b:
            continue
        diff = "".join(difflib.unified_diff(
            (a or "").splitlines(keepends=True), (b or "").splitlines(keepends=True),
            fromfile=rel, tofile=rel, n=2))
        plus = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        minus = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        out.append({"file": rel, "kind": "added" if a is None else "removed" if b is None else "changed",
                    "plus": plus, "minus": minus, "diff": diff[:20000]})
    return out


# ------------------------------------------------------------------ Claude
def _call(claude: str, folder: pathlib.Path, message: str, session: str, resume: bool) -> dict:
    cmd = [claude, "-p", "--model", MODEL, "--output-format", "json",
           "--tools", "Read,Edit,Write,Glob,Grep",
           "--permission-mode", "acceptEdits",
           "--strict-mcp-config", "--disable-slash-commands",
           "--append-system-prompt", SYSTEM,
           "--max-turns", "40"]
    cmd += ["--resume", session] if resume else ["--session-id", session]
    try:
        # The message on stdin: it can be longer than Windows allows on a
        # command line (a pasted project write-up).
        p = subprocess.run(cmd, input=message, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=folder,
                           timeout=TIMEOUT, **pu.quiet_kwargs())
    except subprocess.TimeoutExpired:
        raise ChatError(f"Claude did not finish within {TIMEOUT // 60} minutes. Any edits it "
                        "made so far are shown below; ask again to carry on.")
    out = (p.stdout or "") + (p.stderr or "")
    try:
        env = json.loads(p.stdout)
    except ValueError:
        env = {"is_error": True, "result": out}
    text = str(env.get("result") or "")
    if env.get("is_error") or p.returncode != 0:
        blob = text + "\n" + out
        if AUTH_RE.search(blob):
            raise ChatError("Claude is signed out. In a terminal run: claude auth login")
        if LIMIT_RE.search(blob):
            raise ChatError("Claude's usage limit is reached. Try again after it resets.")
        raise ChatError("Claude stopped with an error: " + (text or out).strip()[-300:])
    return {"text": text.strip(), "session": env.get("session_id") or session}


def _turn(message: str) -> None:
    folder = profile_dir()
    done: list[dict] = []
    try:
        claude = pu.find_claude()
        if not claude:
            raise ChatError("Claude Code is not installed. See Setup on the dashboard.")
        before = snapshot(folder)
        with _lock:
            session, resume = _state["session"], _state["session"] is not None
            if not session:
                session = str(uuid.uuid4())
        try:
            answer = _call(claude, folder, message, session, resume)
        finally:
            # Edits made before a timeout or an error are real: show them.
            done = changes(before, snapshot(folder))
            if done:
                with _lock:
                    _undo.append({"before": {c["file"]: before.get(c["file"]) for c in done},
                                  "after": {c["file"]: _read(folder / c["file"]) for c in done}})
        with _lock:
            _state["session"] = answer["session"]
            _state["messages"].append({"role": "claude", "text": answer["text"] or "Done.",
                                       "changes": done, "at": _now()})
            _state["error"] = ""
    except ChatError as e:
        with _lock:
            _state["error"] = str(e)
            _state["messages"].append({"role": "error", "text": str(e),
                                       "changes": done, "at": _now()})
    except Exception as e:                                  # noqa: BLE001
        with _lock:
            _state["error"] = f"{type(e).__name__}: {e}"[:300]
            _state["messages"].append({"role": "error", "text": _state["error"],
                                       "changes": [], "at": _now()})
    finally:
        with _lock:
            _state["running"] = False


def _read(p: pathlib.Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ the bridge's calls
def state() -> dict:
    folder = profile_dir()
    with _lock:
        return {"running": _state["running"], "messages": list(_state["messages"]),
                "error": _state["error"], "started_at": _state["started_at"],
                "can_undo": bool(_undo), "model": MODEL,
                "folder": f"profiles/{folder.name}", "blocked": _editable(folder),
                "files": sorted(snapshot(folder)) if folder.is_dir() else []}


def send(message: str) -> tuple[bool, str]:
    message = (message or "").strip()
    if not message:
        return False, "type what to change first"
    if len(message) > 20000:
        return False, "that message is too long (over 20,000 characters)"
    why = _editable(profile_dir())
    if why:
        return False, why
    with _lock:
        if _state["running"]:
            return False, "Claude is still working on the last message"
        _state.update(running=True, error="", started_at=_now())
        _state["messages"].append({"role": "you", "text": message, "changes": [], "at": _now()})
    threading.Thread(target=_turn, args=(message,), daemon=True).start()
    return True, ""


def undo() -> tuple[bool, str, list[dict]]:
    """Put back the files the last change touched, unless edited since."""
    folder = profile_dir()
    with _lock:
        if _state["running"]:
            return False, "wait until Claude has finished", []
        if not _undo:
            return False, "there is nothing to undo", []
        last = _undo[-1]
        moved = [rel for rel, text in last["after"].items() if _read(folder / rel) != text]
        if moved:
            return False, ("these files changed again after Claude's edit, so undoing would lose "
                           "that: " + ", ".join(moved)), []
        now = snapshot(folder)
        for rel, text in last["before"].items():
            p = folder / rel
            if text is None:
                p.unlink(missing_ok=True)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
        _undo.pop()
        undone = changes(now, snapshot(folder))
        _state["messages"].append({"role": "undo", "text": "Undid the last change.",
                                   "changes": undone, "at": _now()})
        return True, "", undone


def reset() -> tuple[bool, str]:
    """A new conversation. The files stay as they are."""
    with _lock:
        if _state["running"]:
            return False, "wait until Claude has finished"
        _state.update(session=None, messages=[], error="", started_at=None)
        _undo.clear()
        return True, ""
