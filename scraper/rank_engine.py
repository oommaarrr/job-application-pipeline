"""
Which model ranks the pool: the local one (Ollama) or Claude.

Feedback, October 2026: on many laptops the local model takes hours over a pool,
and on some it does not run at all. Claude (a small, fast model through the
Claude Code sign-in the build already uses) reads the same pool in minutes. The
dashboard switches between the two; the choice is kept in out/rank_engine.json
so the ranker, the build and the dashboard all read the same one.

Standard library only.
"""

from __future__ import annotations

import json
import pathlib

import config

FILE = pathlib.Path(__file__).resolve().parent / "out" / "rank_engine.json"
ENGINES = ("local", "claude")
LABELS = {"local": "Local model", "claude": "Claude"}


def get() -> str:
    try:
        e = json.loads(FILE.read_text(encoding="utf-8")).get("engine")
    except (OSError, ValueError, AttributeError):
        e = None
    return e if e in ENGINES else getattr(config, "RANK_ENGINE", "local")


def set_engine(engine: str) -> str:
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {', '.join(ENGINES)}")
    FILE.parent.mkdir(exist_ok=True)
    FILE.write_text(json.dumps({"engine": engine}), encoding="utf-8")
    return engine


def claude_model() -> str:
    return getattr(config, "CLAUDE_RANK_MODEL", "claude-haiku-5-5")
