"""
Title relevance scoring + drift detection. No LLM.

score_title() maps a job title to 0..1 against the CV-derived term tables in
config.py. DriftDetector keeps a rolling window of those scores; when the window
mean falls below the floor, the query has drifted off-profile and the runner
abandons it.

This works because job boards rank by relevance, so drift is real and roughly
monotonic as you page deeper.
"""

import re
from collections import deque
from dataclasses import dataclass, field

import config


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = s.replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")
    return re.sub(r"[^a-z0-9+#. ]+", " ", s)


def _hits(text: str, table: dict[str, float]) -> list[tuple[str, float]]:
    return [(term, w) for term, w in table.items() if term.strip() in text]


@dataclass
class TitleScore:
    score: float
    core: list[str] = field(default_factory=list)
    support: list[str] = field(default_factory=list)
    penalties: list[str] = field(default_factory=list)

    @property
    def on_profile(self) -> bool:
        return self.score >= config.RELEVANCE_FLOOR


def score_title(title: str) -> TitleScore:
    """
    Composition:
      - best CORE hit carries up to 0.70 (domain match is most of the signal)
      - SUPPORT hits add up to 0.30, with diminishing returns
      - PENALTY and SENIORITY subtract directly
    """
    t = " " + _norm(title) + " "

    core = _hits(t, config.CORE_TERMS)
    support = _hits(t, config.SUPPORT_TERMS)
    penal = _hits(t, config.PENALTY_TERMS)
    senior = _hits(t, config.SENIORITY_PENALTY)

    core_score = max((w for _, w in core), default=0.0) * 0.70

    sup_sorted = sorted((w for _, w in support), reverse=True)
    sup_score = sum(w * (0.5 ** i) for i, w in enumerate(sup_sorted)) * 0.30
    sup_score = min(sup_score, 0.30)

    # Support alone shouldn't clear the floor — "Sales Engineer" must not pass.
    if not core:
        sup_score *= 0.5

    penalty = sum(w for _, w in penal) + sum(w for _, w in senior)

    score = max(0.0, min(1.0, core_score + sup_score + penalty))
    return TitleScore(
        score=round(score, 3),
        core=[t for t, _ in core],
        support=[t for t, _ in support],
        penalties=[t for t, _ in penal] + [t for t, _ in senior],
    )


class DriftDetector:
    """Rolling-window relevance monitor for a single query."""

    def __init__(self):
        self.window: deque[float] = deque(maxlen=config.DRIFT_WINDOW)
        self.seen = 0
        self.drifted_at: int | None = None

    def add(self, score: float) -> None:
        self.window.append(score)
        self.seen += 1

    @property
    def mean(self) -> float:
        return sum(self.window) / len(self.window) if self.window else 0.0

    def has_drifted(self) -> bool:
        if self.seen < config.DRIFT_MIN_RESULTS:
            return False
        if len(self.window) < config.DRIFT_WINDOW:
            return False
        if self.mean < config.DRIFT_THRESHOLD:
            if self.drifted_at is None:
                self.drifted_at = self.seen
            return True
        return False
