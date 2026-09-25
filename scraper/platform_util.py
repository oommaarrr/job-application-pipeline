"""
Everything that differs between macOS, Linux and Windows, in one place.

The pipeline was written on a Mac and leaned on things Windows does not have:
bash, pgrep and pkill, process groups, fcntl, symlinks, osascript, a venv laid
out as .venv/bin/python. Each of those now goes through a function here, so the
rest of the code asks "find the running build" or "stop this process and its
children" and never "which OS is this".

Process work uses psutil, which behaves the same on all three systems. The
alternative was a pgrep branch and a tasklist branch for every call, and the
Windows branch would have been the one nobody tested.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import shutil
import subprocess
import sys
import time
import webbrowser

import psutil

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
HOME = pathlib.Path.home()


# ------------------------------------------------------------------ python
def venv_python(venv: pathlib.Path, windowless: bool = False) -> pathlib.Path:
    """The interpreter inside a venv. Windows puts it in Scripts\\python.exe.

    windowless=True gives pythonw.exe on Windows, for things started at login
    that should not open a console window. Elsewhere it is the same python."""
    if IS_WIN:
        return venv / "Scripts" / ("pythonw.exe" if windowless else "python.exe")
    return venv / "bin" / "python"


def utf8_env(env: dict | None = None) -> dict:
    """An environment in which every Python child reads and writes UTF-8.

    Windows still defaults to the ANSI code page (cp1252 in Germany), so a
    job title with an umlaut read without an explicit encoding comes out
    garbled or raises. Setting UTF-8 mode for every child is the blanket fix;
    on macOS and Linux it changes nothing."""
    env = dict(os.environ if env is None else env)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


# ------------------------------------------------------------------ tools
def _first_file(paths) -> str | None:
    for p in paths:
        if p and pathlib.Path(p).is_file():
            return str(p)
    return None


def find_claude() -> str | None:
    """The Claude Code CLI. PATH first, then where the installers put it.

    A login agent gets a bare PATH, which is why the fixed places matter: the
    native installer uses ~/.local/bin on every system, npm puts claude.cmd in
    %APPDATA%\\npm on Windows, Homebrew uses /opt/homebrew or /usr/local."""
    found = shutil.which("claude")
    if found:
        return found
    if IS_WIN:
        appdata = os.environ.get("APPDATA", "")
        local = os.environ.get("LOCALAPPDATA", "")
        return _first_file([
            HOME / ".local" / "bin" / "claude.exe",
            pathlib.Path(appdata) / "npm" / "claude.cmd" if appdata else None,
            pathlib.Path(local) / "Programs" / "claude" / "claude.exe" if local else None,
        ])
    return _first_file([HOME / ".local/bin/claude", "/usr/local/bin/claude",
                        "/opt/homebrew/bin/claude"])


def find_ollama() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    if IS_WIN:
        local = os.environ.get("LOCALAPPDATA", "")
        return _first_file([pathlib.Path(local) / "Programs" / "Ollama" / "ollama.exe"
                            if local else None])
    return _first_file(["/usr/local/bin/ollama", "/opt/homebrew/bin/ollama",
                        "/Applications/Ollama.app/Contents/Resources/ollama"])


def ollama_up(url: str = "http://127.0.0.1:11434") -> bool:
    import urllib.request
    try:
        urllib.request.urlopen(f"{url}/api/version", timeout=3).close()
        return True
    except OSError:
        return False


def start_ollama(log: pathlib.Path | None = None, url: str = "http://127.0.0.1:11434",
                 wait: float = 20) -> bool:
    """Start Ollama if it is installed and not running; True once it answers.

    It is a normal program, not a service, so after a reboot (or a fresh
    install) it is installed and not running, and every ranking fails until
    someone opens the app. Nobody should have to."""
    if ollama_up(url):
        return True
    exe = find_ollama()
    if not exe:
        return False
    out = open(log, "a", encoding="utf-8") if log else subprocess.DEVNULL
    try:
        subprocess.Popen([exe, "serve"], stdout=out, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, **detach_kwargs())
    except OSError:
        return False
    finally:
        if log:
            out.close()
    end = time.time() + wait
    while time.time() < end:
        if ollama_up(url):
            return True
        time.sleep(1)
    return False


def find_git_bash() -> str | None:
    """Claude Code on Windows runs its shell commands through Git Bash, so a
    build cannot work without it. Not needed (and always None) elsewhere."""
    if not IS_WIN:
        return None
    env = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if env and pathlib.Path(env).is_file():
        return env
    candidates = []
    git = shutil.which("git")
    if git:
        # ...\Git\cmd\git.exe -> ...\Git\bin\bash.exe
        candidates.append(pathlib.Path(git).resolve().parent.parent / "bin" / "bash.exe")
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    if os.environ.get("LOCALAPPDATA"):
        roots.append(str(pathlib.Path(os.environ["LOCALAPPDATA"]) / "Programs"))
    for root in roots:
        if root:
            candidates.append(pathlib.Path(root) / "Git" / "bin" / "bash.exe")
    return _first_file(candidates)


def install_hint(tool: str) -> str:
    """The one command that installs or starts a tool here, for fix messages."""
    if tool == "claude":
        return ("irm https://claude.ai/install.ps1 | iex   (in PowerShell)" if IS_WIN
                else "curl -fsSL https://claude.ai/install.sh | bash")
    if tool == "git-bash":
        return "winget install Git.Git   (or https://git-scm.com/download/win)"
    if tool == "start":
        return "start.bat" if IS_WIN else "./start.sh"
    if tool == "setup":
        return "setup.bat" if IS_WIN else "./setup.sh"
    return tool


# ------------------------------------------------------------------ processes
def detach_kwargs() -> dict:
    """Popen arguments for a long job that must outlive the process starting it.

    POSIX: a new session, so stopping the bridge (Ctrl+C, or its agent being
    restarted) does not take a running build down with it, and the build's pid
    leads a process group that can be stopped as a whole.

    Windows: a new process group (Ctrl+C in the bridge's console is not
    delivered to it) and no console window. CREATE_NO_WINDOW rather than
    DETACHED_PROCESS on purpose: a detached process has no console at all, so
    every console program it starts (claude, ollama, python) would pop open a
    window of its own."""
    if IS_WIN:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def quiet_kwargs() -> dict:
    """Popen arguments for a helper call (claude auth status, ollama list) so a
    bridge running without a console does not flash one open on Windows."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WIN else {}


def _cmdline(p: psutil.Process) -> list[str]:
    try:
        return p.cmdline()
    except (psutil.Error, OSError):
        return []


def cmdline(pid: int) -> list[str]:
    try:
        return _cmdline(psutil.Process(pid))
    except psutil.Error:
        return []


def find_procs(*needles: str) -> list[int]:
    """Pids whose command line contains any needle (the pgrep -f of before).
    The calling process is never included."""
    me = os.getpid()
    out = []
    for p in psutil.process_iter(["pid"]):
        if p.pid == me:
            continue
        line = " ".join(_cmdline(p))
        if line and any(n in line for n in needles):
            out.append(p.pid)
    return out


def _cwd(p: psutil.Process) -> str | None:
    try:
        return p.cwd()
    except (psutil.Error, OSError):
        return None


def _same(a: str | pathlib.Path, b: str | pathlib.Path) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError):
        return False


def find_script_procs(*scripts: pathlib.Path) -> list[int]:
    """Pids running one of these exact script files.

    By path, not by name, so two checkouts on one machine (a personal copy and
    a development copy, each with its own bridge) never see, adopt or stop each
    other's builds. A relative argument is resolved against the process's own
    working directory, which covers `python run_batch.py` typed by hand."""
    names = {pathlib.Path(s).name for s in scripts}
    me = os.getpid()
    out = []
    for p in psutil.process_iter(["pid"]):
        if p.pid == me:
            continue
        args = _cmdline(p)
        hits = [a for a in args[:4] if pathlib.Path(a).name in names]
        if not hits:
            continue
        cwd = _cwd(p) or ""
        for a in hits:
            full = a if os.path.isabs(a) else os.path.join(cwd, a)
            if any(_same(full, s) for s in scripts):
                out.append(p.pid)
                break
    return out


def find_apply_batch_claudes(cwd: pathlib.Path | None = None) -> list[int]:
    """Claude sessions writing a batch: `claude -p "/apply-batch …"`.

    Matched on the arguments rather than a joined string, because the binary
    is claude, claude.exe, or node running claude.cmd depending on how it was
    installed. With cwd, only sessions working in that folder (this checkout)."""
    out = []
    for p in psutil.process_iter(["pid"]):
        args = _cmdline(p)
        if "-p" in args and any(a.startswith("/apply-batch") for a in args) \
                and any("claude" in a.lower() for a in args):
            if cwd is None or _same(_cwd(p) or "", cwd):
                out.append(p.pid)
    return out


def alive(pid: int | None) -> bool:
    """Is this pid a live process? A zombie (exited, not yet reaped) is not."""
    if not pid:
        return False
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def started_at(pid: int) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S",
                             time.localtime(psutil.Process(pid).create_time()))
    except psutil.Error:
        return "unknown"


def kill_tree(pid: int, grace: float = 3.0) -> bool:
    """Stop a process and everything it started: politely, then for good.

    Children are collected BEFORE the parent is touched, because once the
    parent is gone its children are reparented and no longer findable from it.
    Returns False if there was nothing to stop."""
    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return False
    try:
        procs = root.children(recursive=True) + [root]
    except psutil.Error:
        procs = [root]
    if not IS_WIN:
        # The whole group too, which also catches anything already orphaned,
        # but only when pid LEADS its group (a build started detached). A
        # claude session shares the build's group, and signalling that group
        # from the build's own watchdog would stop the build itself.
        import signal
        with contextlib.suppress(OSError):
            if os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGTERM)
    for p in procs:
        with contextlib.suppress(psutil.Error):
            p.terminate()
    _gone, left = psutil.wait_procs(procs, timeout=grace)
    for p in left:
        with contextlib.suppress(psutil.Error):
            p.kill()
    psutil.wait_procs(left, timeout=grace)
    return True


# ------------------------------------------------------------------ files
def _is_junction(p: pathlib.Path) -> bool:
    """Path.is_junction() only exists from Python 3.12. Before that a junction
    looks like a real folder, and link_dir would refuse to replace its own link
    on the second run. The reparse-point attribute says it on every version."""
    if not IS_WIN:
        return False
    if hasattr(p, "is_junction"):
        return p.is_junction()
    import stat
    try:
        attrs = os.lstat(p).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def link_dir(target: pathlib.Path, link: pathlib.Path, relative: bool = False) -> None:
    """Point `link` at the directory `target`, replacing an older link.

    A symlink on macOS and Linux. On Windows a symlink needs admin rights or
    Developer Mode, so a directory junction is used instead: it needs neither
    and behaves the same for reading files through it. A real directory at
    `link` is never deleted; that would be someone's data.

    relative=True writes a relative symlink, so the checkout can be moved.
    Junctions are always absolute (Windows requires it)."""
    target = pathlib.Path(os.path.abspath(target))
    link = pathlib.Path(link)
    if link.is_symlink() or _is_junction(link):
        if IS_WIN:
            try:
                os.rmdir(link)          # removes a junction, never its target
            except OSError:
                os.unlink(link)
        else:
            link.unlink()
    elif link.exists():
        raise FileExistsError(f"{link} is a real folder, not a link; move it aside first")
    if IS_WIN:
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        except (ImportError, AttributeError, OSError):
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           check=True, capture_output=True, **quiet_kwargs())
    else:
        dest = os.path.relpath(target, link.parent.resolve()) if relative else target
        link.symlink_to(dest, target_is_directory=True)


@contextlib.contextmanager
def file_lock(path: pathlib.Path):
    """An exclusive lock between processes, held for the `with` block.

    fcntl.flock on POSIX. On Windows, msvcrt.locking on the first byte: it
    locks a byte RANGE from the current position, so the file is seeked to 0
    first or two writers would lock different bytes and never collide. It
    gives up after ten seconds on its own, so it is retried."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fh:
        if IS_WIN:
            import msvcrt
            fh.seek(0)
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.2)
            try:
                yield fh
            finally:
                fh.seek(0)
                with contextlib.suppress(OSError):
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield fh
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


# ------------------------------------------------------------------ desktop
def open_path(target: str | pathlib.Path) -> None:
    """Open a file or URL with whatever the system uses for it."""
    t = str(target)
    try:
        if t.startswith(("http://", "https://", "file:")):
            webbrowser.open(t)
        elif IS_WIN:
            os.startfile(t)                                  # noqa: S606
        elif IS_MAC:
            subprocess.run(["open", t], check=False)
        else:
            subprocess.run(["xdg-open", t], check=False)
    except OSError:
        pass


def notify(title: str, message: str, subtitle: str = "") -> None:
    """A desktop notification, best effort. Never raises, never blocks long."""
    if os.environ.get("NO_NOTIFY") == "1":      # tests and CI
        return
    text = f"{subtitle}: {message}" if subtitle and not IS_MAC else message
    try:
        if IS_MAC:
            def q(s: str) -> str:
                return s.replace("\\", "\\\\").replace('"', '\\"')
            script = f'display notification "{q(message)}" with title "{q(title)}"'
            if subtitle:
                script += f' subtitle "{q(subtitle)}"'
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
        elif IS_WIN:
            # A toast through the WinRT API that ships with Windows 10 and 11,
            # attributed to PowerShell so no app registration is needed.
            def ps(s: str) -> str:
                return (s.replace("&", "&amp;").replace("<", "&lt;")
                        .replace(">", "&gt;").replace("'", "''"))
            script = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null;"
                "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] > $null;"
                "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
                f"$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{ps(title)}</text><text>{ps(text)}</text></binding></visual></toast>');"
                "$t = New-Object Windows.UI.Notifications.ToastNotification $x;"
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
                "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($t)")
            # Fire and forget: PowerShell takes a second or two to start, and a
            # notification must never hold up the build that sent it.
            subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, **quiet_kwargs())
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", title, text], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass
