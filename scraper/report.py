"""Self-contained HTML report. Opens in any browser, no assets, no network."""

from __future__ import annotations

import datetime as dt
import html
import json
import pathlib
from collections import Counter

import config
import language_gate

CSS = """
*{box-sizing:border-box}
:root{
  --bg:#f7f7f5; --panel:#fff; --line:#e4e2dd; --ink:#1c1b19; --muted:#6b6862;
  --accent:#2f6f4e; --warn:#a86a1f; --bad:#a33a30; --chip:#f0efec;
  --shadow:0 1px 2px rgba(0,0,0,.05),0 4px 14px rgba(0,0,0,.04);
}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#16161a; --panel:#1f1f24; --line:#33333b; --ink:#ecebe6;
  --muted:#9a978f; --accent:#6cc296; --warn:#d9a441; --bad:#e0776b; --chip:#2a2a31;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 4px 14px rgba(0,0,0,.25);
}}
:root[data-theme=dark]{
  --bg:#16161a; --panel:#1f1f24; --line:#33333b; --ink:#ecebe6;
  --muted:#9a978f; --accent:#6cc296; --warn:#d9a441; --bad:#e0776b; --chip:#2a2a31;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 4px 14px rgba(0,0,0,.25);
}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Inter,system-ui,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.02em}
.sub{color:var(--muted);font-size:14px;margin-bottom:26px}
h2{font-size:15px;text-transform:uppercase;letter-spacing:.08em;
  color:var(--muted);margin:40px 0 6px;font-weight:600;
  display:flex;align-items:center;gap:9px}
h2::after{content:"";flex:1;height:1px;background:var(--line)}
.count{font-size:12px;background:var(--chip);border:1px solid var(--line);
  border-radius:999px;padding:1px 9px;letter-spacing:0;color:var(--muted)}
h2 .count{order:1}
.why{color:var(--muted);font-size:12px;margin-top:4px;max-width:330px}
.legend+.scroll{margin-top:11px}

/* ---- applied tracking ---- */
.mark{width:38px;text-align:center}
.mark input{appearance:none;width:19px;height:19px;border:1.5px solid var(--line);
  border-radius:6px;cursor:pointer;position:relative;background:var(--panel);
  transition:.12s;vertical-align:middle}
.mark input:hover{border-color:var(--accent)}
.mark input:checked{background:var(--accent);border-color:var(--accent)}
.mark input:checked::after{content:"";position:absolute;left:5.5px;top:1.5px;
  width:5px;height:10px;border:solid #fff;border-width:0 2px 2px 0;
  transform:rotate(42deg)}
tr.done td{opacity:.42}
tr.done .title{text-decoration:line-through}
tr.done .mark, tr.done .mark input{opacity:1}
.bar-tools{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0 0}
.btn{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:6px 12px;font-size:12.5px;cursor:pointer;color:var(--ink)}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.progress{flex:1;min-width:130px;height:6px;border-radius:4px;background:var(--chip);
  overflow:hidden;border:1px solid var(--line)}
.progress i{display:block;height:100%;background:var(--accent);width:0;transition:.2s}
.tally{font-size:12.5px;color:var(--muted);white-space:nowrap}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:14px 16px;box-shadow:var(--shadow)}
.stat b{display:block;font-size:26px;letter-spacing:-.02em}
.stat span{color:var(--muted);font-size:12.5px}
.scroll{overflow-x:auto;background:var(--panel);border:1px solid var(--line);
  border-radius:12px;box-shadow:var(--shadow)}
table{border-collapse:collapse;width:100%;font-size:14px}
th{text-align:left;font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;
  color:var(--muted);padding:11px 14px;border-bottom:1px solid var(--line);
  white-space:nowrap;font-weight:600}
td{padding:12px 14px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
tr:hover td{background:var(--chip)}
.rank{color:var(--muted);font-variant-numeric:tabular-nums;width:34px}
.title{font-weight:600;text-decoration:none;color:var(--ink);display:block;
  max-width:400px}
.title:hover{color:var(--accent);text-decoration:underline}
.co{color:var(--muted);font-size:12.5px;margin-top:2px}
.loc{white-space:nowrap;font-size:13.5px}
.chip{display:inline-block;padding:2.5px 9px;border-radius:999px;font-size:12px;
  background:var(--chip);border:1px solid var(--line);white-space:nowrap}
.ok{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 35%,var(--line))}
.warn{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 35%,var(--line))}
.bad{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 35%,var(--line))}
.yrs{font-variant-numeric:tabular-nums;font-weight:600;white-space:nowrap}
.bar{height:5px;border-radius:3px;background:var(--chip);width:62px;
  overflow:hidden;margin-top:5px}
.bar i{display:block;height:100%;background:var(--accent)}
.ev{color:var(--muted);font-size:12px;font-style:italic;margin-top:4px;
  max-width:400px;display:block}
.legend{color:var(--muted);font-size:13px;margin:10px 0 0}
.legend code{background:var(--chip);padding:1px 6px;border-radius:5px;
  font-size:12px;border:1px solid var(--line)}
.toggle{position:fixed;top:16px;right:16px;background:var(--panel);
  border:1px solid var(--line);border-radius:9px;padding:7px 12px;cursor:pointer;
  color:var(--muted);font-size:13px;box-shadow:var(--shadow)}
.empty{background:var(--panel);border:1px dashed var(--line);border-radius:12px;
  padding:34px;text-align:center;color:var(--muted)}
@media(max-width:720px){.title{max-width:210px}.ev{max-width:210px}}
"""

JS = """
const t=document.getElementById('t');
t.onclick=()=>{const d=document.documentElement;
 const cur=d.getAttribute('data-theme')||
   (matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');
 d.setAttribute('data-theme',cur==='dark'?'light':'dark');};

/* ---------------------------------------------------------------- applied
 * The report is rewritten on every run, so "applied" state cannot live in the
 * markup. It is keyed by job URL in localStorage, which survives regeneration.
 * BAKED holds anything Python found in out/applied.json, so the state also
 * survives a cleared browser or a move to another machine.
 */
const KEY='jobscraper.applied.v1';
const readStore=()=>{try{return JSON.parse(localStorage.getItem(KEY))||{}}catch(e){return{}}};
let applied=readStore();
BAKED.forEach(k=>{if(!(k in applied))applied[k]=true;});
const persist=()=>{try{localStorage.setItem(KEY,JSON.stringify(applied));}catch(e){}};

const boxes=()=>[...document.querySelectorAll('input.applied')];

function paint(){
  let done=0;
  boxes().forEach(b=>{
    const on=!!applied[b.dataset.key];
    b.checked=on;
    b.closest('tr').classList.toggle('done',on);
    if(on)done++;
  });
  const total=boxes().length;
  document.getElementById('tally').textContent=
    total?`${done} of ${total} applied`:'nothing to track';
  document.getElementById('pbar').style.width=total?(done/total*100)+'%':'0';
  if(document.getElementById('hide').classList.contains('on')){
    boxes().forEach(b=>{
      b.closest('tr').style.display=applied[b.dataset.key]?'none':'';
    });
  }else{
    boxes().forEach(b=>{b.closest('tr').style.display='';});
  }
}

document.addEventListener('change',e=>{
  if(!e.target.classList.contains('applied'))return;
  const k=e.target.dataset.key;
  if(e.target.checked)applied[k]=true;else delete applied[k];
  persist();paint();
});

document.getElementById('hide').onclick=function(){
  this.classList.toggle('on');
  this.textContent=this.classList.contains('on')?'Show applied':'Hide applied';
  paint();
};

document.getElementById('reset').onclick=()=>{
  if(!confirm('Clear every applied mark?'))return;
  applied={};persist();paint();
};

/* Download a backup Python re-bakes into the next report. */
document.getElementById('backup').onclick=()=>{
  const blob=new Blob([JSON.stringify({applied:Object.keys(applied)},null,2)],
    {type:'application/json'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='applied.json';
  a.click();
  setTimeout(()=>URL.revokeObjectURL(a.href),2000);
};

paint();
"""


def _lang_chip(v) -> str:
    # "not checked" must never look like "no German required" — the first means
    # we never read the posting, the second means we read it and it was clean.
    if not v.checked:
        return '<span class="chip warn">⚠ not checked</span>'
    if v.reason.startswith("German only appears as a perk"):
        return '<span class="chip ok">perk only</span>'
    if v.level is None and v.reason.startswith("no German"):
        return '<span class="chip ok">no German required</span>'
    if v.reason == "negated":
        return '<span class="chip ok">German not required</span>'
    if v.reason == "optional":
        return ('<span class="chip ok">optional</span>' if v.level is None
                else f'<span class="chip ok">{html.escape(v.level_name)} · optional</span>')
    if v.level is None:
        return '<span class="chip warn">level unstated</span>'
    cls = "ok" if v.level <= config.GERMAN_CAP else "bad"
    return f'<span class="chip {cls}">{html.escape(v.level_name)}</span>'


def _yrs_chip(e) -> str:
    if e.years is None:
        return '<span class="yrs" style="color:var(--accent)">—</span>'
    cls = "" if e.years <= config.YOUR_YEARS else "color:var(--warn)"
    return f'<span class="yrs" style="{cls}">{e.years}+</span>'


def _load_applied(out_dir: pathlib.Path) -> list[str]:
    """
    Applied ticks live in the browser's localStorage, which is enough day to
    day. If you've used the report's "Back up ticks" button and dropped the
    file next to the report, those keys get baked into the page too — so the
    marks survive a cleared browser, a different profile, or another machine.
    """
    f = out_dir / "applied.json"
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        keys = data.get("applied", data) if isinstance(data, dict) else data
        return [str(k) for k in keys]
    except (json.JSONDecodeError, OSError):
        return []


def build(ranked, all_jobs, runs, out_path: pathlib.Path) -> pathlib.Path:
    now = dt.datetime.now().strftime("%d %b %Y, %H:%M")
    cap = language_gate.LEVEL_NAMES[config.GERMAN_CAP]
    baked = _load_applied(out_path.parent)

    rejected_lang = [j for j in all_jobs
                     if j.score_detail.on_profile and not j.language.accepted]
    reachable = [j for j in ranked
                 if j.experience.years is None or j.experience.years <= config.YOUR_YEARS]
    unchecked = [j for j in ranked if not j.language.checked]
    vetted = len(ranked) - len(unchecked)

    rows = []
    for i, j in enumerate(ranked, 1):
        ev = ""
        if j.language.evidence and j.language.level is not None:
            ev = f'<span class="ev">“{html.escape(j.language.evidence[:120])}”</span>'
        rows.append(f"""<tr>
<td class="mark"><input type="checkbox" class="applied"
     data-key="{html.escape(j.key)}" title="mark as applied"></td>
<td class="rank">{i}</td>
<td><a class="title" href="{html.escape(j.url)}" target="_blank" rel="noopener">{html.escape(j.title)}</a>
    <div class="co">{html.escape(j.company or '—')} · {html.escape(j.source)}</div>{ev}</td>
<td class="loc">{html.escape(j.location or '—')}</td>
<td>{_yrs_chip(j.experience)}</td>
<td>{_lang_chip(j.language)}</td>
<td><span class="chip">{j.score:.2f}</span><div class="bar"><i style="width:{j.score*100:.0f}%"></i></div></td>
</tr>""")

    qrows = []
    for r in sorted(runs, key=lambda r: (-r.kept, r.query)):
        cls = "bad" if r.drifted else ("warn" if r.results == 0 else "ok")
        qrows.append(f"""<tr>
<td><b>{html.escape(r.query)}</b><div class="co">{html.escape(r.source)}</div></td>
<td class="yrs">{r.results}</td><td class="yrs">{r.kept}</td>
<td class="yrs">{r.pages}</td><td class="yrs">{r.mean_score:.2f}</td>
<td><span class="chip {cls}">{html.escape(r.status)}</span></td>
</tr>""")

    table = f"""<div class="bar-tools">
  <span class="tally" id="tally">—</span>
  <span class="progress"><i id="pbar"></i></span>
  <button class="btn" id="hide">Hide applied</button>
  <button class="btn" id="backup" title="Save your ticks so they survive a cleared browser">Back up ticks</button>
  <button class="btn" id="reset">Reset</button>
</div>
<div class="scroll"><table>
<thead><tr><th title="tick when you've applied">✓</th><th></th><th>Role</th>
<th>Location</th><th>Exp.</th><th>German</th><th>Match</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>""" if rows else \
        '<div class="empty">No jobs passed the filters. Loosen <code>RELEVANCE_FLOOR</code> or add queries in <code>config.py</code>.</div>'

    # ---- rejected: every job that was collected but didn't make the cut ----
    ranked_keys = {j.key for j in ranked}
    rejected = [j for j in all_jobs if j.key not in ranked_keys]
    rej_german = [j for j in rejected if j.extras.get("verdict") == "german"]
    rej_profile = [j for j in rejected if j.extras.get("verdict") == "off-profile"]
    rej_applied = [j for j in rejected if j.extras.get("verdict") == "applied"]
    rej_seen = [j for j in rejected if j.extras.get("verdict") == "seen-before"]

    def rej_rows(items, kind):
        out = []
        for j in items:
            if kind == "german":
                reason = (f'<span class="chip bad">needs {html.escape(j.language.level_name)}</span>'
                          f'<div class="why">above your {cap} cap</div>')
                ev = (f'<span class="ev">“{html.escape(j.language.evidence[:150])}”</span>'
                      if j.language.evidence else "")
            elif kind == "applied":
                reason = ('<span class="chip">applied</span>'
                          '<div class="why">on applied.json</div>')
                ev = ""
            elif kind == "seen-before":
                reason = ('<span class="chip">seen before</span>'
                          f'<div class="why">{html.escape(j.extras.get("why", ""))}</div>')
                ev = ""
            else:
                reason = (f'<span class="chip warn">score {j.score:.2f}</span>'
                          f'<div class="why">{html.escape(j.extras.get("why", ""))}</div>')
                ev = ""
            out.append(f"""<tr>
<td><a class="title" href="{html.escape(j.url)}" target="_blank" rel="noopener">{html.escape(j.title)}</a>
    <div class="co">{html.escape(j.company or '—')} · {html.escape(j.source)}</div>{ev}</td>
<td class="loc">{html.escape(j.location or '—')}</td>
<td>{reason}</td></tr>""")
        return "".join(out)

    def rej_block(title, items, kind, blurb):
        if not items:
            return ""
        return f"""<h2>{title} <span class="count">{len(items)}</span></h2>
<p class="legend">{blurb}</p>
<div class="scroll"><table>
<thead><tr><th>Role</th><th>Location</th><th>Why it was cut</th></tr></thead>
<tbody>{rej_rows(items, kind)}</tbody></table></div>"""

    rejected_html = (
        rej_block("Cut — too much German", rej_german, "german",
                  f"These demand German above your {cap} cap. The quoted line is the "
                  f"exact sentence the decision came from — if one looks wrong, that "
                  f"quote tells you why.")
        + rej_block("Cut — off-profile", rej_profile, "off-profile",
                    f"These scored below <code>RELEVANCE_FLOOR</code> "
                    f"({config.RELEVANCE_FLOOR}) against your CV terms. If something "
                    f"here should have matched, add its keyword to "
                    f"<code>CORE_TERMS</code> in <code>config.py</code>.")
        + rej_block("Already applied", rej_applied, "applied",
                    "Held back because they are on <code>applied.json</code>, not "
                    "because anything was wrong with them. If one is here by mistake, "
                    "remove its URL from that file and rerun.")
        + rej_block("Seen in an earlier scrape", rej_seen, "seen-before",
                    "Collected before, so they are not re-reviewed here. Nothing is "
                    "wrong with them and they were never judged on merit. The date is "
                    "when the URL was first recorded in <code>seen_jobs.json</code>; "
                    "delete that file to review everything from scratch.")
    ) or ('<h2>Cut</h2><p class="legend">Nothing was rejected — every collected '
          'job passed both filters.</p>')

    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job scan · {html.escape(config.LOCATION)}</title><style>{CSS}</style></head>
<body><button class="toggle" id="t">◐ theme</button><div class="wrap">

<h1>Job scan · {html.escape(config.LOCATION)}</h1>
<div class="sub">{now} · German cap <b>{cap}</b> · ranked by fewest years of
experience required, then match strength</div>

<div class="stats">
  <div class="stat"><b>{len(ranked)}</b><span>passed all filters</span></div>
  <div class="stat"><b>{vetted}</b><span>German actually verified</span></div>
  <div class="stat"><b>{len(reachable)}</b><span>within {config.YOUR_YEARS} yrs exp.</span></div>
  <div class="stat"><b>{len(rejected_lang)}</b><span>cut on German</span></div>
  <div class="stat"><b>{len(rej_applied)}</b><span>already applied</span></div>
  <div class="stat"><b>{len(all_jobs)}</b><span>collected</span></div>
  <div class="stat"><b>{sum(1 for r in runs if r.drifted)}</b><span>queries drifted</span></div>
</div>
{f'<p class="legend">⚠ {len(unchecked)} listing(s) show <b>not checked</b> — their description was never fetched (capped by <code>MAX_DETAIL_FETCHES</code>), so the German filter has not seen them. Raise the cap or verify those by hand.</p>' if unchecked else ''}

<h2>Matches <span class="count">{len(ranked)}</span></h2>
<p class="legend">Ranked by fewest years of experience required, then by match
strength. <code>—</code> in Exp. means no requirement was stated at all, which
is why those rank first. Italic text under a role is the exact sentence the
German verdict came from.</p>
{table}

{rejected_html}

<h2>Query scoreboard</h2>
<div class="scroll"><table>
<thead><tr><th>Query</th><th>Seen</th><th>New</th><th>Pages</th>
<th>Mean</th><th>Outcome</th></tr></thead>
<tbody>{''.join(qrows)}</tbody></table></div>
<p class="legend">A query is abandoned once the rolling mean of the last
{config.DRIFT_WINDOW} titles falls below <code>{config.DRIFT_THRESHOLD}</code>.
Low <b>Mean</b> or an early drift means that search term isn't finding your kind
of role — retire it or rewrite it in <code>config.py</code>.</p>

</div><script>const BAKED={json.dumps(baked)};{JS}</script></body></html>"""

    out_path.write_text(doc, encoding="utf-8")
    return out_path


def write_markdown(ranked, all_jobs, runs, out_path: pathlib.Path) -> pathlib.Path:
    """Plain-text ranked list — easy to paste into notes, a doc, or a message."""
    now = dt.datetime.now().strftime("%d %b %Y, %H:%M")
    cap = language_gate.LEVEL_NAMES[config.GERMAN_CAP]
    unchecked = [j for j in ranked if not j.language.checked]
    rejected = [j for j in all_jobs
                if j.score_detail.on_profile and not j.language.accepted]

    L = [
        f"# Job scan — {config.LOCATION}",
        "",
        f"_{now} · German cap **{cap}** · ranked by fewest years of experience "
        f"required, then match strength_",
        "",
        f"- **{len(ranked)}** passed all filters",
        f"- **{len(ranked) - len(unchecked)}** had their German requirement actually verified",
        f"- **{len(rejected)}** cut for demanding more German than {cap}",
        f"- **{len(all_jobs)}** collected · **{sum(1 for r in runs if r.drifted)}** queries drifted",
        "",
    ]
    if unchecked:
        L += [f"> ⚠ {len(unchecked)} listing(s) below are marked **not checked** — "
              f"their description was never fetched, so the German filter has not "
              f"seen them. Raise `MAX_DETAIL_FETCHES` or check those by hand.", ""]

    L += ["## Matches", "",
          "| # | Role | Location | Exp. | German | Match |",
          "|---|------|----------|------|--------|-------|"]
    for i, j in enumerate(ranked, 1):
        title = (j.title or "").replace("|", "\\|")
        loc = (j.location or "—").replace("|", "\\|")
        co = (j.company or "—").replace("|", "\\|")
        L.append(f"| {i} | [{title}]({j.url})<br><sub>{co} · {j.source}</sub> | "
                 f"{loc} | {j.experience.label} | {j.language.badge} | {j.score:.2f} |")

    ranked_keys = {j.key for j in ranked}
    rej = [j for j in all_jobs if j.key not in ranked_keys]
    if rej:
        L += ["", "## Cut", "",
              "| Role | Location | Why it was cut |",
              "|------|----------|----------------|"]
        for j in rej:
            title = (j.title or "").replace("|", "\\|")
            loc = (j.location or "—").replace("|", "\\|")
            if j.extras.get("verdict") == "german":
                why = (f"needs **{j.language.level_name}** (above your {cap})"
                       + (f" — “{j.language.evidence[:100]}”".replace("|", "\\|")
                          if j.language.evidence else ""))
            else:
                why = j.extras.get("why", "off-profile").replace("|", "\\|")
            L.append(f"| [{title}]({j.url}) | {loc} | {why} |")

    L += ["", "## Query scoreboard", "",
          "| Query | Source | Seen | New | Pages | Mean | Outcome |",
          "|-------|--------|------|-----|-------|------|---------|"]
    for r in sorted(runs, key=lambda r: (-r.kept, r.query)):
        L.append(f"| {r.query} | {r.source} | {r.results} | {r.kept} | "
                 f"{r.pages} | {r.mean_score:.2f} | {r.status} |")
    L += ["",
          f"_A query is abandoned once the rolling mean of the last "
          f"{config.DRIFT_WINDOW} titles falls below {config.DRIFT_THRESHOLD}. "
          f"`blocked / timed out` is a network problem, not a verdict on the "
          f"search term._", ""]

    out_path.write_text("\n".join(L), encoding="utf-8")
    return out_path


def _one_line(text: str, limit: int) -> str:
    """Collapse to a single line so one job is always exactly one row."""
    flat = " ".join((text or "").split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def write_audit(all_jobs, ranked, out_path: pathlib.Path) -> pathlib.Path:
    """
    A one-pass review packet for checking the filters against a model.

    The gate and the scorer are lexical, so they fail in ways only a reader
    catches: a sentence about English that mentions German, a title whose
    wording hides a real match. Reading full descriptions to find those costs
    more than the scan itself, so this compresses every decision to one line.

    A rejected job gets the exact evidence that condemned it, which for German
    is the sentence the verdict was read from. A job that passed gets a short
    description excerpt, since spotting a false accept needs some substance.
    Jobs held back as already applied are counted but not listed, because that
    is a bookkeeping fact rather than a judgement that can be wrong.

    Writes both a JSON form and audit.txt, the compact one for the model.
    """
    ranked_keys = {j.key for j in ranked}
    rejected = [j for j in all_jobs
                if j.key not in ranked_keys and j.extras.get("verdict") != "applied"]
    applied_n = sum(1 for j in all_jobs if j.extras.get("verdict") == "applied")

    rows_rejected, rows_passed = [], []

    for i, j in enumerate(sorted(rejected, key=lambda x: x.extras.get("verdict", "")), 1):
        verdict = j.extras.get("verdict", "unknown")
        if verdict == "german":
            evidence = _one_line(j.language.evidence, 180) or "(no sentence captured)"
            detail = f"needs {j.language.level_name}"
        else:
            evidence = _one_line(j.extras.get("why", ""), 180)
            detail = f"score {j.score:.2f}"
        rows_rejected.append({
            "id": f"R{i:02d}", "verdict": verdict, "detail": detail,
            "title": j.title, "company": j.company, "location": j.location,
            "url": j.url, "evidence": evidence,
        })

    for i, j in enumerate(ranked, 1):
        rows_passed.append({
            "id": f"P{i:02d}", "title": j.title, "company": j.company,
            "location": j.location, "url": j.url,
            "years": j.experience.label, "german": j.language.level_name,
            "excerpt": _one_line(j.description, config.AUDIT_DESC_CHARS),
        })

    payload = {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "counts": {"collected": len(all_jobs), "passed": len(rows_passed),
                   "rejected": len(rows_rejected), "already_applied": applied_n},
        "rejected": rows_rejected, "passed": rows_passed,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    # The text form is what actually goes to the model.
    lines = [
        f"FILTER AUDIT · {payload['generated']}",
        f"{len(all_jobs)} collected · {len(rows_passed)} passed · "
        f"{len(rows_rejected)} rejected · {applied_n} already applied "
        f"(not listed, bookkeeping not judgement)",
        "",
        "Check each decision. Flag by id only where the filter got it wrong.",
        "",
        f"REJECTED ({len(rows_rejected)}) · id | verdict | detail | title @ company | evidence",
    ]
    for r in rows_rejected:
        lines.append(f"{r['id']} | {r['verdict']} | {r['detail']} | "
                     f"{r['title']} @ {r['company'] or '?'} | {r['evidence']}")
    lines += ["", f"PASSED ({len(rows_passed)}) · id | title @ company | years | german | excerpt"]
    for p in rows_passed:
        lines.append(f"{p['id']} | {p['title']} @ {p['company'] or '?'} | "
                     f"{p['years']} | {p['german']} | {p['excerpt']}")

    txt_path = out_path.with_suffix(".txt")
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return txt_path


def write_all_json(all_jobs, out_path: pathlib.Path) -> pathlib.Path:
    """
    Every job seen this run, with the flag that decided its fate.

    jobs.json holds only survivors, so a rejected job used to leave no trace at
    all and there was no way to ask "how many did German actually cost me".
    This is the full accounting: one flag per job, no prose. The per-job
    reasoning stays in jobs.html for when a verdict looks wrong.

    verdict is one of:
        passed       cleared every filter and is in the ranking
        applied      already on applied.json
        off-profile  title scored below RELEVANCE_FLOOR
        german       the gate found a requirement above GERMAN_CAP
    """
    data = [{
        "title": j.title, "company": j.company, "location": j.location,
        "url": j.url, "source": j.source,
        "verdict": j.extras.get("verdict", "unknown"),
        "match_score": j.score,
        "years_required": j.experience.years,
        "german_level": j.language.level_name,
    } for j in all_jobs]

    counts = Counter(row["verdict"] for row in data)
    payload = {"total": len(data), "by_verdict": dict(counts), "jobs": data}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return out_path


def write_json(ranked, out_path: pathlib.Path) -> pathlib.Path:
    data = [{
        "title": j.title, "location": j.location, "url": j.url,
        "company": j.company, "source": j.source, "query": j.query,
        "match_score": j.score,
        "years_required": j.experience.years,
        "years_source": j.experience.source,
        "german_level": j.language.level_name,
        "german_reason": j.language.reason,
        "german_evidence": j.language.evidence,
    } for j in ranked]
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
