---
description: Build tailored CVs and cover letters for the top-ranked roles
argument-hint: "[how many to build, default: every role that fits]"
allowed-tools: Bash, Read, Write, Edit, Skill
---

Build this batch of applications. Target: $1.

**Whenever you stop on purpose without building** (the skill did not load, the
profile is missing or still the example, zero roles are buildable), end with one
line that starts `BATCH-STOP:` followed by the reason in plain words. The runner
reads it and does not retry, because retrying a deliberate stop only spends
usage to reach the same answer.

**The pool reaching you is already filtered and ranked.** A local model read
every scraped description first and kept only the survivors, in fit order, in
`../scraper/out/ranked.json`. Your job is to write documents, not to rank a
pool. That split exists because having a paid model rank a whole scrape once
used up an entire usage window without producing a single document.

**`$1` is a target, not the length of the list.** The shortlist deliberately
holds more roles than `$1`, because some can only be rejected once the
description is read. Work down the list in order and **stop the moment `$1`
applications are built.** If you drop one, take the next one down. Never build
past `$1`, and never stop below it while candidates remain.

## Parallel shard mode: check this first

```
echo "shard ${APPLY_SHARD:-1} of ${APPLY_SHARDS:-1}"
```

**If `APPLY_SHARDS` is 1 or unset, ignore this section.**

If it is greater than 1, you are one of several parallel workers, and three
things change:

1. **Your pool is only your slice.** `shortlist.py` prints only the roles
   assigned to you. Build from what it prints. `$1` is your own count.
2. **Do not write `batch.json` and do not run `batch_report.py`.** Write your
   results to `applications/<date>/batch.shard-<APPLY_SHARD>.json`, in the same
   shape as `batch.json`. The runner merges the shards afterwards.
3. **Run `check_spread.py` only on the payloads you wrote**, listed explicitly,
   never a glob that could catch another shard's half-written file.

## 0. Check the ranker's work, briefly

Read `../scraper/out/ollama_rank.txt`. It lists the roles sent to you, the ones
each gate dropped, and the ones that passed but fell below the cut.

Spend your attention on the roles you are about to build. You will read their
descriptions in full in step 1, so check what the local model extracted
(language level, years, role family) against the text. Skim the dropped
sections; if one is plainly wrong, say so in one line so the rule can be fixed.
Do not build it. If nothing looks wrong, say that in one line.

## 0b. Resuming an interrupted run

If `applications/<date>/RESUME.md` exists, an earlier attempt today stopped
partway. The companies it lists are finished and verified: do not rebuild, re-rank
or drop them. Build only the number it asks for, from roles not already built.
When you write `batch.json`, **merge**: keep every existing `built` entry, append
yours, renumber `rank` across the whole list.

## 1. Load the roles

```
.venv/bin/python shortlist.py --full
```

Read every description it prints. A CV written from a job title is worthless.
Always use `.venv/bin/python`, never bare `python3`, which may not have the PDF
libraries. That path works on Windows too: setup adds it to the venv for Git
Bash (if it is ever missing there, `.venv/Scripts/python.exe` is the same
interpreter). If it prints zero roles, stop and say so.

## 2. Load the facts and the rules

Invoke the `cv-builder` skill. **If it does not load, stop and say so** rather
than writing from memory.

Then read the candidate's profile: `profile/identity.md`, `profile/profile.md`
and `profile/reference/PROJECTS.md`. For a role that leans heavily on one
project, also read that project's file in `profile/reference/projects/`. These
files are the only source of facts. Nothing goes on a document that is not in
them.

If `profile/writing-rules.md` exists, read it in full too. It holds this
person's own standing rules (project labels, titles, corrections they have made
before). **Where it disagrees with the skill, it wins.**

Read `reference/DOCUMENT_WORKFLOW.md` and follow it for every document.

## 3. Decide what to drop

The local model is good at extraction and blunt at nuance. **Drop a role only
when reading its description shows a fact the ranker got wrong**, and name it:

- a language requirement above what `profile/identity.md` lists;
- more years explicitly required than the profile's own rule allows;
- a core capability the profile lists as out of lane;
- a different field entirely;
- a genuinely organisational role (Head of, Director, VP, or a job that is
  really managing people), not merely an inflated title;
- no usable description was captured;
- already applied, or a duplicate of another role in this pool.

**Judge the description, never the title.** "Senior", "Staff" and "Lead" are
not blockers on their own. A missing company name is not a reason to drop
either: the description almost always names the employer, so take it from there.

## 4. Report before you build

A few honest lines per role you will build: how strong the fit is, what lands,
what is thin. Name the soft flags plainly here. This is the only place gaps are
named; the documents never concede anything. Then list what you dropped and why,
one line each, and which role took its place. Keep the ranked order otherwise.

## 5. Build

For each role, write a CV payload and a letter payload (formats in the skill,
sections 1 and 2) into `applications/<date>/<Company>/`, then:

```
.venv/bin/python check_spread.py applications/<date>/<Company>/cv_<company>.json
.venv/bin/python build_docs.py cv     applications/<date>/<Company>/cv_<company>.json     applications/<date>/<Company>/<First>_<Last>_CV_<Company>.pdf
.venv/bin/python build_docs.py letter applications/<date>/<Company>/letter_<company>.json applications/<date>/<Company>/<First>_<Last>_CoverLetter_<Company>.pdf
```

- `<date>` is today, `YYYY-MM-DD`.
- `<Company>` is the employer the application is addressed to, letters and
  digits only (`Acme`, `PwC`). Use it identically in the folder and both
  filenames. When a posting names a brand or spinout, the employer wins.
- `<First>_<Last>` is the CV name from `profile/identity.md`.

As soon as both PDFs of a role are built, write its record beside them, in
`applications/<date>/<Company>/entry.json`: the same object that role will get
in `built` in step 6 (`company`, `title`, `location`, `url`, `why`, `flags`).
Do it per role, not at the end. A session can stop at any moment (a usage
limit), and a role with documents but no record was once built a second time
the next day. The runner reads these files if the batch record never gets
written.

`check_spread.py` fails on a project with more than two bullets, fewer than four
distinct projects, or a topic masquerading as a project label. Fix the payload
until it passes; never edit the checker. `build_docs.py` refuses em dashes, en
dashes, double hyphens and an underfilled page. When it refuses a thin CV, add
real content from the profile (skill, section 3), never spacing. Set
`ALLOW_SHORT_CV=1` only when the profile genuinely has nothing more to add, and
then say so in that role's `flags` in `batch.json` and name what the profile is
missing, so the user can fill it in.

Every CV in the batch must read differently from the others: its own tagline,
summary, lead project and skills lines.

Then confirm every PDF parses:

```
.venv/bin/python -c "
import pathlib
from pypdf import PdfReader
for p in sorted(pathlib.Path('applications/<date>').rglob('*.pdf')):
    print(len(PdfReader(str(p)).pages), p.stat().st_size, p.name)
"
```

A letter is one page; a CV is at most two.

## 6. Record the batch

Your reasoning is the most valuable thing this produces and it disappears when
the session ends. Write `applications/<date>/batch.json`:

```json
{
  "date": "2027-03-03",
  "pool_size": 19,
  "built": [
    {
      "rank": 1,
      "company": "Acme",
      "title": "Machine Learning Engineer",
      "location": "Berlin, Germany (Hybrid)",
      "url": "https://www.linkedin.com/jobs/view/0000000000/",
      "why": "Two or three honest sentences: why this is the closest match, which project carries it, anything thin.",
      "flags": ["German listed as a plus"],
      "files": {"CV": "Acme/Alex_Rivera_CV_Acme.pdf",
                "cover letter": "Acme/Alex_Rivera_CoverLetter_Acme.pdf"}
    }
  ],
  "dropped": [
    {"company": "Example GmbH", "title": "Data Engineer",
     "reason": "pipeline and warehousing work, out of lane"}
  ]
}
```

Every role you considered goes in one list or the other. `flags` is an empty
list when there are none. Then render the review page:

```
.venv/bin/python batch_report.py
```

## 7. Finish

Confirm how many documents were built and give the path to
`applications/<date>/batch.html`. Remind the user to press **Mark this job
applied** in the extension after sending each one, so the next batch skips it.
