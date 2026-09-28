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

// Stop is deliberately absent: it is what you reach for when a run is
// misbehaving, and disabling it while it runs left no way out mid-collect.
function busy(on) {
  for (const id of ["collect", "auto", "applied"])
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
$("openArbeitnow").onclick = (e) => { e.preventDefault(); openPage("/dashboard#arbeitnow"); };
$("openSearches").onclick = () => openPage("/dashboard#searches");

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

refresh();

/* ------------------------------------------------------------------ searches
 *
 * The saved searches are kept by the pipeline and edited on the dashboard
 * (28 September 2026). What stays here is the one thing only the browser can
 * do well: save the search page you are looking at, which guarantees the saved
 * search is one that works, because you are looking at its results.
 */
const sched = (m) => chrome.runtime.sendMessage(m).catch(() => null);

async function refreshSched() {
  const s = await sched({ type: "sched:get" });
  if (!s) return;
  const on = s.searches.filter((q) => q.enabled !== false).length;
  $("schedCount").textContent = s.searches.length
    ? `${s.searches.length} saved · ${on} switched on`
    : "no saved searches yet";
  const r = s.lastResult;
  const rs = s.runState;
  // A cut-off run is worth saying: it is waiting to continue, which otherwise
  // looks the same as having failed.
  const stalled = !s.running && rs && Array.isArray(rs.order) && rs.index > 0 && rs.index < rs.order.length
    ? `stopped at ${rs.index + 1} of ${rs.order.length}; Run scrape on the dashboard continues it. `
    : "";
  $("schedLast").textContent = stalled + (r ? `last run ${new Date(r.at).toLocaleString()}` : "");
}

$("schedAdd").onclick = async () => {
  const t = await tab();
  const url = t?.url || "";
  if (!/^https:\/\/(www\.linkedin\.com\/jobs\/|[^/]*indeed\.com\/|www\.stepstone\.de\/)/.test(url)) {
    msg("open a job search page on LinkedIn, Indeed or StepStone first, then add it");
    return;
  }
  $("schedAdd").disabled = true;
  const r = await sched({ type: "searches:add", url });
  $("schedAdd").disabled = false;
  msg(!r ? "the extension did not answer, try again"
      : r.ok === false && !r.why ? `pipeline offline, so nothing was saved. Start it: ${START_HINT}`
      : r.ok === false ? `not saved: ${r.why}`
      : r.outcome === "duplicate" ? r.why
      : `saved · ${r.count} saved searches · rename or switch it off on the dashboard`);
  refreshSched();
};

refreshSched();
