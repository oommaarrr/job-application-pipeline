"""
Required-years-of-experience extraction. No LLM.

Same trick as the language gate: find WHERE experience is discussed (a small
closed set of stems — Erfahrung / Berufserfahrung / experience / Jahre / years),
then read the number out of that window.

When a range is given ("3-5 Jahre"), we take the LOW end: that's the entry bar.
When nothing is stated, we fall back to the seniority word in the title, and
failing that return None — which ranks as most accessible.
"""

import re
from dataclasses import dataclass

WORD_NUMBERS = {
    "ein": 1, "eine": 1, "einem": 1, "einer": 1, "one": 1,
    "zwei": 2, "two": 2, "drei": 3, "three": 3, "vier": 4, "four": 4,
    "fünf": 5, "fuenf": 5, "five": 5, "sechs": 6, "six": 6,
    "sieben": 7, "seven": 7, "acht": 8, "eight": 8, "zehn": 10, "ten": 10,
}

EXP_CONTEXT = re.compile(
    r"(berufserfahrung|erfahrung|erfahren|experience|praxis|hintergrund|"
    r"working|worked|shipping|building|developing|professional|t(ä|ae)tigkeit)",
    re.IGNORECASE,
)

# Qualitative German seniority phrases. These carry no number but are extremely
# common — "mehrjährige Erfahrung" appeared in half the Berlin postings sampled.
# Treating them as "not stated" would rank those jobs as the MOST accessible,
# which is precisely backwards, so they map to conventional year equivalents.
QUALITATIVE = [
    (r"erste[ns]?\s+(berufs)?erfahrung", 0),
    (r"erste\s+praktische\s+erfahrung", 0),
    (r"\bberufseinsteiger", 0),
    (r"einschl(ä|ae)gige\s+(berufs)?erfahrung", 2),
    (r"mehrj(ä|ae)hrige\s+(berufs)?erfahrung", 3),
    (r"fundierte\s+(praktische\s+)?(berufs)?erfahrung", 3),
    (r"nachweisliche\s+erfahrung", 3),
    (r"solide\s+erfahrung", 3),
    (r"proven\s+(track\s+record|experience)", 3),
    (r"substantial\s+experience", 4),
    (r"langj(ä|ae)hrige\s+(berufs)?erfahrung", 5),
    (r"umfangreiche\s+(berufs)?erfahrung", 5),
    (r"extensive\s+experience", 5),
]
QUALITATIVE_RX = [(re.compile(p, re.IGNORECASE), y) for p, y in QUALITATIVE]

# "3+ Jahre", "3-5 Jahre", "mindestens 2 Jahren", "at least 3 years", "2 yrs"
NUM_YEARS = re.compile(
    r"(?P<lo>\d{1,2})\s*(?:\+|plus)?\s*(?:\s*(?:-|–|—|bis|to)\s*(?P<hi>\d{1,2}))?"
    r"\s*(?P<unit>jahre?n?|jahres|years?|yrs?|j\.)",
    re.IGNORECASE,
)
WORD_YEARS = re.compile(
    r"\b(?P<w>" + "|".join(WORD_NUMBERS) + r")\s+(?:jahre?n?|years?)\b",
    re.IGNORECASE,
)

# Seniority fallback, when the posting never states a number.
TITLE_SENIORITY = [
    (r"\b(praktik\w*|intern|internship|werkstudent|working\s+student)\b", 0),
    (r"\b(junior|jr\.?|einsteiger|berufseinsteiger|absolvent|graduate|entry[\s-]level|trainee|einstieg)\b", 0),
    (r"\b(mid[\s-]level|professional)\b", 3),
    (r"\b(senior|sr\.?)\b", 5),
    (r"\b(staff|lead|leitung|teamlead|team\s+lead)\b", 7),
    (r"\b(principal|head\s+of|director|vp|chief|cto)\b", 8),
]
TITLE_SENIORITY_RX = [(re.compile(p, re.IGNORECASE), y) for p, y in TITLE_SENIORITY]


@dataclass
class ExperienceVerdict:
    years: int | None          # required years; None = never stated
    source: str                # "description" | "title" | "unstated"
    evidence: str = ""

    @property
    def sort_key(self) -> float:
        """Unstated sorts as 0 — no stated barrier is the most accessible."""
        return 0.0 if self.years is None else float(self.years)

    @property
    def label(self) -> str:
        if self.years is None:
            return "not stated"
        if self.years == 0:
            return "entry level"
        return f"{self.years}+ yrs"


def _window(text: str, start: int, end: int, pad: int = 90) -> str:
    return text[max(0, start - pad): min(len(text), end + pad)]


def extract_years(description: str, title: str = "") -> ExperienceVerdict:
    text = re.sub(r"[ \t\xa0]+", " ", description or "")

    candidates: list[tuple[int, str]] = []

    for m in NUM_YEARS.finditer(text):
        ctx = _window(text, m.start(), m.end())
        explicit_plus = "+" in m.group(0)
        # A bare number needs an experience word nearby, or "3 Jahre befristet"
        # (a contract term) would read as a requirement. But "5+ years" is
        # self-evidently an experience claim in a job ad, so it stands alone.
        if not explicit_plus and not EXP_CONTEXT.search(ctx):
            continue
        lo = int(m.group("lo"))
        if 0 <= lo <= 20:
            candidates.append((lo, ctx.strip()[:200]))

    for m in WORD_YEARS.finditer(text):
        ctx = _window(text, m.start(), m.end())
        if not EXP_CONTEXT.search(ctx):
            continue
        candidates.append((WORD_NUMBERS[m.group("w").lower()], ctx.strip()[:200]))

    if candidates:
        # Within a RANGE ("2-4 Jahre") we already took the low end — that's the
        # entry bar. But ACROSS separate requirements ("3 yrs Python, 5 yrs
        # AWS") the requirements are cumulative, so the job really demands 5.
        # Taking the max is the safe direction for a ranking built on
        # accessibility: understating would float demanding roles to the top.
        years, ev = max(candidates, key=lambda c: c[0])
        return ExperienceVerdict(years=years, source="description", evidence=ev)

    # No explicit number — fall back to qualitative German seniority phrasing.
    qual = []
    for rx, yrs in QUALITATIVE_RX:
        m = rx.search(text)
        if m:
            qual.append((yrs, m.group(0)))
    if qual:
        years, phrase = max(qual, key=lambda c: c[0])
        return ExperienceVerdict(
            years=years, source="phrase", evidence=f"“{phrase}”"
        )

    for rx, yrs in TITLE_SENIORITY_RX:
        m = rx.search(title or "")
        if m:
            return ExperienceVerdict(
                years=yrs, source="title", evidence=f"title says “{m.group(0)}”"
            )

    return ExperienceVerdict(years=None, source="unstated")
