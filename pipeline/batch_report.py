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
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
APPS = HERE / "applications"
BRIDGE = "http://127.0.0.1:8765"

CSS = """
:root{--bg:#fbfaf8;--ink:#1c1b19;--muted:#6b6862;--line:#e4e2dd;--card:#fff;
      --accent:#2f6f4e;--warn:#8a6d1f;--drop:#a33a30;--ok:#1a7f4b;--skip:#8a6414;
      --shadow:0 1px 2px rgba(0,0,0,.04),0 8px 24px -16px rgba(0,0,0,.18)}
@media (prefers-color-scheme:dark){:root{--bg:#191a1d;--ink:#ecebe6;--muted:#9a978f;
      --line:#32333a;--card:#212227;--accent:#6cc296;--warn:#d9b45c;--drop:#e0776b;
      --ok:#5fbf8a;--skip:#d9b45c;--shadow:0 1px 2px rgba(0,0,0,.3)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.wrap{max-width:60rem;margin:0 auto;padding:2rem 1.25rem 5rem}
h1{font-size:1.5rem;margin:0 0 .25rem}
.sub{color:var(--muted);font-size:.9rem;margin:0 0 1.25rem}
h2{font-size:1.05rem;margin:2.5rem 0 .75rem;text-transform:uppercase;
   letter-spacing:.08em;color:var(--muted)}

/* ---- sticky toolbar: counts, progress, filter, the two toggles ---- */
.bar{position:sticky;top:0;z-index:5;margin:0 -1.25rem 1.5rem;padding:.7rem 1.25rem;
     background:color-mix(in srgb,var(--bg) 88%,transparent);
     backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.bar-row{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}
.count{font-size:.85rem;color:var(--muted)}
.count b{font-weight:700;color:var(--ink);font-variant-numeric:tabular-nums}
.count.applied b{color:var(--ok)}
.count.skipped b{color:var(--skip)}
.sep{width:1px;height:1.1rem;background:var(--line)}
button{font:inherit;font-size:.82rem;padding:.32rem .7rem;border-radius:999px;
       border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button:hover{border-color:var(--accent);color:var(--accent)}
#reset:hover{border-color:var(--drop);color:var(--drop)}
input[type=search]{font:inherit;font-size:.82rem;padding:.32rem .7rem;border-radius:999px;
       border:1px solid var(--line);background:var(--card);color:var(--ink);min-width:11rem}
input[type=search]:focus{outline:none;border-color:var(--accent)}
.bridge{margin-left:auto;font-size:.78rem;color:var(--muted)}
.bridge b{font-weight:600}
.bridge.up b{color:var(--ok)}
.bridge.down b{color:var(--drop)}
.progress{height:4px;border-radius:999px;background:var(--line);margin-top:.6rem;
          overflow:hidden;display:flex}
.progress i{display:block;height:100%;transition:width .25s ease}
.progress .p-applied{background:var(--ok)}
.progress .p-skipped{background:var(--skip)}

/* ---- cards ---- */
.card{background:var(--card);border:1px solid var(--line);border-left:3px solid transparent;
      border-radius:12px;padding:1.1rem 1.25rem;margin-bottom:1rem;box-shadow:var(--shadow)}
.card.applied{border-left-color:var(--ok)}
.card.skipped{border-left-color:var(--skip)}
.card.done{opacity:.62}
.card.done:hover{opacity:1}
body.hide-done .card.done{display:none}
.card[hidden]{display:none}
.head{display:flex;gap:.75rem;align-items:baseline;flex-wrap:wrap}
.rank{font-size:1.6rem;font-weight:700;color:var(--accent);line-height:1;min-width:1.4em;
      font-variant-numeric:tabular-nums}
.title{font-size:1.05rem;font-weight:600}
/* The role title is the link to the posting. It reads as a heading until you
   hover it, so the card is not littered with underlines, but it is the thing
   you instinctively click. */
a.title{color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}
a.title:hover{color:var(--accent);border-bottom-color:var(--accent)}
a.title:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.org{color:var(--muted);font-size:.9rem}
.why{margin:.6rem 0 0}
.flags{margin:.55rem 0 0;padding:0;list-style:none;display:flex;flex-wrap:wrap;gap:.4rem}
.flags li{font-size:.78rem;color:var(--warn);border:1px solid var(--line);
          border-radius:999px;padding:.1rem .55rem}
.files{margin-top:.8rem;padding-top:.7rem;border-top:1px solid var(--line);
       display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}
.files a{font-size:.82rem;color:var(--accent);text-decoration:none;
         border:1px solid var(--line);border-radius:999px;padding:.25rem .7rem}
.files a:hover{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 8%,transparent)}

/* ---- the two marks ---- */
.mark{font-size:.82rem;display:inline-flex;gap:.35rem;align-items:center;cursor:pointer;
      border:1px solid var(--line);border-radius:999px;padding:.25rem .7rem;
      color:var(--muted);user-select:none}
.mark:hover{border-color:currentColor}
.mark input{margin:0;cursor:pointer;accent-color:currentColor}
.mark.applied:has(input:checked){color:var(--ok);border-color:var(--ok);
      background:color-mix(in srgb,var(--ok) 10%,transparent)}
.mark.skipped:has(input:checked){color:var(--skip);border-color:var(--skip);
      background:color-mix(in srgb,var(--skip) 10%,transparent)}
.marks{margin-left:auto;display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}
.sync{font-size:.75rem;color:var(--muted)}
.sync.ok{color:var(--ok)}
.sync.err{color:var(--drop)}

/* ---- dropped ---- */
details.dropped{border:1px solid var(--line);border-radius:12px;background:var(--card);
                padding:.4rem .9rem}
details.dropped summary{cursor:pointer;font-size:.9rem;color:var(--muted);padding:.5rem 0}
details.dropped summary::marker{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:.88rem;margin:.5rem 0 .75rem}
th{text-align:left;color:var(--muted);font-weight:600;font-size:.78rem;
   text-transform:uppercase;letter-spacing:.05em;padding:.4rem .5rem;
   border-bottom:1px solid var(--line)}
td{padding:.5rem;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
td.reason{color:var(--muted)}
.empty{color:var(--muted);font-style:italic}
.note{color:var(--muted);font-size:.85rem;border-left:2px solid var(--line);
      padding-left:.9rem;margin:1rem 0 0}
footer{margin-top:3rem;padding-top:1.25rem;border-top:1px solid var(--line);
       color:var(--muted);font-size:.85rem}
footer code{background:color-mix(in srgb,var(--ink) 7%,transparent);
            padding:.1rem .3rem;border-radius:4px}
@media (max-width:34rem){
  .marks{margin-left:0;width:100%}
  .bridge{margin-left:0;width:100%}
}
"""

JS = r"""
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
<title>Application batch {esc(date)}</title><style>{CSS}</style></head>
<body>
<div class="wrap">
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
    <p>Ticking <b>Applied</b> writes the URL into <code>applied.json</code> through the local
       bridge and reranks, so the role never appears in a future batch.
       <b>Skipped</b> is local to this page only. Both survive a reload, as does the
       Hide done filter. CV and cover letter links download rather than open.</p>
    <p>If the bridge shows offline, start it with
       <code>.venv/bin/python serve.py</code> in your scraper folder, then tick again.</p>
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
    out = src.with_name("batch.html")
    out.write_text(render(batch), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
