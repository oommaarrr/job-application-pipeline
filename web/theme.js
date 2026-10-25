/*
 * The theme button every page shares: auto (follow the system), light, dark.
 *
 * Three states, not two: a two-state toggle silently overrides the OS forever
 * after one click, which is the thing people actually complain about. `auto`
 * removes the attribute and lets the media query in tokens.css decide.
 *
 * One file for all three pages, so the button can never go missing from one of
 * them again (25 October 2026: the Applications page had none). Linked by
 * dashboard.html and ranking.html, and INLINED into the Applications page by
 * builder/batch_report.py, which is also opened straight from disk. The choice
 * is kept under the same "theme" key everywhere, so it follows you between
 * pages, and a change in another tab is followed here.
 */
(function () {
  const THEMES = ["auto", "light", "dark"];
  const btn = document.getElementById("themeBtn");
  let cur = "auto";
  try { cur = localStorage.getItem("theme") || "auto"; } catch (e) {}
  if (!THEMES.includes(cur)) cur = "auto";

  // topbar.css draws the icon from the title ("Theme: light" shows the sun).
  function apply(t, persist) {
    cur = t;
    if (t === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
    if (btn) {
      btn.title = "Theme: " + t + " (click to change)";
      btn.setAttribute("aria-label", "Theme: " + t);
    }
    if (persist) { try { localStorage.setItem("theme", t); } catch (e) {} }
  }
  apply(cur, false);
  if (btn) btn.onclick = () => apply(THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length], true);
  window.addEventListener("storage", (e) => {
    if (e.key === "theme") apply(THEMES.includes(e.newValue) ? e.newValue : "auto", false);
  });
  // Paper is white: print in the light theme whatever is on screen.
  let before = null;
  window.addEventListener("beforeprint", () => {
    before = cur; document.documentElement.setAttribute("data-theme", "light");
  });
  window.addEventListener("afterprint", () => { if (before) apply(before, false); });
})();
