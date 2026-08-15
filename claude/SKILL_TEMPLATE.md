---
name: my-cv-builder
description: Build and tailor my CV and cover letters. Use whenever I paste a job description, job link or job title, ask for a CV, resume, Lebenslauf, cover letter or motivation letter, ask whether a role is a good fit, ask how to phrase an experience, or ask anything about my own background, dates, projects, skills or gaps. Also use when I say "tailor this", "just CV", "build it", or send a posting with no instructions at all. This file is the source of truth for every fact about me, so consult it before writing any application material rather than relying on memory.
---

# CV builder for <YOUR NAME>

Everything needed to assess fit, tailor a CV, and write a cover letter.
**Facts here override anything else. If a fact is missing, ask rather than
inventing it.**

---

## 1. Precedence of truth

1. What I say in the current conversation.
2. This file.
3. Anything else, including older uploaded documents, which go stale.

---

## 2. Identity and contact

- CV name: <name as it should appear>
- Email, phone, city
- GitHub, LinkedIn
- Full postal address, **only** for formal applications in countries that
  expect one
- **Availability: <date>.** Goes in every cover letter and application form.
- Salary expectation when asked: <number, or "ask me">
- Anything about visas or permits: keep it here, never on a CV. It affects
  start dates and contract type only.

---

## 3. Education

Degree, institution, years, thesis title and status. Certifications, with
"in progress" marked honestly.

---

## 4. Employment history

For each role: exact title, employer, location, exact start and end dates,
employment type, and the scale of the place (customers, team size, revenue,
whatever makes it real). Then what you actually did.

**Title flexibility:** list the honest variants of your own title so a CV can
mirror the target job title without inventing seniority. Never drop "Intern" or
"Working Student" if that is what it was.

---

## 5. Projects, in full

This is the section that does the work, and it should be long. For every
project worth putting on a CV:

- **The label to use on the CV.** A real project name, not a topic.
- What it is, in one line.
- The real numbers. Not "improved performance", but the figure you would defend.
- The stack.
- **War stories:** the specific things that went wrong and what you did. These
  are the best interview material you own and they are what makes a CV concrete
  rather than generic.

Add a rule about how many of these may appear on one CV. Mine is: at most two
bullets from any single project, at least three distinct projects, at most one
war story per document. `pipeline/check_spread.py` enforces exactly that.

---

## 6. Skills inventory

Group them the way an ATS parses them: Programming Languages, Backend,
Databases, Cloud, Testing, and so on.

### Honest gaps, never claim past these

The most valuable section in the file. List what you have **not** done, in
detail:

- languages and frameworks you have only read about
- cloud platforms you know at concept level rather than production
- anything you have used in a project but never at scale
- domains you have no experience in

Keep it current. A stale gaps list is worse than none, because it silently
costs you keyword matches you could honestly claim. Whenever you correct one,
correct it here.

---

## 7. Document conventions

Format rules, tone rules, and anything your PDF builder enforces. Mine:

- black text only, no tables, A4, two pages maximum
- no em dashes, en dashes or double hyphens anywhere
- vary sentence openers, avoid leverage, seamless, robust, cutting edge
- concrete numbers beat adjectives
- match the document language to the posting language

---

## 8. Application logic

**Standing rules.** Mine, as an example:

1. Every job description pasted gets both a CV and a cover letter, unless I say
   "just CV".
2. The current role leads, and every bullet opens with a bold project label.
3. Non-work projects appear only when they carry something the job history does
   not.

**Hard blockers, do not build, flag and ask:** language requirements above your
level, unpaid roles, seniority far beyond you, an entirely different field.
Be specific, because a vague blocker list gets applied as taste.

**Soft flags, apply anyway and address in the letter:** language listed as a
plus, a stack you have not used, a role slightly senior.

**Workflow when a posting is pasted:**

1. Short fit assessment: strong, soft flags, or blocker, with reasons.
2. If blocked, say so and ask before building.
3. Otherwise build both documents without asking.
4. Reorder bullets so the top three answer the posting's top three requirements.
5. Mirror the posting's own vocabulary where it is honest to do so.

---

## 9. Build pipeline

Point at your scripts and say how to run them. See this repository's README.

---

## 10. Checklist before sending

- [ ] Dates and employers correct
- [ ] Availability correct
- [ ] No forbidden dashes
- [ ] Tagline mirrors the job title
- [ ] Both documents built unless told otherwise
- [ ] Top three bullets map to the posting's top three requirements
- [ ] Nothing claimed that is on the gaps list
- [ ] PDFs parse, page count correct
