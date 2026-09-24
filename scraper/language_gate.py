"""
German-requirement gate. No LLM.

The idea: regexing the *meaning* ("fliessend", "verhandlungssicher", "sehr gut",
...) is an unbounded set. Regexing the *topic word* ("deutsch") is a closed set of
about ten stems. So we use a cheap regex to find WHERE German is discussed, then
score the level WITHIN that sentence/clause using ordered phrase tables.

Level scale (CEFR), sourced from German HR convention:
    0 none   1 A1 Grundkenntnisse   2 A2 erweiterte Grundkenntnisse
    3 B1 gut/konversationssicher    4 B2 sehr gut/fliessend
    5 C1 verhandlungssicher         6 C2 muttersprachlich

Decision rules
    no German mentioned anywhere ............... ACCEPT (level None)
    negation present ........................... ACCEPT (level 0)
    optionality marker present ................. ACCEPT (requirement is soft)
    level <= GERMAN_CAP ........................ ACCEPT
    level >  GERMAN_CAP ........................ REJECT
    German mentioned, no level found ........... ACCEPT + flag (lightest filter)
"""

import re
import unicodedata
from dataclasses import dataclass, field

import config

# --------------------------------------------------------------------------
# Topic detection: does this sentence talk about the German language at all?
# German compounding means the single stem "deutsch" covers Deutschkenntnisse,
# Deutschniveau, deutschsprachig, verhandlungssicheres Deutsch, ...
# --------------------------------------------------------------------------
# The negative lookahead on "german(?!y)" is load-bearing: without it "Berlin,
# Germany" matches as a German-LANGUAGE reference, and essentially every Berlin
# posting falsely reports "German mentioned, level unstated".
# "germany"/"germany-wide" are excluded; "german", "germans", "german-speaking",
# "germanic" still match.
GERMAN_TOKEN = re.compile(
    r"\b(deutsch\w*|german(?!y)\w*|allemand)\b|\bde-?niveau\b", re.IGNORECASE
)
ENGLISH_TOKEN = re.compile(r"\b(englisch\w*|english\w*)\b", re.IGNORECASE)

# Any language talk at all — used to decide if a sentence is a language sentence.
LANG_TOPIC = re.compile(
    r"\b(deutsch\w*|german(?!y)\w*|sprach\w*|language|niveau|kenntnisse)\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# Level table. ORDER MATTERS — most specific first.
# "sehr gute" must be tested before "gute", or every B2 posting reads as B1.
# --------------------------------------------------------------------------
LEVEL_PATTERNS: list[tuple[str, int]] = [
    # --- 6: C2 / native ---
    (r"\bc\s?2\b", 6),
    (r"muttersprach\w*", 6),
    (r"\bnative[\s-]?(speaker|level|proficiency)?\b", 6),
    (r"\bmother\s?tongue\b", 6),
    (r"erstsprache", 6),
    (r"auf\s+muttersprachlichem\s+niveau", 6),

    # --- 5: C1 / verhandlungssicher ---
    (r"\bc\s?1\b", 5),
    (r"verhandlungssicher\w*", 5),
    (r"verhandlungsf(ä|ae)hig\w*", 5),
    (r"\bbusiness[\s-]?fluent\b", 5),
    (r"\bproficient\b", 5),
    (r"\bexzellent\w*", 5),
    (r"\bexcellent\b", 5),
    (r"\bhervorragend\w*", 5),
    (r"\bperfekt\w*", 5),
    (r"\bperfect\b", 5),

    # --- 4: B2 / sehr gut / fliessend ---
    (r"\bb\s?2\b", 4),
    (r"sehr\s+gut\w*", 4),
    (r"flie(ß|ss)end\w*", 4),
    (r"\bfluent\w*", 4),
    (r"\bstilsicher\w*", 4),
    (r"\bsicher(e|es|en|er)?\s+beherrsch\w*", 4),
    (r"\bversiert\w*", 4),
    (r"\bumfassende?\s+kenntnisse", 4),
    (r"\bstrong\s+(command|knowledge|skills)", 4),
    (r"\bhohe[sn]?\s+niveau", 4),

    # --- 3: B1 / gut / konversationssicher  (your cap) ---
    (r"\bb\s?1\b", 3),
    (r"konversationssicher\w*", 3),
    (r"\bgut(e|es|en|er)?\b", 3),
    (r"\bsolide[nrs]?\b", 3),
    (r"\bgood\s+(command|knowledge|skills|level)", 3),
    (r"\bconversational\w*", 3),
    (r"\bintermediate\b", 3),
    (r"\bsicher(e|es|en|er)?\s+kenntnisse", 3),

    # --- 2: A2 ---
    (r"\ba\s?2\b", 2),
    (r"erweiterte\s+grundkenntnisse", 2),
    (r"\belementary\b", 2),

    # --- 1: A1 / Grundkenntnisse ---
    (r"\ba\s?1\b", 1),
    (r"grundkenntnisse", 1),
    (r"grundlegende?\s+kenntnisse", 1),
    (r"\bbasis[\s-]?kenntnisse", 1),
    (r"\bbasic\b", 1),
    (r"\bbeginner\b", 1),
    (r"\bgrundlagen\b", 1),
]
LEVEL_RX = [(re.compile(p, re.IGNORECASE), lv) for p, lv in LEVEL_PATTERNS]

# --------------------------------------------------------------------------
# Optionality markers — "nice to have", not a hard requirement.
# Sourced from German HR convention: wuenschenswert / von Vorteil / idealerweise
# signal a Kann-Anforderung rather than a Muss-Anforderung.
# --------------------------------------------------------------------------
OPTIONAL_PATTERNS = [
    r"von\s+vorteil", r"vorteilhaft", r"w(ü|ue)nschenswert", r"idealerweise",
    r"im\s+idealfall", r"\bideal\w*", r"nice[\s-]to[\s-]have", r"\bein\s+plus\b",
    r"\bals\s+plus\b", r"\bbonus\b", r"\bgern(e)?\s+gesehen", r"\bgerne\b",
    r"\bhilfreich\b", r"\bbegr(ü|ue)(ß|ss)enswert", r"von\s+nutzen",
    r"\bkein\s+muss\b", r"\boptional\b", r"\ba\s+plus\b", r"\badvantageous\b",
    r"\bdesirable\b", r"\bpreferred\b", r"\bpreferably\b", r"\bnot\s+mandatory\b",
    r"\bwelcome\b", r"\bappreciated\b", r"\bvon\s+vorteil\b", r"\bschön\s+wäre\b",
    r"\bplus[\s,.]", r"\bbereitschaft,?\s+deutsch\s+zu\s+lernen",
    r"\bwillingness\s+to\s+learn\s+german", r"\bbereit\w*\s+.{0,20}zu\s+lernen",
]
OPTIONAL_RX = re.compile("|".join(OPTIONAL_PATTERNS), re.IGNORECASE)

# --------------------------------------------------------------------------
# Negation — German explicitly NOT required.
# --------------------------------------------------------------------------
NEGATION_PATTERNS = [
    r"keine?\s+deutsch\w*", r"kein\s+deutsch\b",
    r"deutsch\w*\s+(sind\s+|ist\s+)?nicht\s+(zwingend\s+)?(erforderlich|notwendig|n(ö|oe)tig|vorausgesetzt|ben(ö|oe)tigt)",
    r"ohne\s+deutsch\w*", r"nicht\s+erforderlich", r"nicht\s+notwendig",
    r"nicht\s+zwingend", r"no\s+german", r"without\s+german",
    r"german\s+(is\s+)?not\s+(required|needed|necessary|mandatory)",
    r"german\s+skills?\s+are\s+not", r"not\s+required", r"english[\s-]only",
    r"english\s+is\s+(our|the)\s+(company|working|office)\s+language",
    r"working\s+language\s+is\s+english", r"unternehmenssprache\s+ist\s+englisch",
    r"arbeitssprache\s+ist\s+englisch",
]
NEGATION_RX = re.compile("|".join(NEGATION_PATTERNS), re.IGNORECASE)

# --------------------------------------------------------------------------
# Sentences that mention German but are NOT a language requirement.
# Real examples pulled from live Berlin postings:
#   "Choose between an ABC travel pass or a Deutschland-Ticket"   (transit perk)
#   "free German language classes"                                (benefit)
#   "experience German culture first hand"                        (culture blurb)
# Without this, benefits sections make almost every posting look like it has an
# unstated German requirement, which buries the real signal in noise.
# --------------------------------------------------------------------------
NON_REQUIREMENT_PATTERNS = [
    # Transit passes: Deutschland-Ticket, Deutschland Jobticket, Deutschlandticket…
    r"deutschland[\s-]*\w*ticket", r"job[\s-]?ticket", r"deutsche\s+bahn",
    r"german\s+(culture|cuisine|history|holidays?|weather|market)",
    r"(free|kostenlos\w*|subsidi[sz]ed|gratis)\s+.{0,24}(german|deutsch)\w*",
    r"(german|deutsch\w*)[\s-]*(sprach|language)?\s*(klassen|kurse?|classes|"
    r"courses?|lessons?|training|unterricht|schule)",
    r"deutschkurs\w*", r"sprachkurs\w*",
    r"lerne[nst]?\s+deutsch", r"learn\s+german",
    r"german\s+(public\s+)?holidays", r"deutsche[rn]?\s+feiertag\w*",
    r"nach\s+deutschem\s+recht", r"german\s+law", r"gmbh",
    r"deutsche\s+rentenversicherung", r"krankenversicherung",
]
NON_REQUIREMENT_RX = re.compile("|".join(NON_REQUIREMENT_PATTERNS), re.IGNORECASE)

# Enumerating every perk phrasing is the same losing game as enumerating every
# level phrasing, so back the list above with a general rule: a sentence sitting
# in a BENEFITS context that names no skill level isn't a requirement.
BENEFIT_CONTEXT_RX = re.compile(
    r"(wir\s+bieten|we\s+offer|benefit|perk|zuschuss|bezuschuss\w*|contribution|"
    r"verg(ü|ue)nstigung|rabatt|discount|gutschein|voucher|erstattung|reimburse\w*|"
    r"kostenlos\w*|free\s+of\s+charge|monthly\s+contribution|monatlich\w*|"
    r"mitgliedschaft|membership|urban\s?sports|gym|kantine|obst|snacks?|"
    r"altersvorsorge|pension|jobrad|bike\s?leasing|team\s?event)",
    re.IGNORECASE,
)


def _is_requirement_sentence(sent: str) -> bool:
    """False when a German mention is clearly a perk/benefit, not a demand."""
    if NON_REQUIREMENT_RX.search(sent):
        return False
    # A benefits sentence that states no level is not a requirement. If it DOES
    # state a level ("sehr gute Deutschkenntnisse"), keep it — some ads put the
    # requirement inside an otherwise benefit-shaped paragraph.
    if BENEFIT_CONTEXT_RX.search(sent):
        lvl, _ = _level_of(sent)
        return lvl is not None
    return True

# Sentence + clause splitting.
# NOTE: deliberately NOT splitting on "?" — a question mark never ends a
# requirement in a job ad, but a softener often follows one
# ("Sehr gute Deutschkenntnisse? Gerne, aber kein Muss."). Splitting there
# would orphan the softener into a sentence with no German token and lose it.
# Bullets ("•") and newlines still split, so a "nice to have" on the *next*
# bullet is correctly not borrowed by this one.
SENT_SPLIT = re.compile(r"(?<=[.!;:•])\s+|\n+|•|\|")
CLAUSE_SPLIT = re.compile(r",|\bund\b|\bsowie\b|\band\b|/|\bor\b|\boder\b", re.IGNORECASE)

LEVEL_NAMES = {
    0: "none", 1: "A1", 2: "A2", 3: "B1", 4: "B2", 5: "C1", 6: "C2/native",
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[ \t\xa0]+", " ", text)


def _level_of(fragment: str) -> tuple[int | None, str]:
    """Highest level asserted in a fragment, plus the phrase that triggered it."""
    best, evidence = None, ""
    for rx, lv in LEVEL_RX:
        m = rx.search(fragment)
        if m and (best is None or lv > best):
            best, evidence = lv, m.group(0)
    return best, evidence


@dataclass
class LanguageVerdict:
    accepted: bool
    level: int | None                 # None = never stated
    level_name: str
    reason: str
    evidence: str = ""
    confident: bool = True
    sentences: list[str] = field(default_factory=list)

    @property
    def checked(self) -> bool:
        """False when we never actually read a description for this job."""
        return self.reason != "empty description"

    @property
    def badge(self) -> str:
        # "not checked" must never render as "no German mentioned". The first
        # means we never read the posting; the second means we read it and it
        # was clean. Conflating them silently overstates how much was verified.
        if not self.checked:
            return "⚠ not checked"
        if self.reason.startswith("German only appears as a perk"):
            return "no requirement (perk only)"
        if self.level is None and self.reason.startswith("no German"):
            return "no German mentioned"
        if self.reason == "negated":
            return "German explicitly not required"
        if self.reason == "optional":
            return "optional" if self.level is None else f"{self.level_name} · optional"
        if self.level is None:
            return "level unstated"
        return self.level_name


def assess_german(description: str, cap: int | None = None) -> LanguageVerdict:
    cap = config.GERMAN_CAP if cap is None else cap
    text = _norm(description)

    if not text.strip():
        return LanguageVerdict(
            accepted=config.ACCEPT_WHEN_UNCLEAR, level=None,
            level_name="unknown", reason="empty description", confident=False,
        )

    # 1. Find sentences that talk about German specifically.
    sentences = [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]
    german_sents = [s for s in sentences if GERMAN_TOKEN.search(s)]

    # Drop sentences where "German" is a perk, a transit pass or a legal form
    # rather than a skill being asked for.
    perk_sents = [s for s in german_sents if not _is_requirement_sentence(s)]
    german_sents = [s for s in german_sents if _is_requirement_sentence(s)]

    if not german_sents:
        return LanguageVerdict(
            accepted=True, level=None, level_name="not mentioned",
            reason=("German only appears as a perk or aside, not a requirement"
                    if perk_sents else
                    "no German reference anywhere in the posting"),
            evidence=perk_sents[0][:240] if perk_sents else "",
        )

    # 2. Explicit negation anywhere in a German sentence wins outright.
    for s in german_sents:
        if NEGATION_RX.search(s):
            return LanguageVerdict(
                accepted=True, level=0, level_name="none", reason="negated",
                evidence=s[:240], sentences=german_sents[:4],
            )

    # 3. Score each German sentence. Clause-level first so that
    #    "Verhandlungssicheres Englisch und gute Deutschkenntnisse"
    #    is read as B1-German, not C1-German.
    strictest, evidence, confident = None, "", True

    for sent in german_sents:
        clauses = [c.strip() for c in CLAUSE_SPLIT.split(sent) if c.strip()]
        de_clauses = [c for c in clauses if GERMAN_TOKEN.search(c)]

        lvl, ev = None, ""
        for c in de_clauses:
            cl, ce = _level_of(c)
            if cl is not None and (lvl is None or cl > lvl):
                lvl, ev = cl, ce

        if lvl is None:
            # No level inside the German clause. If the sentence also mentions
            # English, the level probably belongs to English — don't borrow it.
            # Otherwise fall back to the sentence, flagged as lower confidence.
            slvl, sev = _level_of(sent)
            if slvl is not None:
                lvl, ev = slvl, sev
                if ENGLISH_TOKEN.search(sent):
                    confident = False

        if lvl is None:
            continue

        # An optionality marker downgrades a hard requirement to "nice to have".
        if OPTIONAL_RX.search(sent):
            return LanguageVerdict(
                accepted=True, level=lvl, level_name=LEVEL_NAMES[lvl],
                reason="optional", evidence=sent[:240],
                confident=confident, sentences=german_sents[:4],
            )

        if strictest is None or lvl > strictest:
            strictest, evidence = lvl, sent[:240]

    # 4. German mentioned, but no level ever stated. Check optionality even
    #    here — "German is a plus" names no level yet is clearly not a hard
    #    requirement, and calling that merely "unstated" hides a clean accept.
    if strictest is None:
        soft = next((s for s in german_sents if OPTIONAL_RX.search(s)), None)
        if soft:
            return LanguageVerdict(
                accepted=True, level=None, level_name="optional",
                reason="optional", evidence=soft[:240],
                sentences=german_sents[:4],
            )
        return LanguageVerdict(
            accepted=config.ACCEPT_WHEN_UNCLEAR, level=None,
            level_name="unstated", reason="German mentioned but no level given",
            evidence=german_sents[0][:240], confident=False,
            sentences=german_sents[:4],
        )

    ok = strictest <= cap
    return LanguageVerdict(
        accepted=ok, level=strictest, level_name=LEVEL_NAMES[strictest],
        reason="within cap" if ok else f"requires {LEVEL_NAMES[strictest]}, above your {LEVEL_NAMES[cap]}",
        evidence=evidence, confident=confident, sentences=german_sents[:4],
    )
