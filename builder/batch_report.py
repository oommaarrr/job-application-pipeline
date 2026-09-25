#!/usr/bin/env python3
"""
Render a batch's fit ranking as a page you can actually work from.

The scraper's out/jobs.html shows the mechanical ranking: relevance floor,
German gate, years required. This shows the judgement layer on top of it, which
otherwise exists only in a chat transcript and is gone the moment the window
closes: which roles were picked, in what order, why each one, what was dropped
and for what reason.

It is also the page you tick things off on. Marking a role Applied posts its URL
to the local bridge, which writes applied.json, which is what keeps that role
out of every future batch. Skipped is local to the page. Both survive a reload,
as does the Hide done filter, so you can work through a batch across several
sittings without losing your place.

    .venv/bin/python batch_report.py                today's batch
    .venv/bin/python batch_report.py 2026-08-11     a specific date

Reads applications/<date>/batch.json, writes applications/<date>/batch.html.

History worth keeping: the interaction half of this page was originally written
as a one-off generator inside applications/2026-08-12/. It was not reused, so
the next batch shipped as a static list with no way to tick anything off. That
is why it lives here now, in the generator every batch runs, rather than in a
folder for one date.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import pathlib
import sys
import unicodedata

HERE = pathlib.Path(__file__).parent
APPS = HERE / "applications"
BRIDGE = f"http://127.0.0.1:{os.environ.get('BRIDGE_PORT', '8765')}"

# The report's styling lives in web/tokens.css and web/report.css so it shares
# the design system with the dashboard and can be edited as real CSS. Both are
# INLINED rather than linked, because batch.html is opened from disk and sent to
# people — it must not depend on a running server or a sibling file.
_WEB = pathlib.Path(__file__).resolve().parent.parent / "web"


def _load_css() -> str:
    parts = []
    for name in ("tokens.css", "report.css"):
        try:
            parts.append((_WEB / name).read_text(encoding="utf-8"))
        except OSError:
            parts.append("")  # a missing stylesheet must not stop a batch report
    return "\n".join(parts)


CSS = _load_css()

# Applied before the stylesheet so a dark page never flashes white. "theme" is
# the same localStorage key the dashboard uses, and the report is served from
# the same origin (/report/), so choosing dark on either page applies to both.
THEME_BOOT = ('try{var t=localStorage.getItem("theme");'
              'if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}')

JS = r"""
/* Theme: auto (follow the system), light, dark. Shared with the dashboard. */
(function () {
  var THEMES = ["auto", "light", "dark"];
  var LABEL = {auto: "\u263C Auto", light: "\u2600 Light", dark: "\u263E Dark"};
  var btn = document.getElementById("theme");
  var cur = "auto";
  try { cur = localStorage.getItem("theme") || "auto"; } catch (e) {}
  if (THEMES.indexOf(cur) < 0) cur = "auto";
  function apply(t, persist) {
    cur = t;
    if (t === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
    if (btn) {
      btn.innerHTML = LABEL[t];
      btn.title = "Theme: " + t + " (click to change)";
      btn.setAttribute("aria-label", "Theme: " + t);
    }
    if (persist) { try { localStorage.setItem("theme", t); } catch (e) {} }
  }
  apply(cur, false);
  if (btn) btn.onclick = function () { apply(THEMES[(THEMES.indexOf(cur) + 1) % 3], true); };
  // Changed on the dashboard in another tab: follow it.
  window.addEventListener("storage", function (e) {
    if (e.key === "theme") apply(THEMES.indexOf(e.newValue) >= 0 ? e.newValue : "auto", false);
  });
  // Paper is white: print in the light theme whatever is on screen.
  var before = null;
  window.addEventListener("beforeprint", function () {
    before = cur; document.documentElement.setAttribute("data-theme", "light");
  });
  window.addEventListener("afterprint", function () { if (before) apply(before, false); });
})();

(function () {
  var KEY    = "jobpipeline.batch.__DATE__.marks";
  var HIDEK  = KEY + ".hideDone";
  var BRIDGE = "__BRIDGE__";

  var load = function () {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; }
  };
  var save = function (m) {
    try { localStorage.setItem(KEY, JSON.stringify(m)); } catch (e) {}
  };
  var marks = load();
  var cards = [].slice.call(document.querySelectorAll(".card[data-url]"));

  function paint(card) {
    var st = marks[card.dataset.url] || {};
    card.querySelectorAll("input[data-mark]").forEach(function (i) {
      i.checked = !!st[i.dataset.mark];
    });
    card.classList.toggle("applied", !!st.applied);
    card.classList.toggle("skipped", !!st.skipped);
    card.classList.toggle("done", !!(st.applied || st.skipped));
  }

  function counts() {
    var a = 0, s = 0;
    cards.forEach(function (c) {
      var st = marks[c.dataset.url] || {};
      if (st.applied) a++; else if (st.skipped) s++;
    });
    var total = cards.length || 1;
    document.getElementById("n-applied").textContent = a;
    document.getElementById("n-skipped").textContent = s;
    document.getElementById("n-left").textContent = cards.length - a - s;
    document.querySelector(".p-applied").style.width = (a / total * 100) + "%";
    document.querySelector(".p-skipped").style.width = (s / total * 100) + "%";
  }

  // Telling the bridge is what actually keeps a role out of the next batch. If
  // it is not running the tick still sticks locally, it just will not
  // deduplicate, so say so rather than failing silently.
  function tellBridge(url, el) {
    if (!el) return;
    el.textContent = "saving…";
    el.className = "sync";
    fetch(BRIDGE + "/applied", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls: [url] })
    })
      .then(function (r) { return r.json().then(function (j) {
        if (!r.ok || !j.ok) throw new Error(j.error || r.status);
        return j;
      }); })
      .then(function (j) {
        el.textContent = j.added ? "recorded in applied.json" : "already recorded";
        el.className = "sync ok";
      })
      .catch(function () {
        el.textContent = "saved here only, bridge offline";
        el.className = "sync err";
      })
      .then(function () {
        setTimeout(function () { el.textContent = ""; el.className = "sync"; }, 6000);
      });
  }

  cards.forEach(function (card) {
    paint(card);
    card.querySelectorAll("input[data-mark]").forEach(function (input) {
      input.addEventListener("change", function () {
        var url  = card.dataset.url;
        var kind = input.dataset.mark;
        var st   = marks[url] || (marks[url] = {});
        st[kind] = input.checked;
        // Applied and skipped are mutually exclusive: ticking one clears the other.
        if (input.checked) st[kind === "applied" ? "skipped" : "applied"] = false;
        save(marks);
        paint(card);
        counts();
        if (kind === "applied" && input.checked) {
          tellBridge(url, card.querySelector(".sync"));
        }
      });
    });
  });
  counts();

  // The toggle has to survive a reload, otherwise every trip back to this page
  // re-shows everything already dealt with and it has to be clicked again.
  var toggle = document.getElementById("toggle-done");
  function applyHide(hidden) {
    document.body.classList.toggle("hide-done", hidden);
    toggle.textContent = hidden ? "Show done" : "Hide done";
    toggle.setAttribute("aria-pressed", hidden ? "true" : "false");
  }
  applyHide(localStorage.getItem(HIDEK) === "1");
  toggle.addEventListener("click", function () {
    var hidden = !document.body.classList.contains("hide-done");
    try { localStorage.setItem(HIDEK, hidden ? "1" : "0"); } catch (e) {}
    applyHide(hidden);
  });

  // Filter by any text on the card: company, title, location, or a word in the
  // rationale. Deliberately not persisted, since a filter you forgot you set
  // looks exactly like a batch that lost half its roles.
  var search = document.getElementById("filter");
  search.addEventListener("input", function () {
    var q = search.value.trim().toLowerCase();
    cards.forEach(function (c) {
      c.hidden = q !== "" && c.dataset.search.indexOf(q) === -1;
    });
  });

  document.getElementById("reset").addEventListener("click", function () {
    if (!confirm("Clear every applied and skipped mark on this page? This only clears the page. Anything already written to applied.json stays there.")) return;
    marks = {};
    save(marks);
    cards.forEach(paint);
    counts();
  });

  fetch(BRIDGE + "/status")
    .then(function (r) { return r.json(); })
    .then(function (j) {
      var el = document.getElementById("bridge");
      el.className = "bridge up";
      el.innerHTML = "bridge <b>up</b> · " + (j.applied || 0) + " applied on record";
    })
    .catch(function () {
      var el = document.getElementById("bridge");
      el.className = "bridge down";
      el.innerHTML = "bridge <b>offline</b> · ticks save locally only";
    });
})();
"""


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


def backfill_urls(batch: dict) -> int:
    """
    Put the posting link back on every entry that lost it.

    The link is the single most useful thing on a card: it is how you get from
    "this CV is built" to actually applying. But it only lands in batch.json if
    whoever wrote the entry remembered to copy it, and on 19 September 11 of 15
    entries had no url at all, so eleven titles rendered as dead text.

    The link does not need remembering. Every role came from the shortlist, and
    the shortlist has it. Match on company and title, normalised the same way
    the repeat filter does, and fill in what is missing.
    """
    here = pathlib.Path(__file__).parent
    rows = []
    for name in ("shortlist.json",):
        try:
            rows += json.loads((here / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    try:
        cfg = json.loads((here / "pipeline.json").read_text(encoding="utf-8"))
        scraper = (here / cfg["scraper_root"]).resolve()
        for name in ("ranked.json", "jobs.json"):
            try:
                rows += json.loads((scraper / "out" / name).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        sys.path.insert(0, str(scraper))
        from applied_index import norm_company, norm_title  # noqa: E402
    except Exception:
        return 0

    # Documents are written with umlauts transliterated (the CV says "fuer"
    # where the posting says "für"), so a title written for a document does not
    # match the scraped one byte for byte. Fold both to plain ASCII first.
    def fold(t: str) -> str:
        t = (t or "").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
        t = t.replace("ß", "ss").replace("Ä", "ae").replace("Ö", "oe").replace("Ü", "ue")
        return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()

    index: dict = {}
    by_company: dict = {}
    for r in rows:
        if not isinstance(r, dict) or not (r.get("url") or "").strip():
            continue
        c = fold(norm_company(r.get("company", "")))
        t = fold(norm_title(r.get("title", "")))
        index.setdefault((c, t), r["url"])
        by_company.setdefault(c, set()).add(r["url"])

    filled = 0
    for job in batch.get("built", []) + batch.get("dropped", []):
        if (job.get("url") or "").strip():
            continue
        c = fold(norm_company(job.get("company", "")))
        t = fold(norm_title(job.get("title", "")))
        url = index.get((c, t))
        # Titles drift more than companies do (a document title gets tidied,
        # shortened or re-punctuated). When the company had exactly one posting
        # in the pool there is nothing to confuse it with, so take that.
        if not url and len(by_company.get(c, ())) == 1:
            url = next(iter(by_company[c]))
        # Last resort, for records the scraper mangled. One posting was stored
        # with company "Share" and the employer's name as its title: fields
        # swapped at capture, and the real employer only appeared in the
        # description, where whoever wrote the documents found it. So also look
        # for the company name sitting in a scraped TITLE, and take it only when
        # exactly one posting matches.
        if not url and len(c) >= 4:
            hits = {r["url"] for r in rows
                    if isinstance(r, dict) and (r.get("url") or "").strip()
                    and c in fold(norm_title(r.get("title", "")))}
            if len(hits) == 1:
                url = next(iter(hits))
        if url:
            job["url"] = url
            filled += 1
    return filled


def render(batch: dict) -> str:
    date = batch.get("date", "")
    built = batch.get("built", [])
    dropped = batch.get("dropped", [])
    pool = batch.get("pool_size")
    note = batch.get("note", "")

    cards = []
    for job in built:
        url = job.get("url") or ""
        flags = "".join(f"<li>{esc(f)}</li>" for f in job.get("flags", []))
        files = "".join(
            # download rather than navigate: clicking a CV should put the file on
            # the desktop, not replace the page you are working from.
            f'<a href="{esc(v)}" download="{esc(pathlib.Path(str(v)).name)}">{esc(k)}</a>'
            for k, v in (job.get("files") or {}).items() if v)
        why = esc(job.get("why", "")) or '<span class="empty">no rationale recorded</span>'
        title = (f'<a class="title" href="{esc(url)}" target="_blank" rel="noopener">'
                 f'{esc(job.get("title"))}</a>' if url
                 else f'<span class="title">{esc(job.get("title"))}</span>')
        haystack = " ".join(str(x) for x in [
            job.get("title"), job.get("company"), job.get("location"),
            job.get("why"), " ".join(job.get("flags", []))]).lower()
        cards.append(f"""
    <article class="card" data-url="{esc(url)}" data-search="{esc(haystack)}">
      <div class="head">
        <span class="rank">{esc(job.get('rank', ''))}</span>
        <span>
          {title}<br>
          <span class="org">{esc(job.get('company'))} &middot; {esc(job.get('location'))}</span>
        </span>
        <span class="marks">
          <label class="mark applied"><input type="checkbox" data-mark="applied"> <span>Applied</span></label>
          <label class="mark skipped"><input type="checkbox" data-mark="skipped"> <span>Skipped</span></label>
          <span class="sync"></span>
        </span>
      </div>
      <p class="why">{why}</p>
      {f'<ul class="flags">{flags}</ul>' if flags else ''}
      <div class="files">{files}{f'<a href="{esc(url)}" target="_blank" rel="noopener">posting</a>' if url else ''}</div>
    </article>""")

    rows = "".join(
        f"<tr><td>{esc(d.get('company'))}</td>"
        f"<td>{esc(d.get('title'))}</td>"
        f"<td class='reason'>{esc(d.get('reason'))}</td></tr>"
        for d in dropped)

    dropped_block = f"""
  <h2>Not built</h2>
  <details class="dropped">
    <summary>{len(dropped)} role(s) considered and left out &mdash; click to read the reasons</summary>
    <table>
      <thead><tr><th>Company</th><th>Role</th><th>Why not</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </details>""" if dropped else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Application batch {esc(date)}</title>
<script>{THEME_BOOT}</script><style>{CSS}</style></head>
<body>
<div class="wrap">
  <a class="back" href="{BRIDGE}/dashboard">&larr; Dashboard</a>
  <h1>Application batch &middot; {esc(date)}</h1>
  <p class="sub">{len(built)} built{f' from a pool of {esc(pool)}' if pool else ''},
     ranked by fit rather than by the scraper's order.</p>

  <div class="bar">
    <div class="bar-row">
      <span class="count applied"><b id="n-applied">0</b> applied</span>
      <span class="count skipped"><b id="n-skipped">0</b> skipped</span>
      <span class="count"><b id="n-left">0</b> left</span>
      <span class="sep"></span>
      <input type="search" id="filter" placeholder="Filter roles&hellip;" aria-label="Filter roles">
      <button id="toggle-done" aria-pressed="false">Hide done</button>
      <button id="reset">Reset marks</button>
      <button id="theme" type="button" title="Theme: auto (click to change)" aria-label="Theme: auto">&#9788; Auto</button>
      <span class="bridge" id="bridge">bridge <b>checking&hellip;</b></span>
    </div>
    <div class="progress" aria-hidden="true">
      <i class="p-applied" style="width:0"></i><i class="p-skipped" style="width:0"></i>
    </div>
  </div>
{''.join(cards) if cards else '<p class="empty">Nothing was built in this batch.</p>'}
{f'<p class="note">{esc(note)}</p>' if note else ''}
{dropped_block}
  <footer>
    <p>Ticking <b>Applied</b> writes the job into <code>scraper/history/applied.csv</code> through the local
       bridge and reranks, so the role never appears in a future batch.
       <b>Skipped</b> is local to this page only. Both survive a reload, as does the
       Hide done filter. CV and cover letter links download rather than open.</p>
    <p>If the bridge shows offline, start it with
       <code>./start.sh</code> (Windows: <code>start.bat</code>) in the project folder, then tick again.</p>
  </footer>
</div>
<script>{JS.replace("__DATE__", date).replace("__BRIDGE__", BRIDGE)}</script>
</body></html>
"""


def main() -> int:
    date = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    src = APPS / date / "batch.json"
    if not src.exists():
        print(f"no batch.json at {src}")
        return 1
    batch = json.loads(src.read_text(encoding="utf-8"))
    batch.setdefault("date", date)

    # Recover any missing posting links, and write them back, so the next run
    # of this script does not have to find them again.
    filled = backfill_urls(batch)
    if filled:
        src.write_text(json.dumps(batch, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"recovered {filled} missing posting link(s)")

    out = src.with_name("batch.html")
    out.write_text(render(batch), encoding="utf-8")
    missing = sum(1 for j in batch.get("built", []) if not (j.get("url") or "").strip())
    if missing:
        print(f"warning: {missing} built role(s) still have no link")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
