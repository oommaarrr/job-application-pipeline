"""
Keep an installed copy up to date from GitHub, from the dashboard.

Most people install from Download ZIP, which has no way to update: until
October 2026 every fix meant downloading again and moving the profile, jobs
and CVs across by hand. Now the pipeline checks every few hours whether GitHub
has a newer version, and Update now installs it:

  - a git clone:  git pull --ff-only (refused while a program file has local
                  edits, naming them, rather than overwriting them)
  - a ZIP copy:   the new version's ZIP, replacing only the program's own files.
                  Everything the code never ships (profiles, collected jobs,
                  built CVs, history, local.env, config_local.py) is untouched.
                  Files the new version no longer has are removed, the old
                  files are kept in _backups/update-<time>/ first, and any
                  failure puts them back.

The check also runs whenever Check for updates is pressed on the dashboard.

Then new Python packages are installed if requirements.txt changed, and the
bridge restarts itself (serve.py, RESTART_EXIT). The extension reloads itself
when its files on disk change (extension_build, serve.py).

Only version numbers are fetched; nothing about the user is sent. Turn the
check off with UPDATE_CHECK=0 in local.env (or UPDATE_CHECK = False in
config_local.py): the copy a project is developed in must never update itself
from GitHub over unpushed work.

Standard library only.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / ".installed.json"          # which version a ZIP copy has, and its files
CACHE = ROOT / "scraper" / "out" / "update.json"
BACKUPS = ROOT / "_backups"
API = os.environ.get("UPDATE_API", "https://api.github.com")
ZIP_URL = os.environ.get("UPDATE_ZIP", "https://codeload.github.com/{repo}/zip/{sha}")
# Hours between automatic checks. 24 hid a fix pushed in the morning until the
# next day (October 2026); the dashboard's Check for updates asks at once. Each
# check is two GitHub API calls, far under its 60 an hour without a login.
CHECK_EVERY_H = 3

# Never written by an update, whatever a ZIP holds: these are the user's.
KEEP = ("local.env", "config_local.py", "scraper/config_local.py", ".installed.json")
KEEP_DIRS = ("profiles/",)               # except the shipped example, below
SHIPPED_PROFILE = "profiles/example/"


def _config():
    try:
        sys.path.insert(0, str(ROOT / "scraper"))
        import config
        return config
    except Exception:                                       # noqa: BLE001
        return None


def repo() -> str:
    return getattr(_config(), "UPDATE_REPO", "oommaarrr/job-application-pipeline")


def enabled() -> bool:
    env = os.environ.get("UPDATE_CHECK")
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "off")
    return bool(getattr(_config(), "UPDATE_CHECK", True))


def mode() -> str:
    return "git" if (ROOT / ".git").exists() else "zip"


def _git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


_installed_memo: dict = {"at": 0.0, "sha": None}


def installed() -> str | None:
    """The commit this copy is at, or None for a ZIP from before the updater.
    Kept for a minute: the dashboard asks every two seconds, and for a git
    clone each answer would otherwise start a git process."""
    import time
    if time.time() - _installed_memo["at"] < 60:
        return _installed_memo["sha"]
    sha = _installed_now()
    _installed_memo.update(at=time.time(), sha=sha)
    return sha


def _installed_now() -> str | None:
    if mode() == "git":
        r = _git("rev-parse", "HEAD")
        return r.stdout.strip() or None if r.returncode == 0 else None
    try:
        return json.loads(STATE.read_text(encoding="utf-8")).get("sha")
    except (OSError, ValueError):
        return None


def _get_json(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "job-pipeline-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _read_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_cache(d: dict) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(d), encoding="utf-8")
    except OSError:
        pass


def check() -> dict:
    """Ask GitHub for the newest version and what changed since this one."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    have = installed()
    try:
        head = _get_json(f"{API}/repos/{repo()}/commits/main")
        latest = head["sha"]
        info = {"checked_at": now, "installed": have, "latest": latest, "mode": mode(),
                "latest_date": (head.get("commit", {}).get("committer") or {}).get("date")}
        if have == latest:
            info.update(available=False, behind=0, changes=[])
        elif have is None:
            # A ZIP from before the updater: no way to know what it has.
            info.update(available=True, behind=None, changes=[], unknown=True)
        else:
            cmp = _get_json(f"{API}/repos/{repo()}/compare/{have}...{latest}")
            status = cmp.get("status")
            commits = cmp.get("commits") or []
            info.update(available=status in ("ahead", "diverged"),
                        behind=cmp.get("ahead_by"), diverged=status == "diverged",
                        changes=[(c.get("commit", {}).get("message") or "").split("\n")[0][:120]
                                 for c in commits][-12:][::-1])
        info["error"] = None
    except Exception as e:                                  # noqa: BLE001
        info = {**_read_cache(), "checked_at": now, "error": f"could not reach GitHub: {e}"[:200]}
    _write_cache(info)
    return info


def status(auto: bool = True) -> dict:
    """The last check, plus whether a new one is due (the bridge starts it)."""
    info = _read_cache()
    info["enabled"] = enabled()
    due = True
    try:
        due = (dt.datetime.now() - dt.datetime.fromisoformat(info["checked_at"])).total_seconds() \
            > CHECK_EVERY_H * 3600
    except (KeyError, ValueError, TypeError):
        pass
    info["due"] = bool(auto and info["enabled"] and due)
    # Updated by hand since the check (git pull, a new ZIP): not news any more.
    if info.get("latest") and installed() == info.get("latest"):
        info["available"] = False
    return info


# ----------------------------------------------------------------- applying
def _req_hash() -> str:
    try:
        return hashlib.sha1((ROOT / "scraper" / "requirements.txt").read_bytes()).hexdigest()
    except OSError:
        return ""


def _install_packages(log) -> None:
    py = ROOT / "scraper" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.exists():
        return          # bootstrap.py sets it up at the next start
    log("installing the Python packages the new version needs")
    r = subprocess.run([str(py), "-m", "pip", "install", "--quiet", "-r",
                        str(ROOT / "scraper" / "requirements.txt")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=900)
    if r.returncode != 0:
        raise RuntimeError("installing packages failed: " + (r.stderr or r.stdout)[-300:])


def _user_file(rel: str) -> bool:
    if rel in KEEP:
        return True
    return any(rel.startswith(d) for d in KEEP_DIRS) and not rel.startswith(SHIPPED_PROFILE)


def apply(log=print) -> dict:
    """Install the newest version. Returns {"ok", "from", "to", "why"}."""
    if not enabled():
        return {"ok": False, "why": "updates are turned off here (UPDATE_CHECK)"}
    before = installed()
    req_before = _req_hash()
    if mode() == "git":
        changed = [ln[3:] for ln in _git("status", "--porcelain", "--untracked-files=no")
                   .stdout.splitlines() if ln.strip()]
        if changed:
            return {"ok": False, "why": "these program files have local edits, so pulling "
                    "would mix them with the update: " + ", ".join(changed[:6])
                    + ". Undo them (git checkout -- <file>) or update with git yourself."}
        log("pulling the new version with git")
        r = _git("pull", "--ff-only")
        if r.returncode != 0:
            return {"ok": False, "why": "git pull failed: " + (r.stderr or r.stdout).strip()[-300:]}
    else:
        latest = check().get("latest")
        if not latest:
            return {"ok": False, "why": "could not find the newest version on GitHub"}
        log("downloading the new version")
        with urllib.request.urlopen(ZIP_URL.format(repo=repo(), sha=latest), timeout=180) as r:
            data = r.read()
        _replace_from_zip(data, latest, log)
    if _req_hash() != req_before:
        _install_packages(log)
    _installed_memo["at"] = 0.0
    after = installed()
    log("updated")
    return {"ok": True, "from": before, "to": after}


def _replace_from_zip(data: bytes, sha: str, log) -> None:
    z = zipfile.ZipFile(io.BytesIO(data))
    files: dict[str, zipfile.ZipInfo] = {}
    for info in z.infolist():
        if info.is_dir():
            continue
        rel = info.filename.split("/", 1)[1] if "/" in info.filename else ""
        if not rel or rel.startswith("/") or ".." in pathlib.PurePosixPath(rel).parts:
            continue
        if not _user_file(rel):
            files[rel] = info
    # A download that is not this project must not replace it.
    if not {"start.py", "scraper/serve.py"} <= set(files):
        raise RuntimeError("the download does not look like Job Pipeline; nothing was changed")
    try:
        old_files = set(json.loads(STATE.read_text(encoding="utf-8")).get("files") or [])
    except (OSError, ValueError):
        old_files = set()           # first update of an old ZIP: only add and replace

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = BACKUPS / f"update-{stamp}"
    saved: list[str] = []           # replaced or removed, backed up first
    added: list[str] = []
    log(f"replacing {len(files)} program files (the old ones are kept in _backups/update-{stamp})")
    try:
        for rel, info in files.items():
            dest = ROOT / rel
            new = z.read(info)
            if dest.exists():
                if dest.read_bytes() == new:
                    continue
                (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, backup / rel)
                saved.append(rel)
            else:
                added.append(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".updating")
            tmp.write_bytes(new)
            os.replace(tmp, dest)
            if info.external_attr >> 16 & 0o111:            # keep start.sh runnable
                dest.chmod(dest.stat().st_mode | 0o755)
        for rel in sorted(old_files - set(files)):
            dest = ROOT / rel
            if dest.is_file() and not _user_file(rel):
                (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, backup / rel)
                saved.append(rel)
                dest.unlink()
    except Exception:
        log("the update failed part way; putting the old files back")
        for rel in saved:
            try:
                (ROOT / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup / rel, ROOT / rel)
            except OSError:
                pass
        for rel in added:
            try:
                (ROOT / rel).unlink()
            except OSError:
                pass
        raise
    STATE.write_text(json.dumps({"sha": sha, "files": sorted(files),
                                 "at": dt.datetime.now().isoformat(timespec="seconds")}),
                     encoding="utf-8")


if __name__ == "__main__":
    if "--apply" in sys.argv:
        print(json.dumps(apply(), indent=2))
    else:
        print(json.dumps(check(), indent=2))
