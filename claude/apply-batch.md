---
description: Rank the open roles by fit and build tailored CVs and cover letters for the top ones
---

Build this batch of applications. Batch size: $ARGUMENTS (default 5 if empty).

## 0. Audit the filters first

Read the scraper's audit file in full. It is built to be read in one pass: every
job is a single line, a rejected one carries the exact sentence that condemned
it, and a job that passed carries a short excerpt.

The gate and the scorer are lexical, so they fail in ways only a reader catches:
a sentence about English that happens to mention German, an optionality marker
missed, a title whose wording hides a real match. Go through the whole file once
and flag by id only where a decision looks wrong.

Two directions to check:

- **False rejects.** A language verdict whose evidence sentence does not
  actually demand that level, or an off-profile verdict on a title that is
  genuinely in the lane. These are the expensive ones, because a good job
  silently disappeared.
- **False accepts.** A passed job whose excerpt shows a hard requirement the
  gate read as optional, or that is plainly not the lane.

Report what you flagged in chat, with the id and one line of reasoning. A
wrongly rejected job is eligible for this batch: treat it as if it had passed
and say so. Do not edit the gate to force a result; if a rule is genuinely
wrong, say which rule and why.

If nothing looks wrong, say that in one line and move on. Do not invent
findings to look thorough.

## 1. Load the material

Run the shortlist script to print the open roles with their full descriptions.
Roles already applied to are excluded, so everything you see is genuinely open.

If it reports zero roles, stop and say so rather than inventing candidates.

## 2. Load the facts

Invoke the personal CV skill. It is the source of truth for dates, employers,
skills, the gaps that must never be claimed past, and the document conventions.
Do not write a single line from memory of this conversation.

Then read the portfolio reference file: what each project proves, the real
numbers, and which project should lead for which kind of posting.

## 3. Rank by fit, not by the scraper's order

The scraper sorts mechanically. That is not the same as closest fit. Re-rank on
what the role actually is, and apply the hard blockers from the skill. Flag and
skip a blocker, do not build it.

## 4. Report before building

A few lines per selected role: how strong the fit is, what lands, what is thin
or a reach. Name the soft flags plainly. This is the chat assessment, so be
honest here. The documents themselves never concede anything the letter does not
deliberately concede.

Also list what you dropped and why, one line each.

## 5. Plan each document before building it

Write `applications/<date>/<Company>/PLAN.md` first: the posting's requirements
in its own words, which real project answers each, the literal keywords a filter
would search for, the bullet order with project labels, the gaps, and the
tagline angle.

Then criticise that plan in two voices **before building**:

- **As an ATS reviewer.** Reverse-chronological order intact? Which literal
  keywords from the posting are missing? Are the skills headers parseable
  buckets? Is the matching evidence stranded in a projects section, which scores
  lower than experience? Single column, no tables, page count.
- **As a senior HR screener with a thousand CVs and seven seconds.** What does
  the eye hit after the name? Do the tagline and the first job title agree? Are
  bullets over about fifty words? Does anything say why *this* company? Does the
  level match? What is the obvious unanswered objection, and is it answered?

Revise the plan against both, then build once. Never build, criticise and
rebuild: the critique is worth keeping, the wasted build is not.

## 6. Build

For each role write two JSON payloads and run the builder for the CV and the
letter, into `applications/<date>/<Company>/`. Keep the payloads in that folder
so a later tweak does not mean starting over.

Non-negotiables from the skill: the current role leads, every bullet in it opens
with a bold project label naming a real project, the tagline mirrors the job
title, no em dashes or en dashes or double hyphens, availability is correct,
nothing claimed that is on the gaps list, and the document language matches the
posting language.

Run the spread checker over the CV payloads before generating any PDF, and fix
the payloads until it passes. Do not edit the checker to make a payload pass.

Then verify the PDFs actually parse, because a file that opens in a viewer can
still be rejected by an ATS. Every file must open, report the expected page
count, and sit above the size floor.

## 7. Record the ranking

Your reasoning is the most valuable thing this command produces and it is the
one thing that disappears when the chat closes. Write
`applications/<date>/batch.json` with every role you considered in either
`built` or `dropped`, each with the rationale and the soft flags. See
`examples/batch.json` for the shape.

Then render it with the batch report script, which writes `batch.html`: the
ranking, the reasoning, links to each posting, and the Applied and Skipped marks
that feed back into the applied list.

## 8. Finish

Confirm the file count and give the path to `batch.html`. Remind me to mark
roles applied in the extension after applying, so the next batch excludes them.
