#!/usr/bin/env python3
"""
Test the German gate and the years extractor against realistic phrasings
lifted from how German job ads are actually written.

    ./.venv/bin/python test_gate.py
"""

from experience import extract_years
from language_gate import assess_german

# (description, expect_accept, note)
LANG_CASES = [
    # ---- no German mentioned at all -> ACCEPT (your explicit rule) ----
    ("We are looking for a Python engineer. Strong backend skills required.",
     True, "no German reference anywhere"),
    ("Du arbeitest mit Python und Kubernetes an unserer Plattform.",
     True, "German-language ad, but no German *requirement* stated"),

    # ---- at or below B1 -> ACCEPT ----
    ("Gute Deutschkenntnisse in Wort und Schrift.", True, "B1 'gute'"),
    ("Deutschkenntnisse auf B1-Niveau.", True, "explicit B1"),
    ("Grundkenntnisse in Deutsch sind ausreichend.", True, "A1 Grundkenntnisse"),
    ("Erweiterte Grundkenntnisse der deutschen Sprache.", True, "A2"),
    ("Konversationssicheres Deutsch.", True, "B1 konversationssicher"),
    ("Solide Deutschkenntnisse runden dein Profil ab.", True, "B1 solide"),

    # ---- above B1 -> REJECT ----
    ("Sehr gute Deutschkenntnisse in Wort und Schrift.", False, "B2 'sehr gute'"),
    ("Fließende Deutschkenntnisse setzen wir voraus.", False, "B2 fliessend"),
    ("Verhandlungssichere Deutschkenntnisse sind erforderlich.", False, "C1"),
    ("Deutsch auf muttersprachlichem Niveau.", False, "C2/native"),
    ("Deutschkenntnisse auf C1-Niveau zwingend erforderlich.", False, "explicit C1"),
    ("Exzellente Deutschkenntnisse.", False, "C1 exzellent"),
    ("You must have native-level German.", False, "English-language, native"),
    ("Deutschkenntnisse mindestens B2.", False, "explicit B2"),

    # ---- optionality markers override the level -> ACCEPT ----
    ("Sehr gute Deutschkenntnisse sind von Vorteil.", True, "B2 but 'von Vorteil'"),
    ("Fließendes Deutsch ist wünschenswert.", True, "B2 but 'wünschenswert'"),
    ("Idealerweise verfügst du über verhandlungssichere Deutschkenntnisse.",
     True, "C1 but 'idealerweise'"),
    ("German language skills are a plus.", True, "English 'a plus'"),
    ("Deutschkenntnisse nice to have.", True, "nice to have"),
    ("Sehr gute Deutschkenntnisse? Gerne, aber kein Muss.", True, "'kein Muss'"),

    # ---- explicit negation -> ACCEPT ----
    ("Keine Deutschkenntnisse erforderlich.", True, "negated"),
    ("Deutschkenntnisse sind nicht zwingend erforderlich.", True, "negated"),
    ("No German required — our working language is English.", True, "negated EN"),
    ("Unternehmenssprache ist Englisch, Deutschkenntnisse sind nicht notwendig.",
     True, "company language English"),

    # ---- the hard one: level belongs to ENGLISH, not German ----
    ("Verhandlungssichere Englischkenntnisse und gute Deutschkenntnisse.",
     True, "C1 English + B1 German -> accept on the German clause"),
    ("Fließendes Englisch erforderlich, Deutsch von Vorteil.",
     True, "English C1, German optional"),
    ("Sehr gute Englischkenntnisse sind ein Muss.",
     True, "English only — no German requirement at all"),

    # ---- shared modifier across a compound ----
    ("Sehr gute Deutsch- und Englischkenntnisse in Wort und Schrift.",
     False, "'sehr gute' modifies German too -> B2 -> reject"),

    # ---- German mentioned, no level -> ACCEPT + flag (lightest filter) ----
    ("Du kommunizierst auf Deutsch mit unseren Kunden.", True, "unstated level"),
    ("Deutschkenntnisse erforderlich.", True, "required but no level -> lightest"),

    # ---- strictest-wins across multiple sentences ----
    ("Gute Deutschkenntnisse. Darüber hinaus erwarten wir verhandlungssicheres "
     "Deutsch für Kundengespräche.", False, "max across sentences -> C1"),
]

EXP_CASES = [
    ("Mindestens 3 Jahre Berufserfahrung im Bereich Softwareentwicklung.", 3),
    ("2-4 Jahre Erfahrung mit Python.", 2),
    ("5+ years of experience in machine learning.", 5),
    ("At least two years of professional experience.", 2),
    ("Du hast erste Erfahrung in der Softwareentwicklung.", 0),   # qualitative
    ("Der Vertrag ist auf 2 Jahre befristet.", None),   # not an experience req
    # Cumulative requirements -> the job demands the HIGHER figure.
    ("3 Jahre Erfahrung mit Python, 5 Jahre Erfahrung mit AWS.", 5),
    ("5+ years shipping production software, 2+ building with LLMs", 5),
    # Qualitative German phrases (very common; must not read as "not stated")
    ("Mehrjährige Berufserfahrung in der technischen Umsetzung.", 3),
    ("Langjährige Erfahrung in der Softwareentwicklung.", 5),
    ("Einschlägige Berufserfahrung im Bereich Data Science.", 2),
    ("Fundierte praktische Erfahrung mit Python.", 3),
]

EXP_TITLE_CASES = [
    ("", "Junior AI Engineer", 0),
    ("", "Senior Machine Learning Engineer", 5),
    ("", "Working Student Data Science", 0),
    ("", "Head of AI", 8),
    ("", "AI Engineer", None),
]


def main() -> int:
    fails = 0

    print("─" * 74)
    print(" GERMAN GATE".ljust(74))
    print("─" * 74)
    for text, expect, note in LANG_CASES:
        v = assess_german(text)
        ok = v.accepted == expect
        fails += not ok
        mark = "✓" if ok else "✗ FAIL"
        got = "accept" if v.accepted else "reject"
        flag = "" if v.confident else "  (low confidence)"
        print(f" {mark:<7} {got:<7} {v.level_name:<11} {note}{flag}")
        if not ok:
            print(f"         └─ {text[:96]}")
            print(f"            reason={v.reason!r} evidence={v.evidence[:70]!r}")

    print("\n" + "─" * 74)
    print(" YEARS FROM DESCRIPTION".ljust(74))
    print("─" * 74)
    for text, expect in EXP_CASES:
        e = extract_years(text)
        ok = e.years == expect
        fails += not ok
        print(f" {'✓' if ok else '✗ FAIL':<7} got={str(e.years):<6} "
              f"want={str(expect):<6} {text[:52]}")

    print("\n" + "─" * 74)
    print(" YEARS FROM TITLE".ljust(74))
    print("─" * 74)
    for desc, title, expect in EXP_TITLE_CASES:
        e = extract_years(desc, title)
        ok = e.years == expect
        fails += not ok
        print(f" {'✓' if ok else '✗ FAIL':<7} got={str(e.years):<6} "
              f"want={str(expect):<6} {title}")

    total = len(LANG_CASES) + len(EXP_CASES) + len(EXP_TITLE_CASES)
    print("\n" + "═" * 74)
    print(f"  {total - fails}/{total} passed" + ("" if not fails else f"  ·  {fails} FAILING"))
    print("═" * 74)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
