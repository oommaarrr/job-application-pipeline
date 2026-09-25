/*
 * Dashboard behaviour. Extracted from serve.py, 24 September 2026.
 *
 * Polls /status and /progress every 2s and re-renders. Vanilla JS on purpose:
 * no build step, no framework, no CDN — the pipeline has to run offline.
 */
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const PHASE={scraping:["Scraping","var(--blue)"],ranking:["Ranking","var(--purple)"],
  building:["Building","var(--amber)"],done:["Done","var(--green)"],
  "built-partial":["Partly built","var(--teal)"],idle:["Idle","var(--gray)"]};
const SST={ok:["var(--green)","full"],close:["var(--green)","close"],
  short:["var(--red)","short"],thin:["var(--amber)","thin"],failed:["var(--red)","failed"]};
const CPH={done:"var(--green)",rendering:"var(--amber)",writing:"var(--blue)",
  planning:"var(--purple)",started:"var(--gray)"};

async function tick(){
  let d;
  try{ d=await (await fetch("/progress",{cache:"no-store"})).json(); }
  catch(e){ $("offline").classList.remove("hide"); return; }
  $("offline").classList.add("hide");

  renderSteps(d.steps||{});
  const [pl,pc]=PHASE[d.phase]||["…","var(--gray)"];
  $("phase").textContent=pl; $("phase").style.background=pc;
  const h=d.health||{};
  $("health").innerHTML='<span class="dot" style="background:'+(h.ok?"var(--green)":"var(--red)")+'"></span>'+(h.ok?"all good":"needs attention");
  window._autobuild=!!d.autobuild;
  const ab=$("autobuild");
  ab.textContent="autobuild "+(d.autobuild?"ON":"OFF");
  ab.style.borderColor=d.autobuild?"var(--green)":"var(--dim)";
  ab.style.color=d.autobuild?"var(--green)":"var(--dim)";
  $("stamp").textContent=new Date(d.now).toLocaleTimeString();
  const poolN=(d.pool_usable!=null?d.pool_usable:(d.collected_today||0));
  $("collInfo").textContent=poolN+" usable in pool · "+d.applied+" applied"+(d.pool_stale?" · pool is stale":"");

  // ---- stage tracker: where is the pipeline right now (or where the last run left it)
  const bpr=d.build_progress||{}, rnk=d.rank||{}, bld=d.build||{};
  const scrapeDone = d.searches_total>0 && d.searches_done>=d.searches_total;
  const rrr = (d.rank_run && d.rank_run.result) || null;
  const rankDone   = !!rnk.ran_at || !!rrr || (d.ranked_ready||0)>0;
  const buildDone  = !!bld.finished && (bpr.built||0)>0;
  const ph=d.phase;
  const stt={
    scrape: ph==="scraping"?"active":(scrapeDone||["ranking","building","done","built-partial"].includes(ph))?"done":"pending",
    rank:   ph==="ranking"?"active":(["building","done","built-partial"].includes(ph)||rankDone)?"done":"pending",
    build:  ph==="building"?"active":(buildDone||["done","built-partial"].includes(ph))?"done":"pending",
    done:   (ph==="done")?"done":((ph==="built-partial"||buildDone)?"done":"pending"),
  };
  document.querySelectorAll("#steps .step").forEach(el=>{
    const st=stt[el.dataset.k]||"pending";
    el.classList.remove("pending","active","done"); el.classList.add(st);
  });
  $("stScrape").textContent = (d.searches_total? (d.searches_done+"/"+d.searches_total+" searches"):"");
  $("stRank").textContent   = rrr? (rrr.buildable+" buildable")
    : (rnk.matches!=null? (rnk.matches+" passed"):"");
  $("stBuild").textContent  = ((bpr.target? (bpr.built+"/"+bpr.target):"")||"");
  const sug=(d.suggested_build||bpr.target||15);
  const bn=$("buildN");
  if(document.activeElement!==bn && (bn.value===""||bn.value===String(window._sug))) bn.value=sug;
  window._sug=String(sug);
  $("stDone").textContent   = (buildDone? new Date(bld.finished).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}):"");
  const rpt=$("btnReport"); const anyBuilt=(bpr.built||0)>0;
  rpt.href="/report/"; rpt.style.display=anyBuilt?"inline-block":"none";
  rpt.textContent="Open report";

  // buttons: which stage can be triggered right now
  const busy=(ph==="scraping"||ph==="ranking"||ph==="building");
  const canRank=poolN>0;
  const canBuild=poolN>0||(d.ranked_ready||0)>0;
  const bS=$("btnScrape"),bR=$("btnRank"),bB=$("btnBuild");
  bS.disabled=d.scrape.running;
  bR.disabled=busy||!canRank;
  bB.disabled=busy||!canBuild;
  bS.textContent=d.scrape.running?"Scraping\u2026":"\u25B6 Run scrape";
  bR.textContent=(ph==="ranking")?"Ranking\u2026":"\u25B6 Rank pool";
  bB.textContent=(ph==="building")?"Building\u2026":"\u25B6 Start build";
  bR.title=canRank?"score the pool locally (preview, no CVs)":"nothing in the pool to rank yet";
  bB.title=canBuild?"rank locally, then build CVs with Claude":"nothing in the pool to build from yet";

  // one plain sentence for the current state
  const LC={scraping:"var(--blue)",ranking:"var(--purple)",building:"var(--amber)",
    done:"var(--green)","built-partial":"var(--teal)",idle:"var(--dim)"};
  let head, sub="";
  if(ph==="scraping"){ head="Scraping in progress";
    sub=(d.scrape.done||0)+" of "+(d.scrape.total||0)+" searches done"; }
  else if(ph==="ranking"){ head="Ranking the pool";
    sub="the local model is scoring "+poolN+" jobs"; }
  else if(ph==="building"){ head="Building documents";
    sub=(bpr.built||0)+" of "+(bpr.target||0)+" built"+(bld.reason?(" · "+bld.reason):""); }
  else if(ph==="done"){ head="Done — last run complete";
    sub=(bpr.built||0)+" built from "+(d.collected_today)+" collected"; }
  else if(ph==="built-partial"){ head="Idle — pool ready, not fully built";
    sub=(bpr.built||0)+"/"+(bpr.target||0)+" built · "+poolN+" usable in pool"; }
  else if(rrr && !buildDone){ head="Ranked — ready to build";
    sub=rrr.buildable+" buildable · "+(d.suggested_build||rrr.strong)+" suggested to build · verdict "+rrr.verdict; }
  else { head="Idle — nothing running";
    sub = scrapeDone? ("last run: "+(d.searches_done)+" searches, "+poolN+" usable in pool"
                       +(rnk.matches!=null?(", "+rnk.matches+" passed"):"")+(buildDone?(", "+(bpr.built)+" built"):""))
                    : "waiting for a scrape"; }
  $("stageline").style.color=LC[ph]||"var(--ink)";
  $("stageline").innerHTML=esc(head)+'<span class="sub" style="color:var(--dim)">'+esc(sub)+"</span>";

  // live "how many left" bar for whatever stage is running
  const act=d.active;
  if(act && act.total>0){
    $("activeWrap").classList.remove("hide");
    const pct=Math.min(100,Math.round(100*act.done/act.total));
    const left=Math.max(0,act.total-act.done);
    const ab=$("activeBar"); ab.style.width=pct+"%"; ab.style.background=pc;
    const inprog=(act.in_progress||0);
    $("activeLabel").innerHTML="<b>"+act.done+"</b> of <b>"+act.total+"</b> "+esc(act.label)+
      (inprog? " &middot; <b>"+inprog+"</b> in progress":"")+
      " &middot; <b>"+left+"</b> left &middot; "+pct+"%";
    if(ph==="ranking") $("stRank").textContent=act.done+"/"+act.total;
  } else { $("activeWrap").classList.add("hide"); }

  // errors — dismissible. Dismissed texts are remembered per-browser so they
  // stay cleared across refreshes; a genuinely new error still shows.
  // Errors arrive as {text, fix}. Older bridges sent plain strings, so accept
  // both rather than rendering "[object Object]" against a mismatched server.
  window._lastErrs=(h.errors||[]).map(e=>typeof e==="string"?{text:e,fix:""}:e);
  const visible=window._lastErrs.filter(e=>!dismissed.has(e.text));
  if(visible.length){ $("errCard").classList.remove("hide");
    $("errs").innerHTML=visible.map(e=>
      '<li><span class="etext">'+esc(e.text)+
      (e.fix?'<div class="fix-line">&#8594; '+esc(e.fix)+'</div>':'')+
      '</span><button title="dismiss" data-e="'+encodeURIComponent(e.text)+'">&times;</button></li>'
    ).join("");
    $("errs").querySelectorAll("button[data-e]").forEach(b=>b.onclick=()=>{
      dismissErr(decodeURIComponent(b.dataset.e)); });
  } else $("errCard").classList.add("hide");

  // scrape
  const sc=d.scrape||{};
  $("scrDone").textContent=d.searches_done||0; $("scrTotal").textContent=d.searches_total||0;
  const pct=d.searches_total?Math.round(100*(d.searches_done)/d.searches_total):0;
  $("scrBar").style.width=pct+"%";
  $("scrState").textContent=sc.running?("scraping… "+(sc.done||0)+"/"+(sc.total||0)):(d.searches_done?"finished":"idle");
  // Per source, driven by what actually landed in the pool rather than a
  // hardcoded list of sites. Adding a fetcher shows up here for free; the old
  // version named LinkedIn and Indeed in the markup and still said "Indeed 0
  // jobs" months after Indeed was dropped.
  const li=h.linkedin||{};
  const srcs=(h.sources||[]);
  let chips=srcs.map(r=>
    '<span class="src"><b>'+esc(r.source)+'</b> '+r.jobs+' job'+(r.jobs===1?'':'s')+
    (r.described?' · '+r.described+' described':'')+
    (r.thin?' · <span class="warn">'+r.thin+' thin</span>':'')+'</span>').join("");
  if(li.short||li.failed){
    chips+='<span class="src" style="border-color:var(--bad)">'+
      (li.short?'<span style="color:var(--bad)">'+li.short+' short</span> ':'')+
      (li.failed?'<span style="color:var(--bad)">'+li.failed+' failed</span>':'')+'</span>';
  }
  $("scrHealth").innerHTML=chips?('<div class="src-row">'+chips+'</div>')
    :'<span class="muted">nothing collected yet</span>';
  $("scrTable").querySelector("tbody").innerHTML=(d.searches||[]).map(x=>{
    const [col,lab]=SST[x.status]||["var(--gray)",x.status];
    return "<tr><td>"+esc(x.label)+"</td><td>"+esc(siteName(x.host))+
      "</td><td class=n>"+x.found+"</td><td class=n>"+x.loaded+"</td><td class=n>"+x.added+
      "</td><td><span class=pill style=background:"+col+">"+lab+"</span>"+
      (x.userScrolled?' <span class=muted title="'+esc(x.why)+'">manual?</span>':"")+"</td></tr>";
  }).join("")||'<tr><td colspan=6 class=muted>no searches reported yet</td></tr>';

  // rank — prefer the local ranker's own result; fall back to the run.py rerank
  const rk=d.rank||{};
  const passed = rrr? rrr.buildable : rk.matches;
  $("rkMatch").textContent=(passed!=null?passed:"–");
  $("rkColl").textContent=(rk.collected!=null?rk.collected:(d.collected_today||"–"));
  $("rkAt").textContent = (d.rank_run&&d.rank_run.running)? "ranking now…"
    : rrr? ("ranked locally · "+rrr.strong+" strong · verdict "+rrr.verdict)
    : rk.ran_at?("ranked "+new Date(rk.ran_at).toLocaleTimeString()):"not run yet";
  $("rkErr").innerHTML=rk.error?'<span style="color:var(--red)">'+esc(rk.error)+"</span>":"";

  // build — structured, replacing the raw-log-only view
  const b=d.build||{},bp=d.build_progress||{},bd=d.build_detail||{};
  $("bdBuilt").textContent=bp.built||0; $("bdTarget").textContent=bp.target||0;
  $("bdRejected").textContent=(bp.rejected>0?("· "+bp.rejected+" rejected"):"");
  $("bdBar").style.width=(bp.target?Math.round(100*(bp.built||0)/bp.target):0)+"%";
  $("bdState").textContent=b.running?("building… "+(b.reason||"")):(b.finished?("finished "+new Date(b.finished).toLocaleTimeString()):"idle");
  $("btnStop").style.display=b.running?"inline-block":"none";

  const now=bd.building_now||[], builtR=bd.built_roles||[], drop=bd.dropped_roles||[], carried=bd.carried_over||[];
  // what's being written right now
  $("bdNow").innerHTML = now.length
    ? '<h3>&#9998; Writing now <span class=count>'+now.length+'</span></h3>'+
      now.map(c=>'<div class="bd-item now"><span class="spin"></span><span class="t">'+esc(c.company)+
        '</span><span class="bd-phase">'+esc(c.phase)+'</span></div>').join("")
    : (b.running && !builtR.length
        ? '<h3>&#9998; Writing now</h3><div class=muted style="padding:2px 2px 0">Preparing — reading the shortlist and planning (no documents on disk yet).</div>'
        : "");
  // completed, with the reasoning and any soft flags
  $("bdBuiltList").innerHTML = builtR.length
    ? '<h3>&#10003; Built <span class=count>'+builtR.length+'</span></h3>'+
      builtR.map(r=>'<div class="bd-item"><span class="t">'+(r.rank?("#"+r.rank+" "):"")+esc(r.company)+'</span>'+
        (r.title?' — '+esc(r.title):"")+
        (r.why?'<div class="why">'+esc(r.why)+'</div>':"")+
        (r.flags&&r.flags.length?'<div class=bd-flags>'+r.flags.map(f=>'<span class=bd-flag>'+esc(f)+'</span>').join("")+'</div>':"")+
        '</div>').join("")
    : "";
  // rejected, with the reason each was dropped
  $("bdDropped").innerHTML = drop.length
    ? '<h3>&#10007; Rejected <span class=count>'+drop.length+'</span></h3>'+
      drop.map(r=>'<div class="bd-item rej"><span class="t">'+esc(r.company||"?")+'</span>'+
        (r.title?' — '+esc(r.title):"")+
        (r.reason?'<div class="why">'+esc(r.reason)+'</div>':"")+'</div>').join("")
    : "";
  // carried over from an earlier attempt today: skipped because already built
  $("bdCarried").innerHTML = carried.length
    ? '<h3>&#8635; Skipped — already built earlier <span class=count>'+carried.length+'</span></h3>'+
      '<div class=chips>'+carried.map(c=>'<span class=chip>'+esc(c)+'</span>').join("")+'</div>'
    : "";

  const log=(bp.log||[]);
  $("bdLog").innerHTML=log.length?log.map(l=>{
    const bad=/error|failed|could not|usage limit|no network|is down|traceback/i.test(l);
    return bad?'<span class=err>'+esc(l)+"</span>":esc(l);
  }).join("\n"):"no build log yet";
  const lg=$("bdLog"); lg.scrollTop=lg.scrollHeight;
}
/* ============================================================ step outcomes
 * The last outcome of each step, under its buttons, with the fix and a Retry
 * button when it failed or stopped. Retrying is always safe: collection skips
 * searches already done, ranking reuses its cache, and a build skips every
 * application already on disk. */
const STEP_NAME={scrape:"Extension",arbeitnow:"Arbeitnow",collect:"Collect",rank:"Rank",
  build:"Build",reset:"Erase",applied:"Applied",data:"Data",bridge:"Bridge",pool:"Pool"};
const WORD={failed:"Failed",stopped:"Stopped",ok:"Done",info:"Note",started:"Running"};
const hhmm=s=>{try{return new Date(s).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"});}catch(e){return "";}};
function outcomeHTML(e,withRetry,noTime){
  const retry=(withRetry&&(e.status==="failed"||e.status==="stopped")&&ACT[e.step])
    ?'<button class="go sm" type="button" data-retry="'+esc(e.step)+'">&#8635; Retry</button>':"";
  return '<span class="st-word">'+(WORD[e.status]||esc(e.status))+'</span> '+
    (noTime?'':'<span class="muted">'+hhmm(e.at)+'</span> · ')+esc(e.message)+
    (e.fix&&(e.status==="failed"||e.status==="stopped"||e.status==="info")
      ?'<div class="fix-line">&#8594; '+esc(e.fix)+'</div>':"")+retry;
}
function wireRetry(root){
  root.querySelectorAll("button[data-retry]").forEach(b=>b.onclick=()=>doAct(b.dataset.retry));
}
function renderSteps(steps){
  const newer=(a,b)=>!a?b:!b?a:(a.at>=b.at?a:b);
  const map={ssScrape:newer(steps.scrape,steps.arbeitnow),ssRank:steps.rank,ssBuild:steps.build};
  for(const [id,e] of Object.entries(map)){
    const el=$(id); if(!el) continue;
    const show=e&&e.status!=="started";
    const key=show?(e.at+e.status):"";
    if(el.dataset.key===key) continue;           // unchanged: keep focus and hover
    el.dataset.key=key;
    el.className="step-status"+(show?" "+e.status:"");
    el.innerHTML=show?outcomeHTML(e,true):"";
    wireRetry(el);
  }
}
async function loadEvents(){
  let r; try{ r=await fetch("/events",{cache:"no-store"}).then(x=>x.json()); }catch(e){ return; }
  const ev=(r.events||[]).slice(0,25);
  const latest={}; Object.values(r.steps||{}).forEach(e=>latest[e.step]=e.at+e.status);
  const html=ev.map(e=>'<li class="act-item '+esc(e.status)+'"><time>'+hhmm(e.at)+'</time>'+
    '<span class="act-step">'+esc(STEP_NAME[e.step]||e.step)+'</span><span class="act-msg">'+
    outcomeHTML(e,latest[e.step]===e.at+e.status,true)+'</span></li>').join("");
  const list=$("actList"); if(!list) return;
  if(list.dataset.html===html) return;
  list.dataset.html=html;
  list.innerHTML=html||'<li class="muted">nothing has happened yet</li>';
  wireRetry(list);
}
function toast(m,ok){const t=$("toast");t.textContent=m;
  t.style.background=(ok===false)?"var(--red)":"var(--ink)";t.style.color=(ok===false)?"#fff":"var(--bg)";
  t.classList.add("show");clearTimeout(toast._t);toast._t=setTimeout(()=>t.classList.remove("show"),4000);}
async function post(path,body){
  try{const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body||{})});
    return await r.json();}catch(e){return {ok:false,why:"bridge not answering"};}
}
const ACT={
  scrape:{path:"/trigger",ok:"Scrape queued — the extension starts it within a minute.",confirm:null},
  arbeitnow:{path:"/arbeitnow",ok:"Arbeitnow pulling \u2014 its jobs join the pool in about a minute.",confirm:null},
  rank:{path:"/rank",ok:"Ranking started — scoring the pool with the local model.",confirm:null},
  build:{path:"/build",ok:"Local run started — ranking, then building CVs with Claude.",
    confirm:"Start the local run?\n\nThis ranks the pool locally and builds CVs & cover letters with Claude (spends usage)."},
};
async function doAct(k){
  const a=ACT[k]; if(!a) return;
  let body={};
  if(k==="build"){
    const n=parseInt($("buildN").value,10);
    if(n>0) body={top:n};
    const cmsg="Start the local run"+(n>0?(" for "+n+" applications"):"")+
      "?\n\nThis ranks the pool locally and builds CVs & cover letters with Claude (spends usage).";
    if(!confirm(cmsg)) return;
  } else if(a.confirm && !confirm(a.confirm)) return;
  const r=await post(a.path,body);
  if(r && r.ok!==false) toast(a.ok); else toast((r&&r.why)?("Couldn\u2019t start: "+r.why):"Failed to start",false);
  tick(); loadEvents();
}
let dismissed;
try{ dismissed=new Set(JSON.parse(localStorage.getItem("dismissedErrs")||"[]")); }catch(e){ dismissed=new Set(); }
function saveDismissed(){ try{ localStorage.setItem("dismissedErrs",JSON.stringify([...dismissed])); }catch(e){} }
function dismissErr(e){ dismissed.add(e); saveDismissed(); tick(); }
$("clearErrs").onclick=()=>{ (window._lastErrs||[]).forEach(e=>dismissed.add(e.text)); saveDismissed(); tick(); };
$("autobuild").onclick=async()=>{
  const r=await fetch("/autobuild",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({on:!window._autobuild})}).then(x=>x.json()).catch(()=>null);
  if(r) toast("Autobuild "+(r.autobuild?"ON — builds after each scrape":"OFF"));
  tick();
};
document.querySelectorAll("#steps .go[data-act]").forEach(b=>b.onclick=()=>doAct(b.dataset.act));
$("btnStop").onclick=async()=>{
  if(!confirm("Stop the build now?\n\nThe documents already finished are kept. You can start again later; it will resume from what is on disk and not rebuild them.")) return;
  $("btnStop").disabled=true; $("btnStop").textContent="Stopping…";
  const r=await post("/build/stop",{});
  $("btnStop").disabled=false; $("btnStop").textContent="⏹ Stop build";
  if(r && r.ok!==false && !r.still_running) toast("Build stopped. Finished documents are kept.");
  else if(r && r.still_running) toast("Sent stop, but a process is still winding down…",false);
  else toast("Couldn’t stop the build",false);
  tick();
};
tick(); setInterval(tick,2000);
loadEvents(); setInterval(loadEvents,4000);


/* ==================================================================== theme
 * Three states, not two: light, dark, and "follow the system".
 *
 * A two-state toggle silently overrides the OS forever after one click, which
 * is the thing people actually complain about. `auto` removes the attribute
 * and lets the media query in tokens.css decide.
 */
const THEMES=["auto","light","dark"], THEME_ICON={auto:"☼",light:"☀",dark:"☽"};
function applyTheme(t){
  if(t==="auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme",t);
  const b=$("themeBtn");
  if(b){ b.textContent=THEME_ICON[t]; b.title="Theme: "+t+" (click to change)"; }
  try{ localStorage.setItem("theme",t); }catch(e){}
}
let theme="auto";
try{ theme=localStorage.getItem("theme")||"auto"; }catch(e){}
applyTheme(THEMES.includes(theme)?theme:"auto");
if($("themeBtn")) $("themeBtn").onclick=()=>{
  theme=THEMES[(THEMES.indexOf(theme)+1)%THEMES.length]; applyTheme(theme);
};

/* Friendly name for a search host, for the scrape table. */
function siteName(host){
  const h=(host||"").toLowerCase();
  if(h.includes("linkedin")) return "LinkedIn";
  if(h.includes("stepstone")) return "StepStone";
  if(h.includes("indeed")) return "Indeed";
  if(h.includes("arbeitnow")) return "Arbeitnow";
  return host||"—";
}

/* ==================================================================== setup
 * Polled on its own slow timer, not with tick().
 *
 * The checks shell out to Ollama and stat the filesystem, which is far too
 * expensive to repeat every 2 seconds, and none of it changes at that rate.
 */
let setupHidden=false;
try{ setupHidden=localStorage.getItem("setupHidden")==="1"; }catch(e){}

async function loadDoctor(){
  let d;
  try{ d=await fetch("/doctor").then(r=>r.json()); }catch(e){ return; }
  window._doctor=d;
  // Until the extension has sent a single job, the only first step that works
  // is the Arbeitnow feed, so it gets the primary button, not Run scrape.
  const ext=(d.checks||[]).find(c=>c.id==="extension");
  document.documentElement.classList.toggle("no-extension", !!ext && !ext.ok);
  const card=$("setupCard"); if(!card) return;
  const failing=d.checks.filter(c=>!c.ok);

  // Shown automatically while anything is unmet, and after that only if asked
  // for. A working pipeline should not nag; a broken one should not hide.
  const show = window._setupForced || (!d.ok && !setupHidden);
  card.classList.toggle("hide", !show);

  $("setupScore").textContent=d.passed+"/"+d.total+" ready";
  $("setupScore").style.background=d.ok?"var(--ok)":(d.passed>=d.total-1?"var(--warn)":"var(--bad)");
  $("setupIntro").textContent=d.ok
    ? "Everything the pipeline needs is in place."
    : failing.length+" thing"+(failing.length===1?"":"s")+" to fix before this can run end to end.";

  $("setupList").innerHTML=d.checks.map(c=>
    '<li class="'+(c.ok?"":"bad")+'">'+
      '<span class="mark">'+(c.ok?"✓":"!")+'</span>'+
      '<span class="body">'+
        '<span class="label">'+esc(c.label)+'</span>'+
        '<div class="detail">'+esc(c.detail||"")+'</div>'+
        (!c.ok&&c.fix?'<div class="fix"><code>'+esc(c.fix)+
          '</code><button class="go" data-copy="'+encodeURIComponent(c.fix)+'">Copy</button></div>':"")+
      '</span></li>').join("");

  $("setupList").querySelectorAll("button[data-copy]").forEach(b=>b.onclick=async()=>{
    const text=decodeURIComponent(b.dataset.copy);
    try{ await navigator.clipboard.writeText(text); b.textContent="Copied"; }
    catch(e){ b.textContent="Copy failed"; }
    setTimeout(()=>{ b.textContent="Copy"; },1400);
  });
}
if($("setupRefresh")) $("setupRefresh").onclick=()=>{ window._setupForced=true; loadDoctor(); };
if($("setupHide")) $("setupHide").onclick=()=>{
  window._setupForced=false; setupHidden=true;
  try{ localStorage.setItem("setupHidden","1"); }catch(e){}
  $("setupCard").classList.add("hide");
};

/* =================================================================== funnel
 * Bars are scaled against the FIRST stage, not the largest, so the collapse
 * between two stages reads directly as lost width. A per-stage percentage of
 * the previous stage sits next to each number, because "23 passed" means
 * nothing without "out of 164".
 */
async function loadFunnel(){
  let f;
  try{ f=await fetch("/funnel").then(r=>r.json()); }catch(e){ return; }
  const card=$("funnelCard"); if(!card) return;
  if(f.erased){
    // The last ranking belongs to the pool that was erased: say so instead of
    // showing its numbers as if they were this pool's.
    card.classList.remove("hide");
    $("funnel").innerHTML='<p class="muted">Nothing yet since the erase'+
      (f.erased_at?' ('+esc(f.erased_at.replace("T"," "))+')':'')+
      '. The funnel fills again after the next scrape and rank.</p>';
    $("funnelWhy").textContent="";
    $("funnelDrops").innerHTML="";
    card.querySelector(".drops-wrap").classList.add("hide");
    return;
  }
  if(!f.ok||!f.stages||!f.stages.length){ card.classList.add("hide"); return; }
  card.classList.remove("hide");
  card.querySelector(".drops-wrap").classList.remove("hide");

  const top=Math.max(1,f.stages[0].n||1);
  $("funnel").innerHTML=f.stages.map((st,i)=>{
    const prev=i?f.stages[i-1].n:null;
    const share=prev&&prev>0?Math.round(100*st.n/prev):null;
    const w=Math.max(st.n?1.5:0,100*st.n/top);
    return '<div class="fn">'+
      '<span class="fn-label">'+esc(st.label)+'</span>'+
      '<span class="fn-track"><span class="fn-fill" style="width:'+w.toFixed(1)+'%"></span></span>'+
      '<span><span class="fn-n">'+st.n+'</span>'+
        (share!==null?'<span class="fn-drop"> '+share+'%</span>':'')+
        (st.note?'<div class="fn-note">'+esc(st.note)+'</div>':'')+'</span>'+
      '</div>';
  }).join("");

  $("funnelWhy").textContent=f.why||"";
  $("funnelDrops").innerHTML=(f.dropped||[]).map(d=>
    '<span class="drop"><b>'+d.n+'</b> '+esc(d.reason)+'</span>').join("");
}

loadDoctor(); loadFunnel();
setInterval(loadDoctor,30000);
setInterval(loadFunnel,10000);
