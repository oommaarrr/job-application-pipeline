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
  staleBanner(d);
  updateBanner(d);
  versionLine(d);
  rankEngine(d);
  window._autobuild=!!d.autobuild;
  const ab=$("autobuild");
  ab.textContent="autobuild "+(d.autobuild?"ON":"OFF");
  ab.style.borderColor=d.autobuild?"var(--green)":"var(--dim)";
  ab.style.color=d.autobuild?"var(--green)":"var(--dim)";
  $("stamp").textContent=new Date(d.now).toLocaleTimeString();
  const poolN=(d.pool_usable!=null?d.pool_usable:(d.collected_today||0));
  // Every collected job accounted for, in plain words, from the same rule the
  // ranker uses: "81 to judge, of 137 collected: 41 already judged on an
  // earlier day, 14 already applied or built, 1 no description to read".
  $("collInfo").textContent=(d.pool_explain||(poolN+" to judge"))+(d.pool_stale?" · the newest collection is old":"");

  // ---- stage tracker: where is the pipeline right now (or where the last run left it)
  const bpr=d.build_progress||{}, rnk=d.rank||{}, bld=d.build||{};
  const anRun=(d.steps||{}).arbeitnow, be=d.before_erase||{};
  const anOk=!!(anRun&&anRun.status==="ok");
  const scrapeDone = (d.searches_total>0 && d.searches_done>=d.searches_total) || anOk;
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
  $("stRank").textContent   = rrr? (rrr.buildable+" buildable") : (poolN>0? (poolN+" to judge"):"");
  // Only once something is building or built: "0/15" on an idle pipeline read
  // as a build in progress, when 15 is only the size of the next one.
  $("stBuild").textContent  = ((ph==="building"||(bpr.built||0)>0)&&bpr.target? (bpr.built+"/"+bpr.target):"");
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
  // While a scrape runs, Run scrape becomes its Stop button, where the eye is.
  window._scraping=!!d.scrape.running;
  bS.disabled=false;
  bS.classList.toggle("danger",window._scraping);
  $("btnStopScrape").style.display=window._scraping?"inline-block":"none";
  bR.disabled=busy||!canRank;
  bB.disabled=busy||!canBuild;
  bS.textContent=d.scrape.running?"\u23F9 Stop scrape":"\u25B6 Run scrape";
  bS.title=d.scrape.running?"Stop the scrape running in Chrome. Jobs already collected are kept.":"";
  bR.textContent=(ph==="ranking")?"Ranking\u2026":"\u25B6 Rank pool";
  bB.textContent=(ph==="building")?"Building\u2026":"\u25B6 Start build";
  bR.title=canRank?"score the pool locally (preview, no CVs)":"nothing in the pool to rank yet";
  bB.title=canBuild?"rank locally, then build CVs with Claude":"nothing in the pool to build from yet";

  // one plain sentence for the current state
  const LC={scraping:"var(--blue)",ranking:"var(--purple)",building:"var(--amber)",
    done:"var(--green)","built-partial":"var(--teal)",idle:"var(--dim)"};
  let head, sub;
  if(ph==="scraping"){ head="Scraping in progress";
    sub=(d.scrape.done||0)+" of "+(d.scrape.total||0)+" searches done"; }
  else if(ph==="ranking"){ head="Ranking the pool";
    const ac=d.active||{};
    const who=window._engine==="claude"?"Claude":"the local model";
    sub=ac.total? (who+" has judged "+(ac.done||0)+" of "+ac.total+" jobs")
                : (who+" is scoring "+poolN+" jobs"); }
  else if(ph==="building"){ head="Building documents";
    sub=(bpr.built||0)+" of "+(bpr.target||0)+" built"+(bld.reason?(" · "+bld.reason):""); }
  else if(ph==="done"){ head="Done — last run complete";
    sub=(bpr.built||0)+" built from "+(d.collected_today)+" collected"; }
  else if(ph==="built-partial"){ head="Idle — pool ready, not fully built";
    sub=(bpr.built||0)+"/"+(bpr.target||0)+" built · "+poolN+" to judge"; }
  else if(rrr && !buildDone){ head="Ranked — ready to build";
    sub=rrr.buildable+" buildable · "+(d.suggested_build||rrr.strong)+" suggested to build · verdict "+rrr.verdict; }
  else if(poolN>0 && !rankDone){ head="Idle — "+poolN+" job"+(poolN===1?"":"s")+" waiting to be ranked";
    sub="collected"+(d.erased_at?" since the erase":"")+(anOk&&!d.searches_done?" from Arbeitnow":"")+" · press Rank pool"; }
  else { head="Idle — nothing running";
    sub = scrapeDone? ("last run: "+(d.searches_done?d.searches_done+" searches, ":"")+poolN+" to judge"
                       +(rrr?(", "+rrr.buildable+" passed"):"")+(buildDone?(", "+(bpr.built)+" built"):""))
                    : (d.erased_at?"nothing collected since the erase":"waiting for a scrape"); }
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
  $("scrState").textContent=sc.running?("scraping… "+(sc.done||0)+"/"+(sc.total||0))
    :(d.searches_done?"finished":anOk?"Arbeitnow done":"idle");
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
  // Arbeitnow is a source too: its last run this round is a row, so the card
  // no longer says "0 of 0 searches" beside a pool Arbeitnow just filled.
  let anRow="";
  if(anRun&&(anRun.status==="ok"||anRun.status==="failed")){
    const m=/(\d+) jobs kept, (\d+) new/.exec(anRun.message||"")||[];
    const kept=anRun.kept!=null?anRun.kept:(m[1]||"–"), nw=anRun.new!=null?anRun.new:(m[2]||"–");
    anRow="<tr><td>Whole feed, matched by meaning</td><td>Arbeitnow</td><td class=n>"+(anRun.read!=null?anRun.read:"–")+
      "</td><td class=n>"+(anRun.status==="ok"?kept:"–")+"</td><td class=n>"+(anRun.status==="ok"?nw:"–")+
      "</td><td><span class=pill style=background:"+(anRun.status==="ok"?"var(--green)>done":"var(--red)>failed")+"</span></td></tr>";
  }
  // LinkedIn, Indeed and StepStone only run through the extension, so say when
  // it is not there rather than let Run scrape look broken.
  const ext=d.extension||{}, ss=d.saved_searches||{};
  const extEl=$("scrExt");
  if(extEl){
    const show=ss.on>0 && !ext.connected;
    extEl.classList.toggle("hide",!show);
    const html=show?((ext.seen_at?"The Chrome extension has not checked in since "+esc(whenText(ext.seen_at))+". ":"No Chrome extension has connected yet. ")+
      "Your "+ss.on+" saved search"+(ss.on===1?"":"es")+" on LinkedIn, Indeed and StepStone run through it. "+
      '<button class="linkish" type="button" data-open-searches>How to set it up</button>'):"";
    if(extEl.dataset.html!==html){       // unchanged: keep focus and hover
      extEl.dataset.html=html; extEl.innerHTML=html;
      const b=extEl.querySelector("[data-open-searches]"); if(b) b.onclick=()=>window.openSearches&&window.openSearches();
    }
  }
  $("scrPrev").innerHTML=(d.erased_at&&!d.searches_done&&be.scrape)?lastTime(be.scrape).replace("Before the erase","Extension, before the erase"):"";
  $("scrTable").querySelector("tbody").innerHTML=anRow+(d.searches||[]).map(x=>{
    const [col,lab]=SST[x.status]||["var(--gray)",x.status];
    return "<tr><td>"+esc(x.label)+"</td><td>"+esc(siteName(x.host))+
      "</td><td class=n>"+x.found+"</td><td class=n>"+x.loaded+"</td><td class=n>"+x.added+
      "</td><td><span class=pill style=background:"+col+">"+lab+"</span>"+
      (x.userScrolled?' <span class=muted title="'+esc(x.why)+'">manual?</span>':"")+"</td></tr>";
  }).join("")||(anRow?"":'<tr><td colspan=6 class=muted>'+(d.erased_at?"nothing collected since the erase":"no searches reported yet")+'</td></tr>');

  // rank — the local model's result once there is one. Before that, and while
  // it runs, the number is what it will read: the pool, not every collected job.
  // (run.py's keyword count, rank.matches, covers all collected jobs, including
  // ones judged on an earlier day or already applied to, so it is not shown.)
  const rk=d.rank||{};
  const rankingNow=ph==="ranking";
  const coll=(rk.collected!=null?rk.collected:(d.collected_today||0));
  const why=(d.pool_explain||"").replace(/^[^:]*:\s*/,"");
  let passed, unit, rkSub;
  if(rankingNow){
    const ac=d.active||{};
    passed=ac.total||poolN; unit="being judged";
    rkSub="judged "+(ac.done||0)+" of "+(ac.total||poolN)+" so far · from "+coll+" collected"+(why?(" · "+why):"");
  } else if(rrr){
    passed=rrr.buildable; unit=rrr.engine==="claude"?"passed Claude's ranking":"passed the local model";
    // The pre-filter's share is said, so "81 to judge" and "72 judged" add up.
    rkSub=(rrr.judged!=null?("of "+rrr.judged+" judged"+(rrr.set_aside?(" ("+rrr.set_aside+" more set aside as far from your roles)"):"")+", "):"")
      +"from "+coll+" collected · "+rrr.strong+" strong · verdict "+rrr.verdict;
  } else {
    passed=(poolN>0||!d.erased_at)?poolN:null; unit="to judge";
    rkSub=(d.pool_explain||(coll+" collected"))+" · "+(d.erased_at?"not ranked since the erase":"not ranked yet")+(poolN>0?", press Rank pool":"");
  }
  $("rkMatch").textContent=(passed!=null?passed:"–");
  $("rkUnit").textContent=unit;
  $("rkSub").textContent=rkSub;
  $("rkPrev").innerHTML=(passed==null&&!rankingNow&&d.erased_at&&be.rank)?lastTime(be.rank):"";
  $("rkErr").innerHTML=rk.error?'<span style="color:var(--red)">'+esc(rk.error)+"</span>":"";

  // build — structured, replacing the raw-log-only view
  const b=d.build||{},bp=d.build_progress||{},bd=d.build_detail||{};
  $("bdBuilt").textContent=bp.built||0; $("bdTarget").textContent=bp.target||0;
  $("bdRejected").textContent=(bp.rejected>0?("· "+bp.rejected+" rejected"):"");
  $("bdBar").style.width=(bp.target?Math.round(100*(bp.built||0)/bp.target):0)+"%";
  const builtNone=!b.running&&!(bp.built>0);
  // A build ranks first and writes after; until the hand-off it is waiting.
  const bdWaiting=b.running&&ph==="ranking";
  $("bdState").textContent=bdWaiting?("waiting for the ranking · "+(b.reason||"")):b.running?("building… "+(b.reason||"")):(b.finished?("finished "+whenText(b.finished))
    :(d.erased_at&&builtNone?"nothing built since the erase":"idle"));
  $("bdPrev").innerHTML=(d.erased_at&&builtNone&&be.build)
    ?lastTime(be.build)+(be.build.status==="ok"?" · those documents are in applications/archive":""):"";
  $("btnStop").style.display=b.running?"inline-block":"none";
  // A standalone ranking only; inside a build, Stop build is the one to press.
  $("btnStopRank").style.display=(d.rank_run&&d.rank_run.running&&!b.running)?"inline-block":"none";

  const now=bd.building_now||[], builtR=bd.built_roles||[], drop=bd.dropped_roles||[], carried=bd.carried_over||[];
  // what's being written right now
  $("bdNow").innerHTML = now.length
    ? '<h3>&#9998; Writing now <span class=count>'+now.length+'</span></h3>'+
      now.map(c=>'<div class="bd-item now"><span class="spin"></span><span class="t">'+esc(c.company)+
        '</span><span class="bd-phase">'+esc(c.phase)+'</span></div>').join("")
    : bdWaiting
      ? '<h3>&#9203; Ranking first</h3><div class=muted style="padding:2px 2px 0">'+(window._engine==="claude"?"Claude":"The local model")+' is judging the pool'+
        ((d.active&&d.active.total)?(' ('+d.active.done+' of '+d.active.total+')'):'')+'. Writing starts once it picks the roles.</div>'
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
  build:"Build",reset:"Erase",applied:"Applied",searches:"Searches",data:"Data",bridge:"Bridge",pool:"Pool",model:"Model",
  "rank-engine":"Ranker"};
const WORD={failed:"Failed",stopped:"Stopped",ok:"Done",info:"Note",started:"Running"};
const hhmm=s=>{try{return new Date(s).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"});}catch(e){return "";}};
const sameDay=(a,b)=>a.toDateString()===b.toDateString();
// A time for today, a date and time for anything older. A bare "05:53 PM" on
// a result from three days ago read as today's (28 September 2026).
function whenText(s){ try{ const d=new Date(s);
  return sameDay(d,new Date())?hhmm(s):d.toLocaleDateString([],{month:"short",day:"numeric"})+", "+hhmm(s);
}catch(e){ return ""; } }
function dayLabel(s){ const d=new Date(s), now=new Date(), y=new Date(); y.setDate(now.getDate()-1);
  return sameDay(d,now)?"Today":sameDay(d,y)?"Yesterday":d.toLocaleDateString([],{weekday:"short",day:"numeric",month:"short"}); }
// The last outcome of a step from before the erase, as one dated line. "today"
// is dropped from its message: it was today when it was written.
function lastTime(e){ if(!e) return "";
  const msg=(e.built!=null&&e.step==="build")?(e.built+" application(s) built"):String(e.message||"").replace(/ today$/,"");
  return 'Before the erase, <b>'+esc(whenText(e.at))+'</b>: '+esc(msg); }
function outcomeHTML(e,withRetry,noTime){
  const retry=(withRetry&&(e.status==="failed"||e.status==="stopped")&&ACT[e.step])
    ?'<button class="go sm" type="button" data-retry="'+esc(e.step)+'">&#8635; Retry</button>':"";
  return '<span class="st-word">'+(WORD[e.status]||esc(e.status))+'</span> '+
    (noTime?'':'<span class="muted">'+whenText(e.at)+'</span> · ')+esc(e.message)+
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
  // Grouped under a heading per day: without one, today's 4:55 PM sat right
  // above Saturday's 5:42 PM and the list looked out of order.
  let day="";
  const html=ev.map(e=>{
    const dl=dayLabel(e.at), head=dl!==day?'<li class="act-day">'+esc(dl)+'</li>':"";
    day=dl;
    return head+'<li class="act-item '+esc(e.status)+'"><time>'+hhmm(e.at)+'</time>'+
    '<span class="act-step">'+esc(STEP_NAME[e.step]||e.step)+'</span><span class="act-msg">'+
    outcomeHTML(e,latest[e.step]===e.at+e.status,true)+'</span></li>';}).join("");
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
    const j=await r.json().catch(()=>({}));
    // An error status is a failure even without ok:false in the body. An older
    // pipeline answers a request it does not know with 404 {"error":"not found"},
    // which used to read as success (29 September 2026).
    if(!r.ok) return {...j,ok:false,why:j.why||(r.status===404
      ?"the running pipeline is older than this page and does not know this yet. Restart it (see the banner at the top)"
      :(j.error||("error "+r.status)))};
    return j;}catch(e){return {ok:false,why:"bridge not answering"};}
}

/* Which model ranks: the local one or Claude. The pipeline keeps the choice. */
function rankEngine(d){
  window._engine=d.rank_engine||"local";
  const busy=d.phase==="ranking"||!!(d.rank_run&&d.rank_run.running);
  document.querySelectorAll('input[name="rankEngine"]').forEach(i=>{
    if(document.activeElement!==i) i.checked=(i.value===window._engine);
    i.disabled=busy;
  });
}
document.querySelectorAll('input[name="rankEngine"]').forEach(i=>i.onchange=async()=>{
  const r=await post("/rank/engine",{engine:i.value});
  if(!r||r.ok===false){ toast((r&&r.why)||"Couldn\u2019t switch",false); tick(); return; }
  toast(i.value==="claude"
    ? "Claude ranks from now on: minutes instead of hours, using some Claude usage."
    : "The local model ranks from now on: free and private, slower.");
  tick();
});

/* The page is read from disk on every load, the pipeline keeps running the code
   it started with, so an update needs a restart before the two agree. */
const PIPELINE_API=7;     // keep equal to API_VERSION in scraper/serve.py
function staleBanner(d){
  const old=d.api==null||d.api<PIPELINE_API, changed=!!d.code_changed;
  const el=$("staleBanner"); if(!el) return;
  const show=old||changed;
  el.classList.toggle("hide",!show);
  if(!show||el.dataset.busy) return;
  // One state per message, so the 2-second refresh does not rebuild it (and
  // lose a click) while it stays the same.
  const kind=old?"old":(d.can_restart?"button":"manual");
  if(el.dataset.kind===kind) return;
  el.dataset.kind=kind;
  if(kind==="button"){
    el.innerHTML='<span><b>Job Pipeline was updated.</b> Restart it to use the new version.</span>'+
      '<button class="go primary sm" type="button" id="staleRestart">Restart now</button>';
    $("staleRestart").onclick=restartPipeline;
  } else if(kind==="manual"){
    el.innerHTML='<span><b>Job Pipeline was updated.</b> Stop it (Ctrl+C in its window) and start it again to use the new version.</span>';
  } else {
    // An older pipeline has no Restart route: one command that restarts it.
    const win=/Win/i.test(navigator.platform||navigator.userAgent);
    const cmd=win?"scripts\\install-agent.bat":"scripts/install-agent.sh";
    el.innerHTML='<span><b>Job Pipeline was updated, but the running copy is older</b>, so some changes are not saved. '+
      'Restart it: in the project folder, run</span><code>'+cmd+'</code>'+
      '<button class="go sm" type="button" id="staleCopy">Copy</button>';
    $("staleCopy").onclick=async()=>{ try{ await navigator.clipboard.writeText(cmd); toast("Copied. Paste it in a terminal in the project folder."); }catch(e){} };
  }
}
/* Update now (scraper/updater.py): GitHub has a newer version. */
function updateBanner(d){
  const u=d.update||{}, el=$("updateBanner"); if(!el) return;
  let kind="", html="";
  if(!u.enabled){ kind=""; }
  else if(u.applying){ kind="applying:"+u.step;
    html="<span><b>Updating\u2026</b> "+esc(u.step||"")+"</span>"; }
  else if(u.apply_error){ kind="error:"+u.apply_error;
    html="<span><b>The update did not finish.</b> "+esc(u.apply_error)+" Nothing was lost.</span>"+
      '<button class="go sm" type="button" data-update>Try again</button>'; }
  else if(u.available){
    const n=u.behind, ch=u.changes||[];
    kind="available:"+u.latest;
    html="<span><b>An update is available</b>"+(n?(": "+n+" change"+(n===1?"":"s")):"")+
      ". Your profile, jobs, CVs and settings are kept.</span>"+
      '<button class="go primary sm" type="button" data-update>Update now</button>'+
      (ch.length?("<details><summary>What\u2019s new</summary><ul>"+ch.map(c=>"<li>"+esc(c)+"</li>").join("")+"</ul></details>"):"");
  }
  el.classList.toggle("hide",!kind);
  if(el.dataset.kind===kind) return;          // keep an open "What's new" open
  el.dataset.kind=kind; el.innerHTML=html;
  const b=el.querySelector("[data-update]"); if(b) b.onclick=applyUpdate;
}
/* The footer: which version this is, when GitHub was last asked, and Check for
   updates, which asks now. Automatic checks are hours apart (updater.py), so
   without this a fix pushed this morning stayed invisible until later. */
function versionLine(d){
  const u=d.update||{}, el=$("verInfo"); if(!el) return;
  const v=u.version?("version "+String(u.version).slice(0,7)):(u.mode==="zip"?"version unknown":"");
  let when="";
  if(!u.enabled) when="updates off";
  else if(u.checking) when="checking\u2026";
  else if(u.error) when="last check failed";
  else if(u.checked_at) when="checked "+ago(u.checked_at);
  el.textContent=[v,when].filter(Boolean).join(" \u00b7 ");
  el.title=u.error||"";
  el.classList.toggle("bad",!!(u.enabled&&u.error&&!u.checking));
}
function ago(iso){
  const s=Math.max(0,(Date.now()-new Date(iso).getTime())/1000);
  if(!isFinite(s)) return "";
  if(s<90) return "just now";
  if(s<5400) return Math.round(s/60)+" min ago";
  if(s<129600) return Math.round(s/3600)+" h ago";
  return Math.round(s/86400)+" days ago";
}
async function checkForUpdates(){
  const btn=$("checkUpdate"); if(!btn||btn.disabled) return;
  btn.disabled=true; btn.textContent="Checking\u2026";
  const r=await post("/update/check",{});
  btn.disabled=false; btn.textContent="Check for updates";
  const u=(r&&r.update)||{};
  if(r&&r.update){ updateBanner({update:u}); versionLine({update:u}); }
  if(!r||r.ok===false) toast("Couldn\u2019t check for updates: "+((r&&r.why)||"no answer"),false);
  else if(u.available){
    // The button is at the bottom of the page and the banner at the top.
    $("updateBanner").scrollIntoView({behavior:"smooth",block:"center"});
    toast("An update is available.");
  }
  else toast("You\u2019re on the newest version.");
}
if($("checkUpdate")) $("checkUpdate").onclick=checkForUpdates;
async function applyUpdate(){
  if(!confirm("Update Job Pipeline now?\n\nIt downloads the new version, keeps your profile, jobs, CVs and settings, and restarts itself. Takes about a minute.")) return;
  const r=await post("/update/apply",{});
  if(!r||r.ok===false){ toast("Couldn\u2019t update: "+((r&&r.why)||"no answer"),false); return; }
  // Wait for it to come back on the new version, then load the new page.
  for(let i=0;i<180;i++){
    await new Promise(res=>setTimeout(res,1000));
    try{ const p=await fetch("/progress",{cache:"no-store"}).then(x=>x.json());
      const u=p.update||{};
      if(!u.applying&&!u.available&&!p.code_changed&&!u.apply_error){ location.reload(); return; }
      if(u.apply_error){ tick(); return; } }catch(e){}
  }
  tick();
}

async function restartPipeline(){
  const el=$("staleBanner"); el.dataset.busy="1";
  el.innerHTML="<span><b>Restarting\u2026</b> back in a few seconds.</span>";
  const r=await post("/restart",{});
  if(!r||r.ok===false){ delete el.dataset.busy; delete el.dataset.kind; toast("Couldn\u2019t restart: "+((r&&r.why)||"no answer"),false); return; }
  // Wait for the new copy, then reload so the page and the pipeline agree.
  for(let i=0;i<40;i++){
    await new Promise(res=>setTimeout(res,1000));
    try{ const p=await fetch("/progress",{cache:"no-store"}).then(x=>x.json());
      if(p.api>=PIPELINE_API&&!p.code_changed){ location.reload(); return; } }catch(e){}
  }
  delete el.dataset.busy; delete el.dataset.kind;
  toast("It hasn\u2019t come back yet. Start it again with ./start.sh (start.bat on Windows).",false);
}const ACT={
  scrape:{path:"/trigger",ok:"Scrape queued — the extension starts it within a minute.",confirm:null},
  arbeitnow:{path:"/arbeitnow",ok:"Arbeitnow pulling \u2014 its jobs join the pool in a few minutes (the first pull takes longer).",confirm:null},
  rank:{path:"/rank",ok:"Ranking started.",confirm:null},
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
  if(k==="scrape" && r && r.ok!==false && window.scrapeQueued) return window.scrapeQueued(r);
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
document.querySelectorAll("#steps .go[data-act]").forEach(b=>b.onclick=()=>
  (b.dataset.act==="scrape"&&window._scraping)?stopScrape():doAct(b.dataset.act));
async function stopScrape(){
  if(!confirm("Stop the scrape now?\n\nJobs already collected are kept. Run scrape later continues from the search it stopped at.")) return;
  const btns=[$("btnScrape"),$("btnStopScrape")];
  btns.forEach(x=>{x.disabled=true; x.textContent="Stopping\u2026";});
  const r=await post("/scrape/stop",{});
  btns.forEach(x=>{x.disabled=false;});
  $("btnStopScrape").textContent="\u23F9 Stop scrape";
  if(r && r.ok!==false) toast(r.was_running
    ? "Scrape stopped after "+r.done+" of "+r.total+" searches. Chrome closes its tab within a few seconds."
    : "No scrape was running.");
  else toast("Couldn\u2019t stop the scrape",false);
  tick();
}
$("btnStopScrape").onclick=stopScrape;
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
$("btnStopRank").onclick=async()=>{
  if(!confirm("Stop ranking now?\n\nEvery job judged so far is saved. Press Rank pool later and it continues from where it stopped.")) return;
  $("btnStopRank").disabled=true; $("btnStopRank").textContent="Stopping…";
  const r=await post("/rank/stop",{});
  $("btnStopRank").disabled=false; $("btnStopRank").textContent="⏹ Stop ranking";
  if(r && r.ok!==false && !r.still_running) toast("Ranking stopped. Judged jobs are saved; Rank pool continues from here.");
  else toast((r&&r.why)||"Couldn’t stop the ranking",false);
  tick();
};
tick(); setInterval(tick,2000);
loadEvents(); setInterval(loadEvents,4000);


/* ==================================================================== tabs
 * Tabs inside a settings window (Saved searches, Arbeitnow). The WAI-ARIA tab
 * pattern: one tab in the Tab order, arrow keys, Home and End move between
 * them, and each tab shows its own panel. onShow gets the shown tab's id. */
function dlgTabs(list,onShow){
  const tabs=[...list.querySelectorAll('[role="tab"]')];
  const body=list.parentElement.querySelector(".an-body");
  function show(t,focus){
    tabs.forEach(x=>{ const on=x===t;
      x.setAttribute("aria-selected",String(on)); x.tabIndex=on?0:-1;
      document.getElementById(x.getAttribute("aria-controls")).hidden=!on; });
    if(body) body.scrollTop=0;
    if(focus) t.focus();
    if(onShow) onShow(t.id);
  }
  tabs.forEach((t,i)=>{
    t.addEventListener("click",()=>show(t));
    t.addEventListener("keydown",e=>{
      const n=tabs.length, j=e.key==="ArrowRight"?(i+1)%n:e.key==="ArrowLeft"?(i-1+n)%n:
        e.key==="Home"?0:e.key==="End"?n-1:-1;
      if(j<0) return; e.preventDefault(); show(tabs[j],true);
    });
  });
  return {show:id=>show(document.getElementById(id)),
          current:()=>(tabs.find(t=>t.getAttribute("aria-selected")==="true")||tabs[0]).id};
}

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
      (f.erased_at?' ('+esc(whenText(f.erased_at))+')':'')+
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
