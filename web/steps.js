/*
 * Which of the dashboard's four steps (Scrape, Rank, Build, Done) is done,
 * running, or still to come, from one /progress reading.
 *
 * Its own file, with no page code in it, so the selftest can walk it through a
 * whole run in Node (scripts/selftest.py, "the step row follows the run"). It
 * lived inside dashboard.js's render loop until 3 November 2026, where nothing
 * could test it, and it went wrong more than once:
 *
 *   - Build runs its own ranking pass first. The bridge calls that phase
 *     "ranking", so pressing Build moved the running line from step 3 back to
 *     step 2, and it stayed there while the build checked which postings were
 *     still open. A running build is step 3 for its whole run now; the rank
 *     inside it shows as progress on the Rank step ("12/80"), not as the step.
 *   - Done stayed ticked from the previous build while a new one was running.
 *
 * Exactly one step is "active" while anything runs, and the active step only
 * ever moves forward during a run. Steps after it are pending.
 */
function stepStates(d) {
  // Helpers live in here: the dashboard's scripts share one global scope, and
  // arbeitnow.js already has a top-level `state`.
  const state = (running, finished) => {
    if (running) return "active";
    return finished ? "done" : "pending";
  };
  const scrapeDone = (d.searches_total > 0 && d.searches_done >= d.searches_total)
    || d.steps?.arbeitnow?.status === "ok";
  const rankDone = !!d.rank?.ran_at || !!d.rank_run?.result || (d.ranked_ready || 0) > 0;
  const buildDone = !!d.build?.finished && (d.build_progress?.built || 0) > 0;
  const ph = d.phase;
  const after = ph === "done" || ph === "built-partial";
  // build_stage is only sent while a build is running. A build waiting for a
  // scrape to finish is still on step 1: the scrape is what is happening.
  const building = d.build_stage != null && ph !== "scraping";
  const st = {
    scrape: state(ph === "scraping", scrapeDone || building || ph === "ranking" || after),
    rank: state(ph === "ranking" && !building,
                rankDone || (building && d.build_stage === "writing") || after),
    build: state(building, buildDone || after),
    done: state(false, after || buildDone),
  };
  // A new run starts over: whatever comes after the running step is still to
  // do, not ticked off from the last run.
  const order = ["scrape", "rank", "build", "done"];
  const at = order.findIndex((k) => st[k] === "active");
  if (at >= 0) order.slice(at + 1).forEach((k) => { st[k] = "pending"; });
  return st;
}
if (typeof module !== "undefined" && module.exports) module.exports = { stepStates };   // the selftest, in Node
