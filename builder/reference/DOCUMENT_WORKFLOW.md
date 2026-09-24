# How every document gets built

**Plan, criticise, then build once. Never build, criticise, rebuild.**

Building a CV, tearing it apart, and rebuilding it from scratch produces a good
final document and wastes a full cycle on a first draft whose defects were
predictable before a line was written. The critique is worth keeping. Doing it
*after* the build is not.

---

## 1. Plan in markdown, before any payload

Write `applications/<date>/<Company>/PLAN.md` first. It is a working file, not a
deliverable, and it stays in the folder so a later tweak has the reasoning.

```markdown
# <Company> · <Role>

## What they actually ask for, in their words
1. "<exact phrase from the posting>"   → answered by: <real project name>
2. "<exact phrase>"                    → answered by: <real project name>
3. "<exact phrase>"                    → answered by: <real project name>
4. "<exact phrase>"                    → not answered (say so in chat only)

## Hard keywords to land
<the literal strings a filter would search for. Mark any that would be
dishonest to include.>

## Bullet order, with the project label for each
1. <b>Project A:</b> answers requirement 1
2. <b>Project B:</b> requirement 2
3. <b>Project C:</b> requirement 3
4. <b>Project D:</b> breadth
Spread check: N distinct projects, max 2 from any one, war stories: 0 or 1

## Selected Projects, and why each earns the space
## Tagline and summary angle, in one line
```

## 2. Criticise the plan in two voices, in the same file

Both are hostile on purpose. Write the findings down.

**The applicant tracking system.** Mechanical, not stylistic:

- Reverse chronological order intact? The first Experience entry is what a parser
  extracts as the current role.
- Which literal keywords from the posting are missing? List them.
- Is a required keyword absent only because it would be dishonest? Then it stays
  absent, and it goes in the chat assessment.
- Are the skills labels parseable buckets ("Programming languages", "Databases",
  "Cloud"), not clever phrases?
- Is the matching evidence in Experience, or stranded in Selected Projects, which
  scores lower?
- Do the job titles carry a signal that fights the posting?
- Single column, no tables, no photo, a real text layer, two pages at most.

**A senior recruiter with seven seconds and a thousand CVs.** Human:

- What does the eye hit after the name? If the tagline and the first job title
  disagree, that is the whole review for most of the pile.
- Is the strongest evidence for the top requirement a job, or a side project?
- Is any bullet over about 50 words? Nobody reaches the good number at the end.
- Does anything say why *this* company, or would this CV go anywhere unchanged?
- Does the level match? Over-pitching a junior role reads as "will decline our
  offer"; under-pitching a senior one reads as unqualified.
- Is there an obvious unanswered objection? Answer it on the page or in the
  letter, without conceding a weakness.

## 3. Revise the plan, then build once

Fix the plan against both critiques. Only then write the JSON payloads.

## 4. Verify mechanically

```bash
.venv/bin/python check_spread.py applications/<date>/<Company>/cv_<company>.json
.venv/bin/python build_docs.py cv     <cv>.json     <out>.pdf
.venv/bin/python build_docs.py letter <letter>.json <out>.pdf
```

Then confirm the PDFs parse and the keywords actually landed:

```bash
.venv/bin/python - <<'PY'
import pathlib
from pypdf import PdfReader
for p in sorted(pathlib.Path('applications/<date>').rglob('*.pdf')):
    r = PdfReader(str(p)); t = ''.join(x.extract_text() for x in r.pages)
    print(len(r.pages), p.name, 'dashes:', [c for c in t if c in '–—'])
PY
```

A letter is one page, a CV at most two, and no dashes.

## 5. Flag what could still reject it

In chat, after handing over the files: the requirements that are genuinely unmet,
ranked. Do not soften them, and do not invent an experience to close one.

---

## The rule that overrides speed

**Never write a fact to close a gap.** When a keyword is missing because the fact
is unknown, say which fact and where it would go, and ask. Asking usually turns
up something true that could not have been guessed; inventing turns up an
interview question the candidate cannot answer.

The same goes for titles. Removing "Intern" or "Working Student" to fix a
screening problem is inflation unless the profile says the title was wrong.
