const $ = (id) => document.getElementById(id);

/*
 * Status text and the progress bar are the same signal, so they are written in
 * one place. Progress arrives as free text from the content script, "job 12/25"
 * during a page and "page 2/3 · job 12/25" during an auto run, so the last
 * fraction in the string is the one that describes the work. Anything with no
 * fraction, "loading cards… 40", is real progress with no known total, which is
 * what the indeterminate state is for.
 */
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
    $("bridge").textContent = "pipeline offline — run serve.py";
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
    $("site").textContent = "Not a supported search page";
    $("visible").textContent = "0";
    $("collect").disabled = $("auto").disabled = true;
    $("stop").style.display = autoRunning ? "block" : "none";
    $("currentJob").textContent = "no job open";
    $("applied").disabled = true;
    return;
  }
  $("site").textContent =
    `${st.site} · ${withDesc}/${all.length} stored have descriptions`;
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

$("openReport").onclick = () => {
  if (lastReport) chrome.tabs.create({ url: "file://" + lastReport });
};

/*
 * Search builder.
 *
 * Widening a search used to mean hand-editing a URL, which is why the same
 * twelve terms kept getting scraped. Each site spells the same four ideas
 * differently, so the mapping lives here rather than in your head:
 * radius, recency and remote are named differently on all three.
 */
const SEARCH = {
  stepstone(kw, loc, radius, posted, remote) {
    const slug = kw.trim().toLowerCase().replace(/\s+/g, "-");
    const base = loc.trim()
      ? `https://www.stepstone.de/jobs/${encodeURIComponent(slug)}/in-${encodeURIComponent(loc.trim().toLowerCase())}`
      : `https://www.stepstone.de/jobs/${encodeURIComponent(slug)}`;
    const p = new URLSearchParams();
    if (radius && loc.trim()) p.set("radius", radius);
    if (posted) p.set("ag", String(posted * 86400)); // StepStone counts seconds
    if (remote) p.set("wfh", "1");
    const q = p.toString();
    return base + (q ? `?${q}` : "");
  },
  linkedin(kw, loc, radius, posted, remote) {
    const p = new URLSearchParams({ keywords: kw.trim() });
    if (loc.trim()) p.set("location", loc.trim());
    if (radius) p.set("distance", String(Math.round(radius * 0.621371))); // miles
    if (posted) p.set("f_TPR", `r${posted * 86400}`);
    if (remote) p.set("f_WT", "2");
    return "https://www.linkedin.com/jobs/search/?" + p.toString();
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

$("go").onclick = async () => {
  const kw = $("kw").value.trim();
  if (!kw) { msg("type something to search for"); return; }
  const url = SEARCH[$("siteSel").value](
    kw, $("loc").value, +$("radius").value, +$("posted").value, $("remote").checked);
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
  msg(r?.error ? `error: ${r.error}`
      : r?.cancelled ? `stopped at ${r.collected}/${r.of} · +${r.added} new kept`
      : `+${r?.added ?? 0} new (${r?.total ?? 0} total)`);
  refresh();
};

$("applied").onclick = async () => {
  const cur = await send({ type: "current" });
  if (!cur?.url) { msg("no job open on this page"); return; }
  busy(true);
  const r = await bridge({ type: "bridge:applied", urls: [cur.url] });
  busy(false);
  msg(!r?.ok ? `pipeline offline — not recorded`
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
  msg(!r?.ok ? `pipeline offline — start serve.py and retry`
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
