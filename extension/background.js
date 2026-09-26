/*
 * Service worker — the only part of the extension that talks to the pipeline.
 *
 * Content scripts run inside the page and their fetches can be blocked by the
 * page's own CSP; LinkedIn's is strict enough to matter. Requests from here are
 * covered by host_permissions instead, so localhost stays reachable no matter
 * what the page allows.
 *
 * Everything is best-effort. If the bridge is not running, collecting still
 * works and still stores to chrome.storage, and the popup's manual JSON export
 * remains the fallback path.
 */

const BRIDGE = "http://127.0.0.1:8765";

async function call(path, body, timeoutMs = 20000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(BRIDGE + path, {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    if (!res.ok) return { ok: false, error: `bridge returned ${res.status}` };
    return { ok: true, ...(await res.json()) };
  } catch (e) {
    // Bridge down, or it took longer than the timeout. Not fatal.
    return { ok: false, error: e.name === "AbortError" ? "bridge timed out" : "bridge offline" };
  } finally {
    clearTimeout(timer);
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "bridge:status") {
    call("/status", null, 4000).then(sendResponse);
    return true;
  }
  if (msg?.type === "bridge:push") {
    call("/ingest", { jobs: msg.jobs || [] }).then((r) => {
      if (r.ok) chrome.storage.local.set({ lastPush: { at: Date.now(), ...r } });
      sendResponse(r);
    });
    return true;
  }
  if (msg?.type === "bridge:applied") {
    // Never lose an "applied" mark to a sleeping bridge: keep it and send it
    // with the next flush. Losing one meant that job came back in a batch.
    call("/applied", { urls: msg.urls || [], jobs: msg.jobs || [] }).then(async (r) => {
      if (!r.ok) {
        const { pendingApplied = [] } = await chrome.storage.local.get("pendingApplied");
        await chrome.storage.local.set({
          pendingApplied: [...new Set([...pendingApplied, ...(msg.urls || [])])] });
        r = { ...r, queued: true };
      }
      sendResponse(r);
    });
    return true;
  }
  if (msg?.type === "bridge:built") {
    call("/built", null, 4000).then(sendResponse);
    return true;
  }
  if (msg?.type === "bridge:reset") {
    // hard also clears the seen-before history, so a fresh start really is one.
    call("/reset", { hard: !!msg.hard }).then(sendResponse);
    return true;
  }
  return false;
});

/* ------------------------------------------------------------------ searches
 *
 * A saved list of searches, run on demand, in this browser, in the user's own
 * session. That last part is the whole reason this lives in an extension
 * rather than in an agent driving a browser: there is no automation driver, no
 * headless fingerprint and no second login, which is precisely why the
 * collector works on sites that refuse scripted HTTP.
 *
 * There is no longer a daily schedule. Decision, 18 September 2026.
 * It was removed rather than disabled: the daily alarm never fired reliably on
 * this machine, the launcher already had to wake the worker by opening a tab,
 * and every run in practice was started by hand anyway. What is left is the
 * part that worked — a list of searches and a trigger that runs them now.
 *
 * Pacing is still deliberate, because rhythm gets noticed far more than
 * volume: one page per search, and searches dealt round robin by host so the
 * same site is never hit twice in a row. A gap is only spent when the next
 * search would land on the site just used.
 *
 * Descriptions stay on. The ranker reads them, so a run without them produces
 * a pile of jobs nothing downstream can judge.
 */

const SCHED = "schedule";
const DEFAULT_SCHED = {
  pages: 1,
  searches: [],         // [{ label, url }]
  lastResult: null,
  // Progress within the current run, so a dropped connection or a killed
  // service worker resumes instead of starting over. { index, done[], tries{} }
  runState: null,
  running: false,       // guards against two runs overlapping
};

const MAX_TRIES = 2;

/*
 * Starter searches, seeded once on first install so a new user has something to
 * run straight away. They are EXAMPLES that match the example profile. Edit,
 * switch off or delete them in the popup's "Saved searches" tab; nothing here
 * needs to change for that, and a search you delete does not come back.
 *
 * All searches: last 7 days (f_TPR=r604800). The location is pinned by geoId,
 * not by the location text, because LinkedIn falls back to the country on your
 * profile when the text is ambiguous.
 *
 * Two things learned the hard way:
 *   - A COUNTRY geoId ignores a radius. 101282230 is Germany, the whole country,
 *     not Berlin; adding distance=19 to it does nothing. For a city, open the
 *     city once on LinkedIn and the popup learns its geoId.
 *   - f_WT=2 (remote) plus a country geoId means "remote roles open to that
 *     country", which is mostly pan-regional listings, not jobs located there.
 */
const GEO = { germany: "101282230" };
// Germany, whole country, all work types, last 7 days.
const li = (kw) =>
  `https://www.linkedin.com/jobs/search/?keywords=${encodeURIComponent(kw)}` +
  `&location=Germany&geoId=${GEO.germany}&f_TPR=r604800`;

// One per site, so a new user sees all three working and can copy the pattern.
const RECOMMENDED = [
  { label: "Example: Machine Learning Engineer", url: li("Machine Learning Engineer") },
  { label: "Example: Applied AI Engineer",
    url: "https://de.indeed.com/jobs?q=Applied+AI+Engineer&l=Deutschland&fromage=7" },
  { label: "Example: MLOps Engineer",
    url: "https://www.stepstone.de/jobs?what=MLOps+Engineer&ag=7" },
];

// Bump to re-seed. The seed is one-time (guarded by this), so a search the user
// deletes by hand does not reappear on the next reload.
const SEED_VERSION = 1;

// The keyword carried by a saved-search URL, however it was built. Used to
// dedupe the seed against searches already saved, so reloading twice or seeding
// on top of the user's own search does not create a near-duplicate.
//
// The geoId is part of the key, not just the keyword: the same title searched
// in two countries is two searches, and without the geoId they would collapse
// to one and the seed would silently keep only the first.
function searchKey(url) {
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\./, "").split(".")[0];
    const kw = u.searchParams.get("keywords") || u.searchParams.get("q") || u.searchParams.get("what") ||
               decodeURIComponent(u.pathname.split("/")[2] || "").replace(/-/g, " ");
    const geo = u.searchParams.get("geoId") || u.searchParams.get("location") || "";
    return `${host}:${geo}:${kw.trim().toLowerCase()}`;
  } catch { return url; }
}

/*
 * Reconcile the saved list with the two decisions above, on reload.
 *
 * This is what makes a change reach the list already sitting in the browser
 * rather than only a fresh install: on reload, the recommended searches are
 * merged in once and any corrupt URL is repaired. It never removes a search
 * because of which site it points at — see the note below. Runs before any
 * scheduled run reads the list.
 */
/*
 * A search switched off in the popup is skipped, not deleted.
 *
 * `enabled` is absent on every search saved before 24 September 2026, so the
 * test is `!== false` rather than `=== true`: an old search with no flag stays
 * on. Filtering happens where a RUN ORDER is built, never in migrateSched, so
 * turning one off can never lose it.
 */
const isEnabled = (q) => q && q.enabled !== false;
const activeSearches = (list) => (list || []).filter(isEnabled);

async function migrateSched() {
  const s = { ...DEFAULT_SCHED, ...(await chrome.storage.local.get(SCHED))[SCHED] };
  const before = JSON.stringify(s);
  /*
   * Nothing is blocked by site.
   *
   * Every site the extension can parse stays available, and the user decides
   * by adding or deleting searches in the popup. StepStone is German-market
   * only and Indeed sometimes challenges scripted requests, but a human-paced
   * scrape from a logged-in browser works on both, and someone hiring in the
   * DACH region wants StepStone. Which sites to use is a preference, not
   * something the code should decide.
   *
   * One narrow exception survives, and only because it repairs corrupt data
   * rather than expressing a preference: German searches seeded before
   * 22 September 2026 pinned a COUNTRY geoId together with a radius, which
   * LinkedIn ignores, under a label that wrongly said "Berlin". Same geoId and
   * keyword means the same dedupe key, so without this the broken URL wins and
   * keeps its meaningless distance parameter forever.
   */
  const isStaleDE = (url) =>
    /linkedin\.com/i.test(url || "") && /geoId=101282230/.test(url || "")
    && /[?&]distance=/.test(url || "");
  const dropped = (url) => isStaleDE(url);

  s.searches = (s.searches || []).filter((q) => !dropped(q.url));

  if (s.runState && Array.isArray(s.runState.order)
      && s.runState.order.some((q) => dropped(q.url))) {
    delete s.runState;
  }

  if ((s.seedVersion || 0) < SEED_VERSION) {
    const have = new Set(s.searches.map((q) => searchKey(q.url)));
    // A reinstall (or a second copy of the extension) starts with an empty
    // list. The pipeline keeps a copy of the last list it was sent, so restore
    // that before falling back to the examples: removing and re-adding the
    // extension should never cost someone their searches.
    let seed = RECOMMENDED;
    if (!s.searches.length) {
      const saved = await call("/searches/saved", null, 4000);
      if (saved.ok && Array.isArray(saved.searches) && saved.searches.length) {
        seed = saved.searches.filter((q) => q && q.url);
      }
    }
    for (const rec of seed) {
      if (!have.has(searchKey(rec.url))) { s.searches.push(rec); have.add(searchKey(rec.url)); }
    }
    s.seedVersion = SEED_VERSION;
  }

  if (JSON.stringify(s) !== before) {
    await chrome.storage.local.set({ [SCHED]: s });
  }
}
migrateSched();

const getSched = async () => {
  await migrateSched();
  return { ...DEFAULT_SCHED, ...(await chrome.storage.local.get(SCHED))[SCHED] };
};
const setSched = async (patch) =>
  chrome.storage.local.set({ [SCHED]: { ...(await getSched()), ...patch } });

const today = () => new Date().toLocaleDateString("en-CA");   // YYYY-MM-DD, local
const jitter = (a, b) => a + Math.random() * (b - a);

/*
 * Sleep that survives the service worker being shut down.
 *
 * A manifest v3 service worker is terminated after about thirty seconds with
 * nothing happening, and a pending setTimeout does not count as something
 * happening. A plain five minute await between two searches would therefore be
 * killed partway through and the run would simply stop after the first site,
 * with no error and no way to tell from the outside that anything was wrong.
 *
 * Calling an extension API resets that timer, so wait in twenty second slices
 * and touch one on every slice. Waking briefly and often costs nothing and
 * keeps the worker alive through the minutes-long gaps this schedule depends
 * on for not looking like a machine.
 */
async function wait(ms) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, Math.min(20000, deadline - Date.now())));
    try { await chrome.runtime.getPlatformInfo(); } catch { /* worker going away */ }
  }
}

/*
 * Create an alarm only if it does not already exist.
 *
 * This is the whole reason the one minute poll silently died. A service worker
 * is torn down after about thirty seconds idle and restarted on the next event,
 * and every restart re-runs this file top to bottom. chrome.alarms.create with
 * an existing name does not leave that alarm alone: it REPLACES it, restarting
 * the countdown from zero.
 *
 * So while the popup is open it messages the worker every 1.5 seconds, the
 * worker wakes, this file re-runs, and a one minute alarm is pushed one minute
 * into the future again. It can never reach its own deadline. The trigger poll
 * ran fine at 16:18 with the popup shut and stopped dead at 16:26 when it was
 * opened, which is exactly this.
 */
function ensureAlarm(name, opts) {
  chrome.alarms.get(name, (existing) => {
    if (!existing) chrome.alarms.create(name, opts);
  });
}

/*
 * No daily alarm. Removed 18 September 2026 along with the schedule.
 *
 * A one minute alarm still runs below for the manual trigger, which is a
 * different thing: it asks the bridge whether a run was requested, rather than
 * deciding on its own that it is time to scrape.
 */

// Wait for the content script in a freshly opened tab to answer.
async function waitForScript(tabId, ms = 45000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    try {
      const r = await chrome.tabs.sendMessage(tabId, { type: "status" });
      if (r?.ok) return r;
    } catch { /* not injected yet */ }
    await wait(1000);
  }
  return null;
}

// The auto-run clears its own storage key when it finishes or gives up, so
// that key disappearing is the completion signal.
async function waitForRun(ms = 15 * 60 * 1000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    await wait(3000);
    const { autoRun } = await chrome.storage.local.get("autoRun");
    if (!autoRun) return true;
  }
  await chrome.storage.local.remove("autoRun");   // do not leave it armed
  return false;
}

/*
 * Anything collected while the bridge was down is still sitting in
 * chrome.storage, because the content script stores first and pushes second and
 * a failed push is deliberately silent. Flushing at the start and end of a run
 * means a bridge that was asleep, restarting, or unreachable costs a delay
 * rather than a day's jobs. The bridge dedupes by URL, so resending everything
 * is cheap and idempotent.
 */
async function flushToBridge(where) {
  try {
    const { pendingApplied = [] } = await chrome.storage.local.get("pendingApplied");
    if (pendingApplied.length) {
      const a = await call("/applied", { urls: pendingApplied });
      if (a.ok) await chrome.storage.local.remove("pendingApplied");
    }
  } catch { /* retried on the next flush */ }
  try {
    const { jobs = {} } = await chrome.storage.local.get("jobs");
    const all = Object.values(jobs);
    if (!all.length) return;
    const r = await call("/ingest", { jobs: all });
    if (!r.ok) console.warn(`[job collector] flush (${where}) failed: ${r.error}`);
  } catch { /* never let a flush stop a run */ }
}

/*
 * Strip the volatile junk LinkedIn puts in a search URL before opening it.
 *
 * A saved search keeps whatever was in the address bar the day it was added,
 * including `currentJobId` for the posting that happened to be selected, and
 * `origin=...AUTOCOMPLETE&refresh=true` from however the search was typed. The
 * job id ages out, and on 18 September "linkedin: Ai Engineer" hung for the
 * full 45 second timeout twice on a URL carrying a job id from a week earlier,
 * while the other two searches, whose only difference was the origin parameter,
 * loaded fine.
 *
 * None of these parameters affect WHICH jobs the search returns, so dropping
 * them costs nothing and removes a whole class of stale-state failure.
 */
const VOLATILE = ["currentJobId", "origin", "refresh", "trk", "trackingId",
                  "refId", "position", "pageNum", "eBP", "lipi"];

// Sites whose list only renders while the tab is actually on screen.
const FOREGROUND_HOSTS = new Set(["linkedin.com"]);

function cleanSearchUrl(raw) {
  try {
    const u = new URL(raw);
    if (!/linkedin\.com$/.test(u.hostname.replace(/^www\./, ""))) return raw;
    for (const k of VOLATILE) u.searchParams.delete(k);
    return u.toString();
  } catch { return raw; }
}

async function runOneSearch(search, pages) {
  let tab = null;
  const t0 = Date.now();
  const url = cleanSearchUrl(search.url);
  try {
    /*
     * Reuse a tab that is already on this search rather than opening a second.
     *
     * The launcher opens the first saved search itself, because loading a job
     * page is what wakes this worker reliably. Creating a tab here regardless
     * meant two tabs on the identical URL, both being driven, which is
     * confusing to watch and doubles the requests that page makes.
     */
    /*
     * LinkedIn is driven in a VISIBLE, focused tab. The rule,
     * 18 September 2026, and the reasoning holds up.
     *
     * Chrome throttles background tabs hard: requestAnimationFrame stops,
     * timers are slowed, and rendering work is deferred. A virtualised list
     * like LinkedIn's decides what to mount from layout and visibility, so in a
     * background tab it can sit at its first batch no matter how correctly we
     * scroll it. That matches what they saw: the runs that filled up were the
     * ones where the tab happened to be on screen.
     *
     * StepStone and Indeed render their whole page server side and do not care,
     * so they stay in the background where they do not interrupt anything.
     */
    const foreground = FOREGROUND_HOSTS.has(hostOf(url));
    const open = await chrome.tabs.query({ url: url.split("#")[0] });
    tab = open && open[0]
      ? open[0]
      : await chrome.tabs.create({ url, active: foreground });
    if (foreground) {
      // Reused tabs start inactive, and a focused tab in an unfocused window is
      // still not being rendered, so raise both.
      try { await chrome.tabs.update(tab.id, { active: true }); } catch {}
      try { await chrome.windows.update(tab.windowId, { focused: true }); } catch {}
    }
    let status = await waitForScript(tab.id);
    if (!status) {
      // One reload before giving up. A LinkedIn tab that never hands over to
      // its own script is usually a page that half-rendered, and reloading it
      // costs 45 seconds against losing the search entirely.
      try {
        await chrome.tabs.reload(tab.id);
        status = await waitForScript(tab.id);
      } catch { /* tab gone */ }
    }
    if (!status) {
      // This path used to return without reporting, which is how "linkedin: Ai
      // Engineer" ran, failed and left no trace at all on 18 September: the run
      // counted it as done, the audit never saw it. Every exit reports now.
      const audit = { label: search.label, url: search.url, host: hostOf(search.url),
                      site: "", pages: 0, found: 0, added: 0, loaded: 0, ok: false,
                      why: "the page never finished loading (no content script after 45s)",
                      ms: Date.now() - t0 };
      reportSearch(audit);
      return { ok: false, line: "page never loaded", audit };
    }

    await clearReports();
    await chrome.tabs.sendMessage(tab.id, {
      type: "auto", pages, withDescriptions: true,
    });
    const finished = await waitForRun();
    const { autoStatus } = await chrome.storage.local.get("autoStatus");
    const pagesSeen = await drainReports();
    const sum = (k) => pagesSeen.reduce((a, r) => a + (r[k] || 0), 0);
    const audit = {
      label: search.label, url: search.url, host: hostOf(search.url),
      site: pagesSeen[0]?.site || "",
      pages: pagesSeen.length,
      found: sum("found"), added: sum("added"),
      loaded: sum("loaded"), withDesc: sum("withDesc"),
      scrollDead: pagesSeen.some((r) => r.scrollDead),
      userScrolled: pagesSeen.some((r) => r.userScrolled),
      container: pagesSeen[0]?.container || "",
      everMoved: pagesSeen.some((r) => r.everMoved),
      scrollables: pagesSeen[0]?.scrollables ?? 0,
      trace: pagesSeen[0]?.trace || [],
      refused: pagesSeen.filter((r) => !r.ok).length,
      why: pagesSeen.find((r) => !r.ok)?.why || "",
      ms: Date.now() - t0,
      ok: finished && pagesSeen.some((r) => r.ok),
    };
    if (!finished) { audit.ok = false; audit.why = audit.why || "timed out"; }
    if (!pagesSeen.length) { audit.ok = false; audit.why = audit.why || "the page reported nothing"; }
    reportSearch(audit);
    return finished
      ? { ok: true, line: autoStatus?.text || "done", audit }
      : { ok: false, line: "timed out", audit };
  } catch (e) {
    const audit = { label: search.label, url: search.url, host: hostOf(search.url),
                    pages: 0, found: 0, added: 0, loaded: 0, ok: false,
                    why: e.message || "failed", ms: Date.now() - t0 };
    reportSearch(audit);
    return { ok: false, line: e.message || "failed", audit };
  } finally {
    if (tab) { try { await chrome.tabs.remove(tab.id); } catch {} }
  }
}

/*
 * Walk the saved searches, writing progress after every one.
 *
 * Every exit path persists where it got to, so the next alarm tick continues
 * from the search that did not finish rather than from the top. A search that
 * fails is retried once on a later tick, since the common causes are transient:
 * no network yet after a wake, a site briefly refusing, a tab that never
 * finished loading. After MAX_TRIES it is recorded as failed and the run moves
 * on rather than blocking the searches behind it.
 */
/*
 * Interleave the searches so no site is hit twice in a row.
 *
 * The gap between searches exists for one reason: a site should not see a burst
 * from one session. That is a per-site concern, so two searches on two
 * different sites need no gap at all — LinkedIn does not care that StepStone
 * was just queried. The old code paused three to seven minutes between every
 * pair regardless, which spent twenty minutes idling for a protection that only
 * ever applied to consecutive hits on the same host.
 *
 * So deal them round robin by host. With one search per site the order is
 * simply all of them back to back with no waiting. With several on one site,
 * the others are dealt in between, and each site ends up naturally spaced by
 * however long the intervening searches take, which is real work rather than a
 * sleep.
 */
function siteLabel(url) {
  const h = hostOf(url).toLowerCase();
  if (h.includes("linkedin")) return "LinkedIn";
  if (h.includes("stepstone")) return "StepStone";
  if (h.includes("indeed")) return "Indeed";
  return h;
}

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}

function interleave(searches) {
  const byHost = new Map();
  for (const q of searches) {
    const h = hostOf(q.url);
    if (!byHost.has(h)) byHost.set(h, []);
    byHost.get(h).push(q);
  }
  // Deal one from each host in turn, largest queue first each round so a host
  // with several searches does not end up bunched at the tail.
  const out = [];
  while (out.length < searches.length) {
    const queues = [...byHost.values()].filter((q) => q.length)
      .sort((a, b) => b.length - a.length);
    for (const q of queues) {
      const next = q.shift();
      // Never place two of the same host back to back if anything else is left.
      if (out.length && hostOf(out[out.length - 1].url) === hostOf(next.url)
          && queues.some((o) => o !== q && o.length)) {
        q.unshift(next);
        continue;
      }
      out.push(next);
    }
  }
  return out;
}

// Tell the bridge whether a scrape is in flight. The batch script waits on
// this instead of watching the collected count, which cannot distinguish
// "finished" from "paused between searches".
// One line per search, straight to the bridge, which keeps the run log.
async function reportSearch(audit) {
  try { await call("/search-result", audit); } catch { /* best effort */ }
}

async function reportScrape(running, done, total) {
  try { await call("/scrape", { running, done, total }); } catch { /* best effort */ }
}

async function runSchedule() {
  const s0 = await getSched();
  // The order is fixed once per run and carried in runState, so a resume
  // continues the same sequence rather than reshuffling and repeating a site.
  // Resuming is still worth keeping without a schedule: a run interrupted by a
  // dropped connection or a reclaimed worker picks up at the search that did
  // not finish, rather than starting the whole list again.
  const searches = (s0.runState && s0.runState.order)
    ? s0.runState.order
    : interleave(activeSearches(s0.searches));
  const rs = s0.runState || { index: 0, done: [], tries: {} };
  rs.order = searches;

  rs.startedAt = Date.now();
  await setSched({ running: true, runState: rs });
  await reportScrape(true, rs.index, searches.length);
  await flushToBridge("run start");

  try {
    while (rs.index < searches.length) {
      const i = rs.index;
      const search = searches[i];
      const tries = (rs.tries[i] || 0) + 1;
      rs.tries[i] = tries;
      await setSched({ runState: rs });

      await chrome.storage.local.set({ autoStatus: {
        text: `search ${i + 1}/${searches.length}: ${siteLabel(search.url)} · ${search.label}`, running: true } });

      const res = await runOneSearch(search, s0.pages);

      if (!res.ok && tries < MAX_TRIES) {
        /*
         * Retry here and now, in this run.
         *
         * This used to leave the index where it was and return, so the daily
         * alarm's next tick would pick the search up again. That alarm is gone
         * (18 September), and nothing replaced it, so returning meant the run
         * simply stopped at the first failure with no way to continue: on
         * 18 September "linkedin: Ai Engineer" failed and the remaining
         * searches only ran because a second trigger happened to arrive.
         *
         * A short pause first, since the usual causes are transient.
         */
        rs.done.push(`${search.label}: ${res.line} (retrying)`);
        await setSched({ runState: rs, lastResult: { at: Date.now(), lines: rs.done } });
        await chrome.storage.local.set({ autoStatus: {
          text: `${search.label} failed, retrying now`, running: true } });
        await wait(jitter(15000, 30000));
        continue;                       // same index, second attempt
      }

      if (!res.ok) alert_("Search failed", `${search.label}: ${res.line}`);
      rs.done.push(`${search.label}: ${res.line}` + (res.ok ? "" : ` (gave up after ${tries})`));
      rs.index = i + 1;
      await setSched({ runState: rs, lastResult: { at: Date.now(), lines: rs.done } });
      // Still running: the gap that follows is a pause, not the end.
      await reportScrape(rs.index < searches.length, rs.index, searches.length);

      // Minutes between sites. Three searches back to back is the tell.
      //
      // Nothing visible happens during this gap, and the last thing on screen
      // is a tab closing, so it reads exactly like the run having died. Count
      // it down in the popup instead: a silence you can see the end of is a
      // pause, an identical silence you cannot is a failure.
      if (rs.index < searches.length) {
        // Only a repeat of the same host needs real spacing. A different site
        // gets a few seconds, which is enough for the tab to close cleanly.
        /*
         * No waiting between searches. Decision, 18 September 2026:
         * "each one should start as soon as the previous finishes".
         *
         * The three to seven minute pause between searches on the same site was
         * there to keep a run from looking like a burst. It is gone, and it is
         * worth being clear about what that trades: three LinkedIn searches now
         * run back to back instead of spread over a quarter of an hour. What
         * remains in our favour is that each search is genuinely slow anyway,
         * because it scrolls the whole list and reads every description: the
         * two that ran tonight took 216 and 286 seconds. So the pacing is still
         * minutes apart in practice, it is just real work rather than sleeping.
         *
         * The two seconds below are not pacing. They let the finished tab close
         * before the next one opens.
         */
        await chrome.storage.local.set({ autoStatus: {
          text: `${rs.index}/${searches.length} done · starting the next one`,
          running: true } });
        await wait(2000);
      }
    }

    await flushToBridge("run end");
    await chrome.storage.local.set({ autoStatus: {
      text: `all ${searches.length} searches done`, running: false } });
    // The popup is usually shut while this runs, so the only place a result
    // would otherwise appear is a status line nobody is looking at.
    try {
      const st = await call("/status", null, 4000);
      alert_("Job collector finished",
        st.ok ? `${st.collected_today} jobs collected · ${st.last_rank?.matches ?? "?"} matched`
              : `${searches.length} searches done`);
    } catch { alert_("Job collector finished", `${searches.length} searches done`); }
    await setSched({ runState: null,
                     lastResult: { at: Date.now(), lines: rs.done } });
  } finally {
    await setSched({ running: false });
    await reportScrape(false, searches.length, searches.length);
  }
}

chrome.runtime.onMessage.addListener((msg, _s, sendResponse) => {
  if (msg?.type === "sched:get") { getSched().then(sendResponse); return true; }
  if (msg?.type === "sched:set") {
    (async () => {
      const patch = { ...msg.patch };
      // Editing, switching off, removing or importing searches must not be
      // undone by a stale resume. An interrupted run freezes its order in
      // runState and replays it, so after an edit it would re-run the OLD list.
      // Drop the resume point when the list changes between runs; during a
      // run, leave it alone (the run owns runState and resets it when done).
      if (patch.searches) {
        const cur = await getSched();
        if (!cur.running) patch.runState = null;
      }
      await setSched(patch);
    })().then(async () => {
      // The launcher needs a real search URL to open, and only the extension
      // knows them. Mirroring them to the bridge is what lets a shell script
      // open the right page instead of guessing one. Only searches that are
      // switched on, so the dashboard's "of N" counts what will actually run.
      const s = await getSched();
      await call("/searches", { searches: activeSearches(s.searches), all: s.searches });
      sendResponse({ ok: true });
    });
    return true;
  }
  if (msg?.type === "sched:runNow") {
    // A fresh progress record, so an interruption resumes rather than restarts.
    setSched({ runState: { index: 0, done: [], tries: {} }, running: false })
      .then(() => { runSchedule().catch(() => {}); sendResponse({ ok: true }); });
    return true;
  }
  if (msg?.type === "sched:resume") {
    runSchedule().catch(() => {});
    sendResponse({ ok: true });
    return true;
  }
  return false;
});


/* ------------------------------------------------------- manual trigger + alerts
 *
 * A shell script cannot talk to a Chrome extension, and the schedule's own
 * alarm only fires every twenty minutes, which is far too slow to feel like
 * pressing a button. So "run it now" is parked on the bridge and picked up
 * here on a one minute poll. Polling localhost costs nothing, and it means the
 * whole pipeline has a single entry point that works from the Desktop, from a
 * terminal, or from the popup.
 *
 * The window is deliberately ignored for a manual run. The point of it is the
 * mornings when the laptop was shut at 11 and is opened at four.
 */
ensureAlarm("job-collector-trigger", { periodInMinutes: 1 });

/*
 * A fresh worker means no run of ours survived.
 *
 * The keepalive holds this worker up for the whole of a run, so if this file is
 * executing from cold then whatever set running:true is gone. Clearing it here
 * is what stops one killed run from disabling the extension permanently.
 */
(async () => {
  const s = await getSched();
  if (s.running) {
    await setSched({ running: false });
    console.warn("[job collector] cleared a stale running flag from a previous worker");
  }
  checkTrigger();
})();

function alert_(title, message) {
  chrome.notifications.create({
    type: "basic", iconUrl: "icon128.png", title, message, priority: 1,
  }, () => void chrome.runtime.lastError);   // no icon file is not fatal
}

/*
 * Check for a parked "run now" request.
 *
 * This deliberately does not live only in an alarm. The one minute alarm has
 * not fired once since 16:26 across several extension reloads, and a scheduler
 * that silently stops is worse than no scheduler: the launcher parks a request,
 * nothing picks it up, and the batch sits printing "pool is empty" for half an
 * hour. So the same check runs from every event that can wake this worker —
 * the alarm when it works, a message from a content script or the popup, and
 * browser startup. Whichever happens first wins, and the request can only be
 * consumed once because the bridge clears it on read.
 */
/*
 * Is a "running" flag left over from a run that died?
 *
 * A run is bounded: at most one search at a time, each capped by its own
 * timeouts, plus the gaps between them. Anything still claiming to run an hour
 * later is a corpse, and treating it as live means the extension never scrapes
 * again until the profile is wiped.
 */
const RUN_MAX_MS = 60 * 60 * 1000;

async function staleRunning(s) {
  const started = s.runState?.startedAt || 0;
  if (!started) return true;               // no timestamp at all: assume stale
  return Date.now() - started > RUN_MAX_MS;
}

let checking = false;

async function checkTrigger() {
  if (checking) return;
  checking = true;
  try {
    const s = await getSched();
    if (!s.searches.length) return;
    // A stuck running flag blocks every future run, silently and forever.
    // setSched({running:true}) is written at the start of a run and cleared in
    // a finally, so a worker killed mid-run leaves it true with nothing alive
    // to clear it. After that every trigger is swallowed here and the batch
    // just prints "pool is empty". See clearStaleRunning: a flag older than the
    // longest possible run cannot belong to anything still going.
    if (s.running && !(await staleRunning(s))) return;
    // Keep the bridge's copy current on every wake, not only when a setting is
    // changed. Otherwise the launcher has nothing to open on a fresh profile.
    call("/searches", { searches: activeSearches(s.searches), all: s.searches }).catch(() => {});
    const r = await call("/trigger", null, 4000);
    if (r.ok) {
      const { pendingApplied = [] } = await chrome.storage.local.get("pendingApplied");
      if (pendingApplied.length) await flushToBridge("bridge is back");
    }
    if (!r.ok || !r.run) return;

    /*
     * A trigger may name one host, so a manual run can cover a single site
     * without editing the saved schedule. Used to re-run LinkedIn on its own
     * after the virtualised-list fix, rather than re-scraping all three.
     */
    const only = (r.only || "").trim().toLowerCase();
    const picked = activeSearches(
      only ? s.searches.filter((q) => hostOf(q.url).includes(only)) : s.searches);
    if (only && !picked.length) {
      alert_("Job collector", `No saved search matches “${only}”`);
      return;
    }

    alert_("Job collector",
      `Starting ${picked.length} search${picked.length === 1 ? "" : "es"}` +
      (only ? ` on ${only}` : "") + "…");
    await setSched({ runState: {
      index: 0, done: [], tries: {},
      // Pinning the order here means runSchedule uses this subset rather than
      // rebuilding from every saved search.
      order: interleave(picked),
    } });
    runSchedule().catch(() => {});
  } finally {
    checking = false;
  }
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "job-collector-trigger") checkTrigger();
});

// Any message wakes this worker, and a woken worker is a free chance to look.
// The content script writes audit lines into chrome.storage.local, because this
// worker is torn down and restarted during a long search and would lose
// anything held in a variable. These two just read and clear them.
async function drainReports() {
  const { searchReports = [] } = await chrome.storage.local.get("searchReports");
  await chrome.storage.local.remove("searchReports");
  return searchReports;
}
function clearReports() { return chrome.storage.local.remove("searchReports"); }

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "hello" || msg?.type === "bridge:push") checkTrigger();
  return false;
});

chrome.runtime.onStartup?.addListener?.(() => checkTrigger());
chrome.runtime.onInstalled?.addListener?.(() => checkTrigger());
