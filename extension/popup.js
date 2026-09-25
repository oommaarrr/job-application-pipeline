const $ = (id) => document.getElementById(id);

/*
 * Status text and the progress bar are the same signal, so they are written in
 * one place. Progress arrives as free text from the content script, "job 12/25"
 * during a page and "page 2/3 · job 12/25" during an auto run, so the last
 * fraction in the string is the one that describes the work. Anything with no
 * fraction, "loading cards… 40", is real progress with no known total, which is
 * what the indeterminate state is for.
 */
// What to do when the pipeline is offline, in this computer's terms. Telling a
// Windows user to "run serve.py" sent them to the one command that could not
// work on a fresh download.
const START_HINT = /win/i.test(navigator.userAgentData?.platform || navigator.platform || "")
  ? "double-click start.bat in the project folder"
  : "run ./start.sh in the project folder";

const msg = (t) => {
  const text = t || "";
  $("msg").textContent = text;
  const bar = $("bar");
  if (!bar) return;
  const fractions = [...text.matchAll(/(\d+)\s*\/\s*(\d+)/g)];
  const last = fractions[fractions.length - 1];
  if (last && Number(last[2]) > 0) {
    document.body.classList.remove("indeterminate");
    bar.style.width = Math.min(100, (Number(last[1]) / Number(last[2])) * 100) + "%";
  } else if (text) {
    document.body.classList.add("indeterminate");
  }
};

// When the content script last sent a live progress ping. Declared up here
// rather than beside its listener so refresh() can never hit it in the
// temporal dead zone.
let lastLiveMsgAt = 0;

async function tab() {
  const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
  return t;
}

async function send(payload) {
  const t = await tab();
  try {
    return await chrome.tabs.sendMessage(t.id, payload);
  } catch {
    return null; // content script not present on this page
  }
}

const bridge = (payload) => chrome.runtime.sendMessage(payload).catch(() => ({ ok: false }));

// The pipeline is the point of the extension now, so its state is shown before
// anything else. A red dot means collecting still works but nothing is being
// ranked, and the manual export is the way out.
let lastReport = null;

async function refreshBridge() {
  const st = await bridge({ type: "bridge:status" });
  $("dot").classList.toggle("up", !!st?.ok);
  if (!st?.ok) {
    $("bridge").textContent = `pipeline offline — start it: ${START_HINT}`;
    $("batchLine").textContent = "pipeline offline";
    $("openReport").disabled = true;
    return;
  }
  const r = st.last_rank || {};
  $("bridge").textContent =
    `pipeline up · ${st.collected_today} today · ${st.applied} applied` +
    (r.matches != null ? ` · ${r.matches} ranked` : "");

  lastReport = st.latest_report || null;
  $("openReport").disabled = !lastReport;
  // No batch yet means no list to expand, so don't show a dead triangle.
  $("batchBox").style.display = st.latest_batch ? "block" : "none";
  $("batchLine").textContent =
    `${st.built} with documents · latest ${st.latest_batch}`;
  renderBatch(st.latest_batch);
}

// The popup is where you are standing when you decide whether to apply, so the
// batch belongs here, not only in the report.
async function renderBatch(latest) {
  const list = $("batchList");
  list.innerHTML = "";
  if (!latest) return;
  const res = await bridge({ type: "bridge:built" });
  if (!res?.ok) return;
  Object.values(res.built)
    .filter((b) => b.date === latest)
    .sort((a, b) => (a.rank || 99) - (b.rank || 99))
    .forEach((b) => {
      const li = document.createElement("li");
      li.textContent = b.company || "(unnamed)";
      list.appendChild(li);
    });
}

// Which job is on screen, and does it already have documents.
async function refreshBuiltFlag(current) {
  const el = $("builtFlag");
  el.textContent = "";
  if (!current?.url) return;
  const res = await bridge({ type: "bridge:built" });
  if (!res?.ok) return;
  const key = current.url.split("?")[0].replace(/\/$/, "").toLowerCase();
  const hit = res.built[key];
  if (hit) el.textContent = `✓ documents built ${hit.date} (#${hit.rank})`;
}

async function refresh() {
  const { jobs = {}, autoRun = null, autoStatus = null } =
    await chrome.storage.local.get(["jobs", "autoRun", "autoStatus"]);
  const all = Object.values(jobs);
  const withDesc = all.filter((j) => j.description).length;
  $("stored").textContent = all.length;
  $("export").disabled = all.length === 0;
  $("clear").disabled = all.length === 0;
  $("resend").disabled = all.length === 0;
  refreshBridge();

  // An auto-collect keeps running after the popup closes (state is in
  // storage, not in this page), so reflect whatever it last reported.
  //
  // But the stored status is a leftover, not a live signal: it survives the run
  // that wrote it. refresh() fires on every storage change, including each time
  // the collector saves jobs, so this line used to stamp a stale
  // "stopped by user" over the live progress of a run that was still going. A
  // live progress message always wins for the next few seconds.
  const autoRunning = !!(autoRun && autoRun.remaining > 0);
  if (autoStatus?.text && Date.now() - lastLiveMsgAt > 3000) msg(autoStatus.text);

  const st = await send({ type: "status" });
  if (!st || !st.ok) {
    // Two very different problems used to print the same thing. If the tab is
    // on a supported site and the content script still did not answer, the
    // script is simply not in this tab: that happens after reloading the
    // extension, because existing tabs keep running the old code until they
    // are reloaded themselves. Telling them to reload the tab is the fix, and
    // it is not guessable from "Not a supported search page".
    const t = await tab();
    const url = t?.url || "";
    const supported = /^https:\/\/(www\.linkedin\.com\/jobs\/|[^/]*indeed\.com\/|www\.stepstone\.de\/)/.test(url);
    $("site").textContent = supported
      ? "extension not active in this tab — reload the page (⌘R)"
      : "Not a supported search page";
    $("visible").textContent = "0";
    $("collect").disabled = $("auto").disabled = true;
    $("stop").style.display = autoRunning ? "block" : "none";
    $("currentJob").textContent = "no job open";
    $("applied").disabled = true;
    return;
  }
  // Zero cards on a page that is plainly full of jobs means the selectors no
  // longer match the markup, not that the page is empty. Say so, because the
  // silent version of this reads as the extension being broken outright.
  $("site").textContent = st.visible === 0
    ? `${st.site} · no job cards found — the site's layout may have changed`
    : `${st.site} · ${withDesc}/${all.length} stored have descriptions`;
  $("visible").textContent = st.visible;

  // Show Stop whenever anything is in flight, not only during an auto-collect.
  // A single "Collect this page" over 25 cards runs for a couple of minutes and
  // used to offer no way to interrupt it at all.
  const running = autoRunning || !!st.collecting;
  // Drives the progress bar's visibility. When nothing is in flight the bar is
  // hidden and reset, so a finished run does not leave a full bar sitting there
  // looking like a run still going.
  document.body.classList.toggle("running", running);
  if (!running) {
    document.body.classList.remove("indeterminate");
    if ($("bar")) $("bar").style.width = "0";
  }
  $("stop").style.display = running ? "block" : "none";
  $("auto").style.display = running ? "none" : "block";
  $("collect").disabled = $("auto").disabled = st.visible === 0 || running;

  $("currentJob").textContent = st.current
    ? (st.current.title || st.current.url)
    : "open a job to mark it applied";
  $("applied").disabled = !st.current;
  refreshBuiltFlag(st.current);
}

// While a run is in flight, keep the popup in sync with storage.
chrome.storage.onChanged.addListener((changes) => {
  if (changes.autoStatus || changes.autoRun || changes.jobs) refresh();
});

// Progress pings from the content script. The timestamp is what stops a stale
// stored status from overwriting a live line (see refresh()).
chrome.runtime.onMessage.addListener((m) => {
  if (m?.type === "progress") {
    lastLiveMsgAt = Date.now();
    msg(m.text);
  }
});

// Stop and Erase are deliberately absent: those are the two things you reach
// for when a run is misbehaving, and disabling them while it runs is what left
// you with no way out mid-collect.
function busy(on) {
  for (const id of ["collect", "auto", "export", "resend", "applied"])
    $(id).disabled = on;
}

/*
 * Open the pipeline's own pages. Always through the bridge, never file://: an
 * extension cannot open a file:// URL unless the user has ticked "Allow access
 * to file URLs", and the saved batch.html on disk is a frozen copy in whatever
 * design existed the day it was built. The bridge renders it fresh.
 */
const PIPELINE = "http://127.0.0.1:8765";
const openPage = (path) => chrome.tabs.create({ url: PIPELINE + path });
$("navDash").onclick = () => openPage("/dashboard");
$("navApps").onclick = () => openPage("/report/");
$("navRank").onclick = () => openPage("/ranking");
$("openReport").onclick = () => openPage("/ranking");

/*
 * Search builder.
 *
 * Widening a search used to mean hand-editing a URL, which is why the same
 * twelve terms kept getting scraped. Each site spells the same four ideas
 * differently, so the mapping lives here rather than in your head:
 * radius, recency and remote are named differently on all three.
 */
const SEARCH = {
  /*
   * LinkedIn resolves a free-text location itself, and not the way you expect:
   * "berlin" comes back as "Berlin, Berlin, Germany", and an empty location
   * falls back to the country on your profile, which is why a blank search
   * reads "Germany" and quietly returns the whole country.
   *
   * The parameter that actually pins a search to a place is geoId, not
   * location. Rather than hardcode ids that would rot, the popup learns them:
   * every time you are on a LinkedIn search page it records the geoId that page
   * is using against the location text, and reuses it next time you type that
   * location. See learnGeo().
   */
  linkedin(kw, loc, radius, posted, remote, geoId) {
    const p = new URLSearchParams({ keywords: kw.trim() });
    if (loc.trim()) p.set("location", loc.trim());
    if (geoId) p.set("geoId", geoId);
    if (radius) p.set("distance", String(Math.round(radius * 0.621371))); // miles
    if (posted) p.set("f_TPR", `r${posted * 86400}`);
    if (remote) p.set("f_WT", "2");
    return "https://www.linkedin.com/jobs/search/?" + p.toString();
  },
  /*
   * StepStone. German-market, so the parameters are German too: `what`/`where`,
   * `radius` in km (not miles, unlike LinkedIn), `ag` is an age-in-days filter.
   * Restored on 24 September 2026 — the author stopped using it, but an
   * open-source user hiring in the DACH region should not have it hidden.
   */
  stepstone(kw, loc, radius, posted, remote) {
    const p = new URLSearchParams({ what: kw.trim() });
    if (loc.trim()) p.set("where", loc.trim());
    if (radius) p.set("radius", String(radius));
    if (posted) p.set("ag", String(posted));
    if (remote) p.set("wt", "home-office");
    return "https://www.stepstone.de/jobs?" + p.toString();
  },
  indeed(kw, loc, radius, posted, remote) {
    const p = new URLSearchParams({ q: kw.trim() });
    if (loc.trim()) p.set("l", loc.trim());
    if (radius) p.set("radius", String(radius));
    if (posted) p.set("fromage", String(posted));
    if (remote) p.set("sc", "0kf:attr(DSQF7);");
    return "https://de.indeed.com/jobs?" + p.toString();
  },
};

async function saveSearch() {
  await chrome.storage.local.set({ search: {
    kw: $("kw").value, loc: $("loc").value, radius: $("radius").value,
    site: $("siteSel").value, posted: $("posted").value, remote: $("remote").checked,
  }});
}

/*
 * Learn geoId from whatever LinkedIn search page is open.
 *
 * LinkedIn's own URLs carry the resolved geoId, so browsing to the right place
 * once teaches the popup that location for good. Nothing is guessed and nothing
 * hardcoded goes stale.
 */
async function learnGeo() {
  const t = await tab();
  const url = t?.url || "";
  if (!/linkedin\.com\/jobs\/search/.test(url)) return;
  const q = new URL(url).searchParams;
  const geo = q.get("geoId");
  const loc = (q.get("location") || $("loc").value || "").trim().toLowerCase();
  if (!geo || !loc) return;
  const { geoByLoc = {} } = await chrome.storage.local.get("geoByLoc");
  if (geoByLoc[loc] === geo) return;
  geoByLoc[loc] = geo;
  await chrome.storage.local.set({ geoByLoc });
}

$("go").onclick = async () => {
  const kw = $("kw").value.trim();
  if (!kw) { msg("type something to search for"); return; }
  const loc = $("loc").value;
  const { geoByLoc = {} } = await chrome.storage.local.get("geoByLoc");
  const geoId = geoByLoc[loc.trim().toLowerCase()];
  if ($("siteSel").value === "linkedin" && loc.trim() && !geoId)
    msg("no geoId learned for that location yet — open it once on LinkedIn and it is remembered");
  const url = SEARCH[$("siteSel").value](
    kw, loc, +$("radius").value, +$("posted").value, $("remote").checked, geoId);
  await saveSearch();
  chrome.tabs.create({ url });
};

for (const id of ["kw", "loc", "radius", "siteSel", "posted", "remote"])
  $(id).addEventListener("change", saveSearch);

$("collect").onclick = async () => {
  busy(true); msg("collecting…");
  refresh();                       // surface Stop straight away
  const r = await send({ type: "collect", withDescriptions: $("desc").checked });
  busy(false);
  // A misalignment warning has to be visible here, while the tab is still open
  // and a re-collect is one click away, not buried in the console.
  const m = r?.mismatch;
  const warn = !m ? ""
    : m.empty != null
      ? ` · ⚠ ${m.empty}/${m.of} descriptions came back empty, the page may have changed`
      : ` · ⚠ ${m.orphans}/${m.of} descriptions never name their own company, check alignment`;
  msg(r?.error ? `error: ${r.error}`
      : r?.short ? `⚠ only ${r.loaded}/${r.expected} cards loaded — nothing read. `
                 + `Scroll the list yourself, then collect again.`
      : r?.cancelled ? `stopped at ${r.collected}/${r.of} · +${r.added} new kept${warn}`
      : `+${r?.added ?? 0} new (${r?.total ?? 0} total)${warn}`);
  refresh();
};

$("applied").onclick = async () => {
  const cur = await send({ type: "current" });
  if (!cur?.url) { msg("no job open on this page"); return; }
  busy(true);
  const r = await bridge({ type: "bridge:applied", urls: [cur.url], jobs: [cur] });
  busy(false);
  msg(!r?.ok ? (r?.queued ? `pipeline offline — saved, sent when it is back`
                          : `pipeline offline — not recorded`)
            : r.added ? `marked applied · ${r.total} total · ${r.matches} still ranked`
                      : `already on the applied list`);
  refresh();
};

// Everything collected while the bridge was down still lives in chrome.storage,
// so one button is enough to catch the pipeline up rather than re-scraping.
$("resend").onclick = async () => {
  const { jobs = {} } = await chrome.storage.local.get("jobs");
  const all = Object.values(jobs);
  busy(true); msg(`sending ${all.length}…`);
  const r = await bridge({ type: "bridge:push", jobs: all });
  busy(false);
  msg(!r?.ok ? `pipeline offline — ${START_HINT}, then retry`
             : `sent ${all.length} · +${r.added} new · ${r.matches} ranked`);
  refresh();
};

$("auto").onclick = async () => {
  const pages = Math.max(1, Math.min(15, +$("pages").value || 3));
  const r = await send({ type: "auto", pages, withDescriptions: $("desc").checked });
  if (r?.error) { msg(`error: ${r.error}`); return; }
  // The run continues in the tab even if you close this popup — it only stops
  // if you close the tab, navigate away, or press Stop.
  msg(`running ${pages} page(s)… keep the tab open; you can close this popup`);
  refresh();
};

$("stop").onclick = async () => {
  $("stop").disabled = true;
  msg("stopping after the current job…");
  const r = await send({ type: "stop" });
  await chrome.storage.local.remove("autoRun");
  $("stop").disabled = false;
  msg(r?.wasCollecting ? "stopped, what was read is kept" : "stopped");
  refresh();
};

// While a run is in flight the popup polls, so Stop appears without needing the
// popup to be reopened and the counter does not freeze mid-run.
setInterval(() => { if (document.visibilityState === "visible") refresh(); }, 1500);

$("export").onclick = async () => {
  const { jobs = {} } = await chrome.storage.local.get("jobs");
  const payload = {
    exported_at: new Date().toISOString(),
    count: Object.keys(jobs).length,
    jobs: Object.values(jobs),
  };
  const url = "data:application/json;charset=utf-8," +
    encodeURIComponent(JSON.stringify(payload, null, 2));
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
  await chrome.downloads.download({
    url,
    filename: `job-collector-${stamp}.json`,
    saveAs: true,
  });
  msg("saved — only needed if the pipeline is offline");
};

// Clearing only chrome.storage used to leave every job sitting in the
// pipeline's inbox, so the next rank still showed them and "cleared" was a lie.
// This wipes both sides. The bridge archives rather than deletes, so nothing is
// actually destroyed.
$("clear").onclick = async () => {
  const { jobs = {} } = await chrome.storage.local.get("jobs");
  const n = Object.keys(jobs).length;
  if (!confirm(`Erase all ${n} collected jobs, here and in the pipeline?\n\n` +
               `Your applied list is not touched.`)) return;
  busy(true);
  await chrome.storage.local.set({ jobs: {} });
  const r = await bridge({ type: "bridge:reset" });
  busy(false);
  msg(r?.ok ? `erased ${n} here, ${r.archived ?? 0} in the pipeline`
            : `erased ${n} here, pipeline offline so its copy remains`);
  refresh();
};

// Restore the last search so widening one term does not mean retyping the rest.
(async () => {
  const { search } = await chrome.storage.local.get("search");
  if (!search) return;
  $("kw").value = search.kw || "";
  $("loc").value = search.loc || "";
  if (search.radius) $("radius").value = search.radius;
  if (search.site) $("siteSel").value = search.site;
  if (search.posted) $("posted").value = search.posted;
  $("remote").checked = !!search.remote;
})();

refresh();

/* ------------------------------------------------------------------ searches
 *
 * A saved list of searches, run on demand in this browser, in your own
 * session. The daily schedule was removed on 18 September 2026: it never fired
 * reliably here, and every run was started by hand anyway.
 */
const sched = (m) => chrome.runtime.sendMessage(m).catch(() => null);

function siteOf(url) {
  let h = "";
  try { h = new URL(url).hostname.toLowerCase(); } catch { return "?"; }
  if (h.includes("linkedin")) return "LinkedIn";
  if (h.includes("stepstone")) return "StepStone";
  if (h.includes("indeed")) return "Indeed";
  return h.replace(/^www\./, "");
}

async function refreshSched() {
  const s = await sched({ type: "sched:get" });
  if (!s) return;
  $("schedPages").value = s.pages;

  const list = $("schedList");
  list.innerHTML = "";
  if (!s.searches.length) {
    const li = document.createElement("li");
    li.innerHTML = "<span>no searches saved yet</span>";
    list.appendChild(li);
  }
  s.searches.forEach((q, i) => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    // Every saved search says which site it runs on: a label alone ("ML
    // Engineer Berlin") reads the same for LinkedIn, Indeed and StepStone.
    const site = document.createElement("b");
    site.className = "site";
    site.textContent = siteOf(q.url);
    span.append(site, " ", q.label);
    span.title = q.url;
    /*
     * Edit in place.
     *
     * Before this the only way to change a saved search was to delete it and
     * rebuild it from the URL builder, so nobody ever adjusted one — they just
     * accumulated. Editing the URL directly is the honest interface here: the
     * URL *is* the search, and anyone running this can read a query string.
     */
    const ed = document.createElement("button");
    ed.textContent = "edit";
    ed.title = "change the label or the URL";
    ed.onclick = async () => {
      const label = prompt("Label for this search:", q.label);
      if (label === null) return;
      const url = prompt("Search URL:", q.url);
      if (url === null) return;
      if (!/^https?:\/\//i.test(url.trim())) { msg("that is not a URL"); return; }
      const cur = await sched({ type: "sched:get" });
      cur.searches[i] = { ...cur.searches[i], label: label.trim() || q.label,
                          url: url.trim() };
      await sched({ type: "sched:set", patch: { searches: cur.searches } });
      msg("updated");
      refreshSched();
    };

    // Off rather than deleted: a search you want back next week should not have
    // to be rebuilt. background.js skips anything with enabled === false.
    const off = document.createElement("button");
    const isOn = q.enabled !== false;
    off.textContent = isOn ? "on" : "off";
    off.title = isOn ? "click to skip this search on the next run"
                     : "click to include it again";
    if (!isOn) { span.style.opacity = ".45"; span.style.textDecoration = "line-through"; }
    off.onclick = async () => {
      const cur = await sched({ type: "sched:get" });
      cur.searches[i] = { ...cur.searches[i], enabled: !isOn };
      await sched({ type: "sched:set", patch: { searches: cur.searches } });
      refreshSched();
    };

    const rm = document.createElement("button");
    rm.textContent = "remove";
    rm.onclick = async () => {
      const cur = await sched({ type: "sched:get" });
      cur.searches.splice(i, 1);
      await sched({ type: "sched:set", patch: { searches: cur.searches } });
      refreshSched();
    };
    li.append(span, ed, off, rm);
    list.appendChild(li);
  });

  const r = s.lastResult;
  const rs = s.runState;
  // An unfinished run is the thing worth surfacing: it says the work is not
  // lost, it is waiting to pick up, which is otherwise indistinguishable from
  // the run having silently failed.
  const stalled = rs && rs.index < s.searches.length
    ? `paused at ${rs.index + 1}/${s.searches.length} — press resume. `
    : "";
  $("schedLast").textContent = stalled + (!r
    ? "never run"
    : `last run ${new Date(r.at).toLocaleString()} · ` + r.lines.join(" · "));
  $("schedRun").textContent = stalled ? "Resume now" : "Run all searches now";
}

$("schedPages").addEventListener("change", async () => {
  await sched({ type: "sched:set", patch: { pages: +$("schedPages").value } });
  refreshSched();
});

// Saving the page you are on beats retyping a URL, and it guarantees the saved
// search is one that actually works: you are looking at its results.
$("schedAdd").onclick = async () => {
  const t = await tab();
  const url = t?.url || "";
  if (!/linkedin\.com|indeed\.com|stepstone\.de/.test(url)) {
    msg("open a job search page first, then add it");
    return;
  }
  const s = await sched({ type: "sched:get" });
  if (s.searches.some((q) => q.url === url)) { msg("already saved"); return; }
  const host = new URL(url).hostname.replace(/^www\./, "").split(".")[0];
  const kw = new URLSearchParams(new URL(url).search).get("keywords") ||
             new URLSearchParams(new URL(url).search).get("q") ||
             new URLSearchParams(new URL(url).search).get("what") ||
             decodeURIComponent(new URL(url).pathname.split("/")[2] || "").replace(/-/g, " ");
  s.searches.push({ label: `${host}: ${kw || "search"}`, url });
  await sched({ type: "sched:set", patch: { searches: s.searches } });
  msg(`saved · ${s.searches.length} scheduled`);
  refreshSched();
};

$("schedRun").onclick = async () => {
  const s = await sched({ type: "sched:get" });
  const resuming = s?.runState && s.runState.index < s.searches.length;
  await sched({ type: resuming ? "sched:resume" : "sched:runNow" });
  msg(resuming ? `resuming at search ${s.runState.index + 1}…`
               : "running the saved searches in background tabs…");
};

refreshSched();
learnGeo();

/*
 * Erase everything and start fresh.
 *
 * The Maintenance "erase" clears the collected jobs but deliberately keeps the
 * seen-before history, which is right for tidying up and wrong for starting
 * over: every posting from an earlier day comes straight back as a duplicate
 * and a "fresh" run returns almost nothing. This one forgets the history too,
 * and clears the schedule's own state so a half-finished run cannot block the
 * next one.
 *
 * What it never touches: applied.json, the built CVs, and the saved searches.
 */
$("freshStart").onclick = async () => {
  const { jobs = {} } = await chrome.storage.local.get("jobs");
  const n = Object.keys(jobs).length;
  if (!confirm(
      `Start completely fresh?\n\n` +
      `Erases ${n} collected jobs here and in the pipeline, and forgets that ` +
      `they were seen, so the next scrape collects them again.\n\n` +
      `Every built CV and cover letter moves to applications/archive, so the ` +
      `Applications page starts empty. Nothing is deleted, and already-built ` +
      `jobs still never come back into a batch.\n\n` +
      `Your applied list, your 30-day job history (jobs from earlier days stay skipped) and your saved searches are NOT touched.`)) return;

  busy(true);
  msg("erasing…");
  await chrome.storage.local.set({ jobs: {} });
  await chrome.storage.local.remove(["autoRun", "autoStatus"]);
  // A stuck runState or running flag would silently block the next scrape.
  await sched({ type: "sched:set", patch: {
    runState: null, running: false, lastResult: null } });
  const r = await bridge({ type: "bridge:reset", hard: true });
  busy(false);

  msg(r?.ok
    ? `erased ${n} here, ${r.archived ?? 0} archived in the pipeline` +
      (r.build_archived ? `, ${r.build_archived} day(s) of CVs moved to applications/archive` : "") +
      ` — ready for a fresh run`
    : `erased ${n} here, but the pipeline is offline so its copy remains`);
  refresh();
  refreshSched();
};


/* ----------------------------------------------------------- share searches
 *
 * A set of searches is the most valuable thing a user of this project builds,
 * and the whole reason to open source it is that people can hand each other a
 * good one. JSON through the clipboard needs no server and no account.
 */
$("schedExport").onclick = async () => {
  const s = await sched({ type: "sched:get" });
  const text = JSON.stringify(
    (s?.searches || []).map(({ label, url, enabled }) => ({ label, url, enabled })), null, 2);
  try { await navigator.clipboard.writeText(text); msg(`copied ${(s?.searches || []).length} searches`); }
  catch (e) { msg("could not reach the clipboard"); }
};

$("schedImport").onclick = async () => {
  const raw = prompt("Paste an exported search list (JSON). This REPLACES the saved list.");
  if (raw === null) return;
  let rows;
  try { rows = JSON.parse(raw); } catch (e) { msg("that is not valid JSON"); return; }
  if (!Array.isArray(rows)) { msg("expected a JSON array"); return; }
  const clean = rows
    .filter((r) => r && typeof r.url === "string" && /^https?:\/\//i.test(r.url))
    .map((r) => ({ label: String(r.label || r.url).slice(0, 120), url: r.url,
                   enabled: r.enabled !== false }));
  if (!clean.length) { msg("no usable searches in that JSON"); return; }
  if (!confirm(`Replace the saved list with ${clean.length} search(es)?`)) return;
  await sched({ type: "sched:set", patch: { searches: clean } });
  msg(`imported ${clean.length}`);
  refreshSched();
};
