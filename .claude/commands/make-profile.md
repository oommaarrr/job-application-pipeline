---
description: Create your profile from your existing CV (PDF, Word, or pasted text)
argument-hint: "path/to/your-cv.pdf  (or paste the CV text after the command)"
allowed-tools: Read, Write, Edit, Bash
---

Create the user's profile in `profiles/me/` from their existing CV.

The CV is at: $ARGUMENTS

If that is a file path, read it. If it is text, use the text. If nothing was
given, ask for the path to their CV and stop.

## Ask what the CV leaves open

A CV says what someone has done, not what they are looking for. Before writing,
ask the user, in one message, only what the CV does not already answer and that
changes which jobs they are shown:

- which roles they want next (offer what their recent work suggests);
- which kinds of job: a regular job, a working-student job, an internship, a
  thesis, an apprenticeship, freelance or contract work;
- where they would work, on site, hybrid or remote, and whether they would
  relocate;
- their level in each language the CV names without one;
- whether a role that mainly manages a team is fine;
- anything that rules a job out for them (a years limit, a field they are
  leaving).

Write their answers into `profile.md`. If they would rather not answer, write
nothing for that point: an unanswered point rules nothing out.

## What to write

Use `profiles/example/` as the template for all three files: the same
headings, filled in for this person, whatever their field. Replace every
fictional detail with the user's own.

1. **`profiles/me/identity.md`**: the name exactly as it should appear on a
   CV, the contact line (city, email, phone, LinkedIn, GitHub if present),
   languages with honest levels, availability if the CV states it, and the
   **Employment history** and **Education** sections: every employer with its
   exact name, title, city and dates, and every degree with institution, years,
   grade and the full thesis title. The CV builder takes these word for word.
2. **`profiles/me/profile.md`**: what each job is judged against. Target
   roles and kinds of job, where and how they would work, languages, a short
   experience summary, what they are strong at, what is **not their lane**,
   and **what rules a job out** for them, in their own terms (from their
   answers; leave out anything they did not say).
3. **`profiles/me/reference/PROJECTS.md`**: one row per project, result or
   major piece of work from the CV (for sales, marketing or operations, the
   achievements inside each role: quota, revenue, accounts, campaigns): what it
   proves, with every number the CV gives, and which kind of role it should
   lead for.

## Rules

- **Only facts from the CV.** Never invent a number, a tool, a date or a result.
  Where the CV is silent on something the template asks for, write `TODO:` and
  say what is needed, rather than guessing.
- Keep the example's headings so the ranker and the build can read the files.
- Do not touch `profiles/example/`.
- Do not write `profiles/me/preferences.json`. The pipeline fills it in from
  `profile.md` and `identity.md` by itself; if the example's copy came along,
  leave it, it is replaced as soon as these files are yours.

## Finish

List every `TODO:` you left, in one line each, and tell the user:

> Your profile is ready in `profiles/me/`. Read `profile.md` once: it decides
> which jobs are ranked highly, so fix anything that is not quite you. Then
> open the dashboard: it fills in your **Preferences** (roles, places,
> languages) from your profile within a minute. Check them, then press
> **Rank pool**.
