/*
 * Fit-ranking page.
 *
 * Reads /ranking.json and renders it client-side. Filtering and sorting happen
 * here rather than on the server because the whole table is a few hundred rows
 * at most, and a round trip per keystroke would be absurd.
 */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* Same three-state theme as the dashboard, same storage key, so the choice
 * follows you between the two pages. */
const THEMES = ["auto", "light", "dark"];
const THEME_ICON = { auto: "☼", light: "☀", dark: "☽" };
function applyTheme(t) {
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
  const b = $("themeBtn");
  if (b) { b.textContent = THEME_ICON[t]; b.title = "Theme: " + t; }
  try { localStorage.setItem("theme", t); } catch (e) {}
}
let theme = "auto";
try { theme = localStorage.getItem("theme") || "auto"; } catch (e) {}
applyTheme(THEMES.includes(theme) ? theme : "auto");
$("themeBtn").onclick = () => {
  theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
  applyTheme(theme);
};

let DATA = { rows: [] };
let sortKey = "score", sortDir = -1;

const KIND = {
  sent:    { label: "SENT TO CLAUDE", color: "var(--ok)" },
  passed:  { label: "passed",         color: "var(--teal)" },
  dropped: { label: "dropped",        color: "var(--idle)" },
};

function visibleRows() {
  const q = $("q").value.trim().toLowerCase();
  const withDropped = $("showDropped").checked;
  let rows = DATA.rows.filter((r) => withDropped || r.kind !== "dropped");
  if (q) {
    rows = rows.filter((r) =>
      (r.company + " " + r.title + " " + r.location + " " + r.source)
        .toLowerCase().includes(q));
  }
  return rows.slice().sort((a, b) => {
    const x = a[sortKey], y = b[sortKey];
    if (typeof x === "number" && typeof y === "number") return (x - y) * sortDir;
    return String(x).localeCompare(String(y)) * sortDir;
  });
}

function render() {
  const rows = visibleRows();
  const body = $("tbl").querySelector("tbody");
  $("shown").textContent = rows.length + " shown";

  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty">'
      + (DATA.rows.length ? "Nothing matches that filter."
         : "No ranking yet — run <b>Rank pool</b> on the dashboard first.")
      + "</td></tr>";
    return;
  }

  // Scale the meter to the range actually on screen. Against a fixed 0-100 a
  // table of 97-to-107 is ten identical bars, which is worse than no bar.
  const scores = rows.map((r) => r.score);
  const lo = Math.min(...scores), hi = Math.max(...scores);
  const span = Math.max(1, hi - lo);

  body.innerHTML = rows.map((r) => {
    const k = KIND[r.kind] || KIND.dropped;
    const pct = Math.round(8 + 92 * (r.score - lo) / span);
    const meta = [r.location, r.source, r.berlin ? "Berlin" : "",
                  r.german ? "German " + r.german : "",
                  r.years ? r.years + "y" : ""].filter(Boolean).join(" · ");
    const title = esc(r.title || "?");
    const link = r.url
      ? '<a href="' + esc(r.url) + '" target="_blank" rel="noopener">' + title + "</a>"
      : title;
    // Plain words for the verdicts that are not the model's own labels.
    const VERDICT = {"seen-earlier": "judged earlier"};
    const badge = r.kind === "dropped" ? esc(VERDICT[r.verdict] || r.verdict || "dropped") : k.label;
    return '<tr class="' + r.kind + '">'
      + '<td class="sc n"><span class="num">' + r.score + "</span>"
      + '<span class="meter"><span style="width:' + pct + '%"></span></span></td>'
      + "<td>" + esc(r.company || "?") + "</td>"
      + '<td class="role">' + link + '<div class="meta">' + esc(meta) + "</div></td>"
      + '<td><span class="v" style="background:' + k.color + '">' + badge + "</span>"
      + (r.why ? '<div class="why">' + esc(r.why) + "</div>" : "") + "</td>"
      + "</tr>";
  }).join("");
}

document.querySelectorAll("th[data-sort]").forEach((th) => {
  th.onclick = () => {
    const key = th.dataset.sort;
    if (sortKey === key) sortDir = -sortDir;
    else { sortKey = key; sortDir = key === "score" ? -1 : 1; }
    render();
  };
});
$("q").addEventListener("input", render);
$("showDropped").addEventListener("change", render);

(async function load() {
  try { DATA = await fetch("/ranking.json").then((r) => r.json()); }
  catch (e) { $("summary").textContent = "Could not reach the bridge."; return; }

  const c = DATA.counts || {};
  $("model").textContent = DATA.model ? "· " + DATA.model : "";
  $("summary").innerHTML = DATA.ok
    ? [c.scraped + " scraped", c.judged + " judged",
       "<b>" + c.passed + " passed</b>", c.sent_to_claude + " sent to Claude",
       c.dropped + " dropped"].join(" &middot; ")
      + ((DATA.health && DATA.health.why) ? ' &middot; <span class="muted">'
         + esc(DATA.health.why) + "</span>" : "")
    : "No ranking yet. Run <b>Rank pool</b> on the dashboard first.";
  render();
})();
