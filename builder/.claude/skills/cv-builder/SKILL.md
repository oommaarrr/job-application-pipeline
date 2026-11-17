---
name: cv-builder
description: Write a tailored CV and a cover letter for a specific job posting, from the facts in the active profile. Use it whenever a CV, resume, Lebenslauf, cover letter or Anschreiben is being written or reviewed, when judging whether a role fits, or when phrasing an experience or project for an application.
---

# CV builder

How to turn one job posting plus one person's real history into a CV and a cover
letter that get read. These rules come from building a few hundred real
applications and watching which ones got answered. Every rule is here because
the opposite was tried and failed.

This file is **technique only**. It holds no facts about any person.

---

## 0. Where the facts come from

Everything true about the candidate lives in their profile, which the build
links at `profile/`:

| File | Holds |
|---|---|
| `profile/identity.md` | name as it goes on the CV, contact line, location, availability, languages, **employment history and education exactly as they go on the CV**, anything private (work authorisation, salary expectation) |
| `profile/profile.md` | what they are looking for and what they have done, in brief |
| `profile/preferences.json` | their limits and CV settings: roles, kinds of job, languages, years, places, management, CV shape and length. `shortlist.py` prints them in plain words |
| `profile/reference/PROJECTS.md` | every project, what it proves, the real numbers |
| `profile/reference/projects/` | long-form write-ups, read when a posting leans on that project |

Rows in `PROJECTS.md` copied word for word from the fictional example (Demand forecasting service, Document retrieval pipeline, Model release gate, Open-source contribution,
with the example's own descriptions) were left over from the template and are not
this person's: ignore them. A row of their own that only shares a name is theirs.

**Never write a fact that is not in those files.** Not a date, not a number, not
a tool, not an employer. If something the posting needs is missing, say so and
leave it out. Where `PROJECTS.md` and a long-form write-up disagree on a number,
`PROJECTS.md` wins.

`identity.md` also holds details that must **never** appear on a document
(salary, work authorisation, a legal name different from the CV name). Use them
only to answer a direct question in chat.

---

## 1. CV structure

As long as the person's preferences say (`cv_max_pages`): one A4 page filled top
to bottom, or two or three when they have the career to fill them (long CVs are
normal in academia, medicine, law and for senior executives). How experience is
shown follows `cv_shape`:

- **projects**: bullets lead with the named thing that was built or delivered
  (engineering, research, design).
- **achievements**: bullets are results inside each role, led by the outcome and
  its number (sales, marketing, operations, finance, management, consulting).

Students and early careers use whichever shows their real work best: an
internship, a working-student job, a thesis, university or side projects.

```
NAME
Tagline that mirrors the posting's job title
City, Country · email · phone · linkedin · github
────────────────────────────────────────────────
Summary: two or three sentences.

EXPERIENCE
Title, Employer                          (most relevant role first)
Month Year to Month Year · City · one-line context
• Label: what was built or achieved, the outcome, a number.
• ...

SELECTED PROJECTS                        (only when they earn the space)
Project name, context
• ...

EDUCATION
Degree, Institution                      grade if it is strong
Thesis or focus line, in full

SKILLS
Label: items · items · items             (three to five labelled lines)
Languages: ...
```

Order sections by what the posting cares about most, but Experience almost
always leads. A reader should meet named, shipped work before they meet an
adjective.

### The payload `build_docs.py` renders

```json
{
  "name": "Alex Rivera",
  "tagline": "Machine Learning Engineer",
  "contact": ["Berlin, Germany · alex@example.com · +49 30 0000000 · linkedin.com/in/example"],
  "summary": "Two or three sentences.",
  "sections": [
    {"title": "Experience", "type": "experience", "items": [
      {"role": "Machine Learning Engineer", "org": "Acme Logistics",
       "meta": "Jan 2023 to present · Berlin · one line of context",
       "bullets": ["<b>Demand forecasting service:</b> built and operate ..."]}
    ]},
    {"title": "Education", "type": "experience", "items": [
      {"role": "M.Sc. Computer Science", "org": "Example University",
       "meta": "2021 to 2023 · Thesis: full title here", "bullets": []}
    ]},
    {"title": "Skills", "type": "skills", "items": [
      ["Machine learning", "PyTorch, scikit-learn, model evaluation"],
      ["Languages", "English fluent, German A2"]
    ]}
  ]
}
```

A section with `"type": "skills"` takes `[label, value]` pairs. Every other
section takes items with `role`, optional `org`, optional `meta`, and `bullets`.
Bullets may use `<b>` and `<i>`, nothing else.

---

## 2. Cover letter structure

One page. Four short paragraphs, each doing one job:

1. **Why this company, specifically.** Something true about what they build or
   the problem the role solves. Never a sentence that fits any company.
2. **The strongest evidence, mapped to their top requirement.** One project or
   result, one outcome, one number.
3. **Range.** A second, different piece of evidence covering the next
   requirement, so the letter is not one story told twice.
4. **Close.** Availability and a plain invitation to talk. No begging, no
   "I would be thrilled".

### The letter payload

```json
{
  "sender": ["Alex Rivera", "Berlin, Germany", "alex@example.com · +49 30 0000000"],
  "recipient": ["Hiring team", "Acme Logistics", "Berlin"],
  "date": "Berlin, 3 March 2027",
  "subject": "Application: Machine Learning Engineer",
  "salutation": "Dear hiring team,",
  "paragraphs": ["...", "...", "...", "..."],
  "closing": "Kind regards,",
  "signature": "Alex Rivera"
}
```

Address a named person only when the posting names one.

---

## 3. Fill the page

A one-page CV that stops two thirds of the way down reads as a thin candidate,
whatever is on it. `build_docs.py` measures the rendered page and prints a note
when a single-page CV fills less than 90% of it. It never refuses: you decide.

When the page is short and the profile has more that fits the posting, add
**real content**, never bigger spacing or longer words. Reach for what this
person has:

- more experience bullets, from more of their roles, projects or results;
- for a projects CV, a **Selected Projects** section chosen for this posting;
- for an achievements CV, key accounts, deals, campaigns, launches,
  certifications or awards from the profile;
- an Education block with content: the thesis or focus, the grade if it is
  strong, relevant coursework for a student;
- three to five **labelled skills lines** instead of one or two.

When the profile has nothing more, a shorter page is better than padding: keep
it and name what is missing in the role's flags.

---

## 4. Writing bullets

**For a projects CV, open each experience bullet with a bold label naming the
real project**: `<b>Demand forecasting service:</b> ...`. The label says what
was built before the sentence says anything else, and it lets a skimming reader
count projects. A label names a project, never a topic: "Reliability:" on a
third bullet about the same system looks like range and is not.

**For an achievements CV, lead each bullet with the result**: `Grew the DACH
mid-market book from EUR 1.2M to EUR 2.1M ARR in 18 months`. A bold label is
optional (a key account, a campaign); a bold number alone is fine.

- **Show range.** One system or one deal described five times reads as one
  thing, not a career. Spread the page across what the person has done, as far
  as their profile allows; how many bullets each gets is your judgement for
  this posting. `check_spread.py` shows the spread; it never fails.
- **At most one "war story" per CV** (a bug found, an incident fixed), and only
  when the posting asks for debugging, reliability or security. Two read as a
  pattern.
- **Numbers over adjectives.** "Cut forecast error 14%" beats "significantly
  improved accuracy". Use only numbers that are in the profile.
- **Never shrink a project to fit a posting.** Tailoring changes which project
  leads and which detail gets the extra clause. It never changes what the thing
  is. If only one part of a system matches the posting, still describe the whole
  system, then say more about that part.

### The interview test

Before a bullet ships, ask: could the candidate say this sentence out loud in an
interview and then answer the obvious follow-up without notes?

If a bullet names an internal data structure, a hash, a key or a field to make
its point, it fails. **Name the outcome, not the mechanism.** "Built protection
against duplicate side effects when a job resumes after a crash" is honest,
checkable and easy to talk through. The mechanism belongs in the project's
write-up, for interview prep, not compressed into one clause on the CV.

---

## 5. The summary

It is the first prose a screener reads, and it is where generated text gives
itself away. Rules:

- **Two or three sentences, in CV register.** Subject-dropped, clipped:
  "Machine learning engineer who builds and runs forecasting models in
  production", not "I have always been fascinated by data".
- **Lead with a hard fact**, not a self-assessment. What was built, where, at
  what scale.
- **Sentence three is tailored**: the one capability this posting cares about
  most, drawn from real work.
- **Banned**: "passionate", "proven track record", "results-driven", "thrive",
  "wear many hats", "comfortable in ambiguity", "I am someone who", "with a
  strong foundation in", "leverage", "seamless", "robust", "cutting-edge". If a
  sentence would fit on anyone's CV unchanged, rewrite it around a specific.

A good one: "Machine learning engineer at a Berlin logistics company, where I
built and operate the demand forecasting service that plans 40 warehouses.
Owns models from training through monitoring, including the evaluation gate
every release must pass. Strongest where this role needs it most: getting
models into production and keeping them honest there."

---

## 6. Tailoring: every CV is its own

- **The tagline mirrors the posting's job title** as closely as the truth
  allows. "Applied AI Engineer" for an Applied AI Engineer posting, not a generic
  "Software Engineer".
- **Put the posting's own words on the page.** Screening software and skimming
  humans both look for the exact named stack, tools and domain terms. Where the
  candidate has used the thing, it goes in a bullet. Where they have not but
  could credibly pick it up, it may go in a skills line as familiarity.
- **Every CV must read differently from the last one.** Two CVs that differ only
  in the company name are a failure. Pull these levers every time, in order of
  impact: tagline, summary, which projects lead, bullet order, which skills lines
  appear and the words inside them.
- **The document language follows the posting when the person can work in it.**
  A German posting gets German documents when their German (in their
  preferences) is B2 or better, native, or not stated. When it is lower, write in
  the strongest language of theirs that the posting accepts (most often English),
  and say so in the role's flags: a CV in a language they cannot interview in
  misrepresents them.
- **Languages by market.** List a language when it helps in that market, for
  example a regional language for a role serving that region. Do not pad the line
  with languages that change nothing.

---

## 7. Honesty

Two rules that pull in opposite directions, and both hold.

**The documents never concede.** No weakness, gap or shortfall is stated on the
CV or in the letter: no "my German is basic", no "about two years rather than
three", no "growing into X", no "the frontend is the lighter part of my
profile". A document is not the place to argue the reader out of a meeting. The
honest caveats go in the chat assessment (see section 9), never on the page.

**The documents never invent.** Never a tool used in production that was not,
never a years count, never an employer, a title, a certification or a metric
that is not in the profile. Naming a tool as familiarity is fair; claiming to
have shipped it for three years is not.

Frame anything less familiar as capability, not as a gap: "comfortable working
across the React frontend", never "frontend is not my strength".

**Ownership, stated as ownership.** When the candidate built something alone,
say so: "designed, built and shipped end to end", "owned the architecture
decisions". Never phrase it as an absence of oversight ("no senior engineer
reviewed it"). That reads as a complaint about a former employer and makes the
work sound unvetted.

**Seniority comes from scope, not from years.** Describe what was owned and
decided, and let the reader conclude. State a year count only when the person's
preferences say to (`cv_state_years`), and then only the true one from the
profile. Do not hedge the level when the scope supports it.

---

## 8. Format

- Black text only. No colour, no tables, no columns, no icons, no photos.
- **No em dashes, en dashes or double hyphens anywhere.** `build_docs.py` refuses
  them. Use a comma, or restructure the sentence. Hyphens inside compound words
  are fine.
- A4. Filenames `<First>_<Last>_CV_<Company>.pdf` and
  `<First>_<Last>_CoverLetter_<Company>.pdf`, using the CV name from
  `identity.md`.
- Dates as "Mon Year to Mon Year". "Present" for a current role.
- The PDF must open and parse as text: an applicant tracking system reads the
  text layer, not the picture.

---

## 9. Judging fit, before writing anything

Give a short, honest assessment in chat for each role: strong fit, fit with soft
flags, or a hard blocker. This is the one place gaps are named plainly.

**Do not build, say why, only when the posting breaks one of the person's own
limits** (`preferences.json`, printed by `shortlist.py`):
- a language required above the level they have, when their languages are listed;
- more years explicitly required than their limit, when they set one;
- a kind of job they did not choose (internship, working student, regular,
  freelance...), when their kinds are set;
- work their limits say is not theirs, or a field far from every role they want;
- a role that is mainly managing a team, only when management is not wanted;
- a place or way of working they rule out.

A limit that is not set rules nothing out, and nothing else is a blocker.

**Soft flags: build anyway, and keep them out of the documents.** A preferred
language, a stack the candidate has not used, a stretch in seniority. Mirror
the stack as capability and move on.

**Titles are noise.** "Senior", "Staff", "Lead" alone decide nothing. Startups
inflate titles; a "Senior" role that asks for two years is exactly the stretch
worth taking. The description's own requirements decide.

---

## 10. Checklist before a document ships

- [ ] Every fact is in the profile. Nothing invented, no number rounded up.
- [ ] Name, contact line and dates match `identity.md` exactly.
- [ ] Tagline mirrors the job title. Summary is two or three sentences, leads
      with a fact, and uses none of the banned words.
- [ ] Bullets follow the CV shape (named projects, or results inside each role),
      the page shows range across what the person has done, at most one war story.
- [ ] The posting's key terms and named stack appear on the page.
- [ ] No weakness or gap is stated anywhere in the CV or the letter.
- [ ] Every bullet passes the interview test.
- [ ] It reads differently from the previous CV in the batch.
- [ ] Education carries the thesis or focus line when the profile has one.
- [ ] Three to five labelled skills lines; languages chosen for this market.
- [ ] No em dashes, en dashes or double hyphens. Black text, no tables.
- [ ] The CV is no longer than the person's page limit and a one-page CV fills
      its page when the profile has the content; the letter fits on one page.
- [ ] Document language matches the posting, or the person's strongest language the
      posting accepts when their level in the posting's language is below B2.
