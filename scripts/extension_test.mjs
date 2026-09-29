/*
 * The extension's background worker against a running bridge, with a fake
 * chrome.* API. Run by scripts/selftest.py when Node is installed:
 *
 *   node scripts/extension_test.mjs <bridge port> extension/background.js
 *
 * Covers what moved to the dashboard on 28 September 2026: the extension
 * pulling the saved searches, moving a list it kept itself, clearing its copy
 * of the jobs after an erase, "Add the page I am on", learning LinkedIn place
 * ids, Run scrape continuing a cut-off run, and Stop scrape (29 September).
 * Changes the bridge's searches
 * and erases its pool, so point it only at a test bridge.
 */
import fs from "node:fs";
import vm from "node:vm";
const PORT = process.argv[2], SRC = process.argv[3];
const store = {};
const listeners = { msg: [], alarm: [], updated: [] };
const notes = [];
const chrome = {
  runtime: { onMessage: { addListener: (f) => listeners.msg.push(f) },
             onStartup: { addListener() {} }, onInstalled: { addListener() {} },
             getPlatformInfo: async () => ({}), lastError: null },
  storage: { local: {
    get: async (k) => { const ks = k == null ? Object.keys(store) : Array.isArray(k) ? k : [k];
      const o = {}; for (const x of ks) if (x in store) o[x] = JSON.parse(JSON.stringify(store[x])); return o; },
    set: async (o) => { Object.assign(store, JSON.parse(JSON.stringify(o))); },
    remove: async (k) => { for (const x of [].concat(k)) delete store[x]; },
  } },
  alarms: { get: (n, cb) => cb(null), create() {}, onAlarm: { addListener: (f) => listeners.alarm.push(f) } },
  tabs: { onUpdated: { addListener: (f) => listeners.updated.push(f) }, query: async () => [],
          create: async () => ({ id: 1 }), remove: async () => {}, sendMessage: async () => null },
  notifications: { create: (o, cb) => { notes.push(o); cb && cb(); } },
  windows: { update: async () => {} },
};
let code = fs.readFileSync(SRC, "utf8").replace('"http://127.0.0.1:8765"', `"http://127.0.0.1:${PORT}"`);
// Exposed for the checks below; runSchedule is replaced before any run.
code += "\n;globalThis.__t = { syncSearches, checkTrigger, flushToBridge, getSched, setSched, runSchedule };";
const ctx = { chrome, fetch, AbortController, setTimeout, clearTimeout, setInterval, clearInterval, console, URL, JSON, Date, Math, Promise, Set, Map, globalThis: null };
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(code, ctx);
// A run would drive real tabs; count the runs started instead.
vm.runInContext("runSchedule = async () => { globalThis.__ran = (globalThis.__ran||0) + 1; };", ctx);
const t = ctx.__t;
const send = (m) => new Promise((res) => { for (const f of listeners.msg) { if (f(m, {}, res) === true) return; } res(undefined); });
const B = `http://127.0.0.1:${PORT}`;
const get = (p) => fetch(B + p).then((r) => r.json());
const post = (p, b) => fetch(B + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }).then((r) => r.json());
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const ok = (c, label, extra = "") => { results.push([!!c, label]); console.log((c ? "PASS " : "FAIL ") + label + (c ? "" : "  " + extra)); };
await sleep(300);           // let the startup IIFE settle

// 1. First pull moves the browser's own list to the pipeline, once.
store.schedule = { pages: 1, searches: [
  { label: "Local only", url: "https://www.stepstone.de/jobs?what=Local+Only+Search" },
  { label: "dup of pipeline", url: (await get("/searches/list")).searches[1].url } ] };
delete store.searchesMoved; delete store.resetSeen;
const before = (await get("/searches/list")).searches.length;
ok(await t.syncSearches(), "first pull succeeds");
let after = (await get("/searches/list")).searches;
ok(after.length === before + 1 && after.some((q) => q.label === "Local only"), "the browser's own search moves to the pipeline, the duplicate does not", `${before} -> ${after.length}`);
ok(store.searchesMoved === true, "moving happens once");
ok(JSON.stringify(store.schedule.searches.map((q) => q.url)) === JSON.stringify(after.map((q) => q.url)), "the browser's copy is now the pipeline's list");
ok(typeof store.resetSeen === "number", "first pull records the last erase without acting on it");

// 2. A dashboard edit reaches the extension on the next pull, and drops a stale resume point.
await t.setSched({ runState: { index: 1, order: [{}, {}, {}], done: [], tries: {}, startedAt: Date.now() } });
await post("/searches/save", { searches: after.slice(0, 3), pages: 2 });
await t.syncSearches();
ok(store.schedule.searches.length === 3 && store.schedule.pages === 2, "an edit on the dashboard reaches the extension");
ok(store.schedule.runState === null, "a changed list drops the old resume point");

// 3. Erase on the dashboard clears the jobs this browser keeps.
store.jobs = { a: { url: "https://www.linkedin.com/jobs/view/1", title: "Old" } };
store.unsentJobs = true;
const oldSeen = store.resetSeen;
await post("/reset", { hard: false });
// A flush from before the pull must be refused by the pipeline.
const r = await post("/ingest", { jobs: Object.values(store.jobs), reset_seen: oldSeen });
ok(r.stale === true && r.added === 0, "a resend of the erased pool is refused");
await t.checkTrigger();
ok(Object.keys(store.jobs || {}).length === 0 && !store.unsentJobs, "the next check-in clears the browser's copy");
ok(store.resetSeen > oldSeen, "and records the erase");

// 4. Add the page I am on (popup message).
const added = await send({ type: "searches:add", url: "https://www.linkedin.com/jobs/search/?keywords=Popup%20Add&geoId=101282230&currentJobId=5" });
ok(added.ok && added.outcome === "added", "popup add is saved by the pipeline", JSON.stringify(added));
ok(store.schedule.searches.some((q) => /Popup/.test(q.url) && !/currentJobId/.test(q.url)), "and pulled straight back, cleaned");
const dup = await send({ type: "searches:add", url: "https://www.linkedin.com/jobs/search/?keywords=Popup+Add&geoId=101282230" });
ok(dup.ok && dup.outcome === "duplicate", "adding the same search twice says so");
const bad = await send({ type: "searches:add", url: "https://www.xing.com/jobs" });
ok(bad.ok === false && /not LinkedIn/.test(bad.why), "a site the extension cannot read is refused");

// 5. geoId learned from an open LinkedIn search.
for (const f of listeners.updated) f(1, { url: "https://www.linkedin.com/jobs/search/?keywords=x&location=Hamburg%2C%20Germany&geoId=106430557" }, {});
await sleep(500);
const geo = (await get("/searches/list")).geo;
ok(geo["hamburg, germany"] === "106430557" && geo["hamburg"] === "106430557", "a LinkedIn place id is learned from an open search", JSON.stringify(geo));

// 6. A trigger resumes a cut-off run, and otherwise starts fresh.
const order = store.schedule.searches.slice(0, 3);
await t.setSched({ running: false, runState: { index: 1, order, done: ["x"], tries: {}, startedAt: Date.now() } });
await post("/trigger", {});
await t.checkTrigger();
ok(ctx.__ran === 1 && store.schedule.runState.index === 1, "Run scrape continues a cut-off run where it stopped", JSON.stringify(store.schedule.runState).slice(0, 120));
await t.setSched({ runState: null });
await post("/trigger", {});
await t.checkTrigger();
ok(ctx.__ran === 2 && store.schedule.runState.index === 0 && store.schedule.runState.order.length === store.schedule.searches.filter((q) => q.enabled !== false).length, "and otherwise starts every search that is on");

// 7. Stop scrape on the dashboard closes the search tab and ends the run,
// keeping the resume point at the search that was cut off.
const closed = [];
chrome.tabs.remove = async (id) => { closed.push(id); };
vm.runInContext(`runOneSearch = async () => { currentTabId = 7;
  while (!stopRequested) await wait(100); return { ok: false, line: "tab closed" }; };`, ctx);
await t.setSched({ running: false, runState: { index: 1, order, done: ["x"], tries: {}, startedAt: Date.now() } });
const run = t.runSchedule();
await sleep(600);
const stopped = await post("/scrape/stop", {});
const finished = await Promise.race([run.then(() => true), sleep(15000).then(() => false)]);
const st = await get("/scrape/state");
const prog = await get("/progress");
ok(stopped.was_running && finished && closed.includes(7) && store.schedule.runState?.index === 1
   && !store.schedule.running && !st.stop && !prog.scrape.running,
   "Stop scrape closes the tab, ends the run and keeps where it stopped",
   JSON.stringify({ stopped, finished, closed, rs: store.schedule.runState?.index, st, scrape: prog.scrape }));

// 8. Nothing to run: the pipeline refuses the trigger instead of queuing it.
await post("/searches/save", { searches: (await get("/searches/list")).searches.map((q) => ({ ...q, enabled: false })) });
const none = await post("/trigger", {});
ok(none.ok === false && /switched on/.test(none.why), "Run scrape with every search off says so", JSON.stringify(none));
const ext = (await get("/searches/list")).extension;
ok(ext.connected, "the extension's check-ins show as connected");

const failed = results.filter((x) => !x[0]).length;
console.log(`${results.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
