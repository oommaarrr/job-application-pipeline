"""
Which Ollama model the ranker uses.

config.OLLAMA_MODEL names the model to use. It used to be the only one ever
considered: setup and the bridge downloaded it (a few GB) even when Ollama
already had a perfectly good model installed. Now, when OLLAMA_MODEL is not
installed but another local model is, that one is used and nothing is
downloaded. A download only happens when Ollama has no usable model at all.

Set OLLAMA_USE_INSTALLED = False in scraper/config_local.py to always use (and
download) exactly OLLAMA_MODEL.

Standard library only, so setup and the shell helpers can import it.
"""

from __future__ import annotations

import json
import urllib.request

DEFAULT_MODEL = "llama3.1"
DEFAULT_URL = "http://127.0.0.1:11434"


def tags(url: str = DEFAULT_URL, timeout: float = 3) -> list[dict] | None:
    """Ollama's installed models (/api/tags), or None when it is not reachable."""
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=timeout) as r:
            return list(json.loads(r.read()).get("models") or [])
    except (OSError, ValueError):
        return None


def matches(name: str, want: str) -> bool:
    """'llama3.1' is satisfied by 'llama3.1:latest', 'llama3.1:8b' and so on."""
    return name == want or (":" not in want and name.split(":")[0] == want)


def usable(entry: dict) -> bool:
    """Can this model rank jobs? Not an embedding model (it cannot generate
    text), and not an Ollama cloud model (the descriptions and the profile
    would leave the machine, and the ranker promises they never do)."""
    name = (entry.get("name") or entry.get("model") or "").lower()
    caps = entry.get("capabilities")          # newer Ollama versions list them
    if caps is not None and "completion" not in caps:
        return False
    details = entry.get("details") or {}
    families = [details.get("family") or ""] + list(details.get("families") or [])
    if "embed" in name or any("bert" in (f or "").lower() for f in families):
        return False
    if entry.get("remote_host") or entry.get("remote_model") or name.endswith("cloud"):
        return False
    return bool(name)


def choose(want: str, entries: list[dict] | None, use_installed: bool = True) -> str | None:
    """
    The model to rank with, given what Ollama has installed.

      * want is installed                 -> want
      * Ollama cannot be reached          -> want (nothing better is known)
      * another usable model is installed -> that one (Ollama lists the most
                                             recently changed first)
      * nothing usable is installed       -> None: want has to be downloaded
    """
    if entries is None:
        return want
    names = [e.get("name") or e.get("model") or "" for e in entries]
    if any(matches(n, want) for n in names):
        return want
    if not use_installed:
        return None
    return next((e.get("name") or e.get("model") for e in entries if usable(e)), None)


def wanted(cfg) -> str:
    return getattr(cfg, "OLLAMA_MODEL", None) or DEFAULT_MODEL


def resolve(cfg, entries: list[dict] | None = None, fetch: bool = True) -> str | None:
    """choose() with the settings from config. Fetches the model list unless
    one is given. None means: nothing usable is installed, download wanted()."""
    if entries is None and fetch:
        entries = tags(getattr(cfg, "OLLAMA_URL", DEFAULT_URL))
    return choose(wanted(cfg), entries, bool(getattr(cfg, "OLLAMA_USE_INSTALLED", True)))
