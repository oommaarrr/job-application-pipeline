/*
 * The saved-searches dialog (the "Searches" button beside Run scrape), and the
 * Start over card.
 *
 * The pipeline keeps the saved searches (scraper/saved_searches.py) and the
 * Chrome extension pulls them about once a minute, then runs them in your own
 * logged-in browser. Until 28 September 2026 all of this lived in the
 * extension's popup. Uses $, esc, post, toast, tick and loadEvents from
 * dashboard.js.
 */
(function(){
const dlg=$("srDlg"); if(!dlg) return;

/* ------------------------------------------------------------ URL helpers
 * Each site spells the same ideas differently: radius, recency and remote are
 * named differently on all three, and LinkedIn's radius is in miles. */
const INDEED=[["de","Germany"],["at","Austria"],["ch","Switzerland"],["nl","Netherlands"],
  ["be","Belgium"],["uk","United Kingdom"],["ie","Ireland"],["fr","France"],["es","Spain"],
  ["it","Italy"],["pl","Poland"],["www","United States"],["ca","Canada"],["au","Australia"],
  ["in","India"],["ae","United Arab Emirates"],["sg","Singapore"]];
// LinkedIn pins a place by geoId; the extension learns ids from the searches
// you open. Germany is known, because the example searches use it.
const KNOWN_GEO={germany:"101282230"};
const BUILD={
  linkedin(f,geo){
    const p=new URLSearchParams({keywords:f.kw});
    if(f.loc) p.set("location",f.loc);
    if(geo) p.set("geoId",geo);
    if(f.radius) p.set("distance",String(Math.round(f.radius*0.621371)));
    if(f.posted) p.set("f_TPR","r"+(f.posted*86400));
    if(f.remote) p.set("f_WT","2");
    return "https://www.linkedin.com/jobs/search/?"+p.toString();
  },
  indeed(f){
    const p=new URLSearchParams({q:f.kw});
    if(f.loc) p.set("l",f.loc);
    if(f.radius) p.set("radius",String(f.radius));
    if(f.posted) p.set("fromage",String(f.posted));
    if(f.remote) p.set("sc","0kf:attr(DSQF7);");
    return "https://"+(f.indeed||"de")+".indeed.com/jobs?"+p.toString();
  },
  stepstone(f){
    const p=new URLSearchParams({what:f.kw});
    if(f.loc) p.set("where",f.loc);
    if(f.radius) p.set("radius",String(f.radius));
    if(f.posted) p.set("ag",String(f.posted));
    if(f.remote) p.set("wt","home-office");
    return "https://www.stepstone.de/jobs?"+p.toString();
  },
};
function siteOf(url){
  let h=""; try{ h=new URL(url).hostname.toLowerCase(); }catch(e){ return ""; }
  if(/(^|\.)linkedin\.com$/.test(h)) return "LinkedIn";
  if(/(^|\.)indeed\.com$/.test(h)) return "Indeed";
  if(/(^|\.)stepstone\.de$/.test(h)) return "StepStone";
  return "";
}
function kwOf(u){
  const q=u.searchParams;
  let kw=q.get("keywords")||q.get("q")||q.get("what")||"";
  if(!kw){ const seg=u.pathname.split("/").filter(Boolean);
    if(seg.length>1&&seg[0]==="jobs") kw=decodeURIComponent(seg[1]).replace(/-/g," "); }
  return kw.trim();
}
// The same rule as saved_searches.key(): site, place and keywords.
function keyOf(url){
  try{ const u=new URL(url), q=u.searchParams;
    const host=u.hostname.replace(/^www\./,"").split(".")[0];
    const geo=q.get("geoId")||q.get("location")||q.get("l")||q.get("where")||"";
    return host+":"+geo.trim().toLowerCase()+":"+kwOf(u).toLowerCase();
  }catch(e){ return url; }
}
// A search in words, from its address, so a list of URLs is readable.
function describe(url){
  let u; try{ u=new URL(url); }catch(e){ return ""; }
  const q=u.searchParams, site=siteOf(url), bits=[];
  const kw=kwOf(u); if(kw) bits.push("“"+kw+"”");
  const loc=q.get("location")||q.get("l")||q.get("where");
  if(loc) bits.push(loc);
  let days=null;
  if(site==="LinkedIn"){ const t=/^r(\d+)$/.exec(q.get("f_TPR")||""); if(t) days=Math.round(t[1]/86400); }
  else if(site==="Indeed") days=+q.get("fromage")||null;
  else if(site==="StepStone") days=+q.get("ag")||null;
  if(days) bits.push(days===1?"past day":"past "+days+" days");
  const remote=site==="LinkedIn"?/(^|,)2(,|$)/.test(q.get("f_WT")||"")
    :site==="Indeed"?/DSQF7/.test(q.get("sc")||""):/home-office/.test(q.get("wt")||"");
  if(remote) bits.push("remote only");
  return bits.join(" · ");
}

/* ------------------------------------------------------------ state */
let draft=null, meta={}, dirty=false, pollTimer=null;
const clone=o=>JSON.parse(JSON.stringify(o));
function status(t){ $("srStatus").textContent=t||""; }
function setDirty(v){ dirty=v; status(v?"Not saved yet":""); }

const tabs=dlgTabs(dlg.querySelector(".dlg-tabs"));
async function open(tab){
  status(""); $("srImportBox").classList.add("hide");
  $("srList").innerHTML='<p class="muted sr-empty">Loading your searches\u2026</p>';
  tabs.show(tab||"srTabList");
  if(!dlg.open) dlg.showModal();
  await load(true);
  fillBuilder();
}
async function load(replace){
  let d; try{ d=await fetch("/searches/list",{cache:"no-store"}).then(r=>r.json()); }
  catch(e){ $("srList").innerHTML=""; extNote(null); status("The pipeline is not answering, so the searches cannot be loaded."); return; }
  meta=d;
  extNote(d.extension);
  if(replace||!dirty){ draft={pages:d.pages,searches:clone(d.searches||[])}; setDirty(false); render(); }
  clearTimeout(pollTimer);
  // Something added from the extension while this is open shows up, as long as
  // there is nothing unsaved here to overwrite.
  if(dlg.open) pollTimer=setTimeout(()=>load(false),5000);
}
function ago(s){ if(s==null) return ""; if(s<90) return "just now";
  const m=Math.round(s/60); if(m<90) return m+" min ago";
  const h=Math.round(m/60); return h<48?h+" h ago":Math.round(h/24)+" days ago"; }
const INSTALL="In Chrome, open chrome://extensions, switch on Developer mode, press Load unpacked and pick the extension folder inside the project.";
function extNote(x){
  const n=$("srExt"), pill=$("srExtPill");
  if(!x){ n.classList.add("hide"); pill.classList.add("hide"); return; }
  pill.classList.remove("hide");
  pill.className="ext-pill"+(x.connected?" ok":" warn");
  pill.textContent=x.connected?"Extension connected":"Extension not connected";
  pill.title=x.seen_at?("last checked in "+ago(x.age_s)):"never checked in";
  if(x.connected){ n.classList.add("hide"); return; }
  n.classList.remove("hide");
  const text=x.seen_at
    ? "The Chrome extension last checked in "+ago(x.age_s)+", so these searches wait until Chrome is open with it loaded."
    : "No Chrome extension has connected yet, and these searches only run through it.";
  if(n.dataset.text===text) return;          // keep the steps open across polls
  n.dataset.text=text;
  n.innerHTML=esc(text)+' <button type="button" class="linkish" aria-expanded="false">How to install it</button>'+
    '<span class="ext-steps" hidden><br>'+esc(INSTALL)+'</span>';
  const b=n.querySelector("button"), st=n.querySelector(".ext-steps");
  b.onclick=()=>{ st.hidden=!st.hidden; b.setAttribute("aria-expanded",String(!st.hidden)); };
}

/* ------------------------------------------------------------ the list */
const ICON=(name,label)=>'<span class="ico i-'+name+'" aria-hidden="true"></span><span class="sr-only">'+label+'</span>';
function render(flash){
  const box=$("srList");
  const pages=$("srPages");
  if(!pages.dataset.done){ pages.innerHTML=(meta.pages_choices||[1,2,3]).map(n=>'<option value="'+n+'">'+n+'</option>').join(""); pages.dataset.done="1"; }
  pages.value=String(draft.pages||1);
  if(!draft.searches.length){
    box.innerHTML='<div class="sr-empty"><p>No saved searches yet.</p>'+
      '<button type="button" class="go sm" data-go-add>Add a search</button></div>';
    box.querySelector("[data-go-add]").onclick=()=>tabs.show("srTabAdd");
  } else box.innerHTML=draft.searches.map((q,i)=>{
    const site=siteOf(q.url), on=q.enabled!==false, name=esc(q.label);
    return '<div class="sr-row'+(on?"":" off")+(flash===i?" flash":"")+'" data-i="'+i+'">'+
      '<span class="sr-site s-'+esc((site||"x").toLowerCase())+'">'+esc(site||"?")+'</span>'+
      '<div class="sr-main"><div class="sr-name">'+name+'</div><div class="sr-sum">'+esc(describe(q.url))+'</div></div>'+
      '<span class="switch" title="'+(on?"On: runs with Run scrape":"Off: skipped, not deleted")+'">'+
        '<input type="checkbox" class="sr-on" aria-label="Run '+name+'"'+(on?" checked":"")+'><i aria-hidden="true"></i></span>'+
      '<div class="sr-tools">'+
        '<button type="button" class="icon-btn sr-edit" aria-expanded="false" title="Rename or change the address">'+ICON("pencil","Edit "+name)+'</button>'+
        '<a class="icon-btn" href="'+esc(q.url)+'" target="_blank" rel="noopener" title="Open on '+esc(site)+'">'+ICON("external","Open "+name)+'</a>'+
        '<button type="button" class="icon-btn danger sr-del" title="Remove">'+ICON("trash","Remove "+name)+'</button>'+
      '</div>'+
      '<div class="sr-editor" hidden>'+
        '<label>Name<input class="sr-label" type="text" maxlength="120" value="'+name+'"></label>'+
        '<label>Address<input class="sr-url" type="url" value="'+esc(q.url)+'"></label>'+
        '<button type="button" class="go sm sr-done">Done</button>'+
      '</div>'+
    '</div>';
  }).join("");
  box.querySelectorAll(".sr-row").forEach(el=>{
    const i=+el.dataset.i, q=draft.searches[i];
    const ed=el.querySelector(".sr-editor"), eb=el.querySelector(".sr-edit");
    const close=()=>{ ed.hidden=true; eb.setAttribute("aria-expanded","false"); };
    eb.onclick=()=>{ ed.hidden=!ed.hidden; eb.setAttribute("aria-expanded",String(!ed.hidden));
      if(!ed.hidden) el.querySelector(".sr-label").focus(); };
    el.querySelector(".sr-done").onclick=()=>{ close(); eb.focus(); };
    el.querySelector(".sr-label").oninput=e=>{ q.label=e.target.value; el.querySelector(".sr-name").textContent=q.label; setDirty(true); };
    const url=el.querySelector(".sr-url");
    url.onchange=()=>{ const v=url.value.trim();
      if(!siteOf(v)){ status("That address is not a LinkedIn, Indeed or StepStone page."); url.value=q.url; return; }
      q.url=v; el.querySelector(".sr-sum").textContent=describe(v);
      el.querySelector("a").href=v; el.querySelector(".sr-site").textContent=siteOf(v); setDirty(true); };
    el.querySelector(".sr-on").onchange=e=>{ q.enabled=e.target.checked; el.classList.toggle("off",!q.enabled); setDirty(true); counts(); };
    el.querySelector(".sr-del").onclick=()=>{ draft.searches.splice(i,1); render(); setDirty(true);
      status("Removed “"+q.label+"”. Save to keep the change, or close without saving to undo."); };
  });
  if(flash!=null){ const f=box.querySelector(".flash"); if(f) f.scrollIntoView({block:"nearest"}); }
  counts();
}
function counts(){
  const on=draft.searches.filter(q=>q.enabled!==false).length;
  $("srCounts").textContent=draft.searches.length?(on+" of "+draft.searches.length+" on"):"";
  $("srTabN").textContent=draft.searches.length||"";
}
function addToDraft(url,label){
  url=(url||"").trim();
  if(!/^https:\/\//i.test(url)){ status("A search address starts with https://"); return false; }
  if(!siteOf(url)){ status("That is not a LinkedIn, Indeed or StepStone page, so the extension cannot collect it."); return false; }
  const k=keyOf(url), hit=draft.searches.find(q=>keyOf(q.url)===k);
  if(hit){ status("Already in the list as “"+hit.label+"”."); return false; }
  let kw=""; try{ kw=kwOf(new URL(url)); }catch(e){}
  draft.searches.push({label:label||(siteOf(url)+": "+(kw||"search")),url,enabled:true});
  tabs.show("srTabList");
  render(draft.searches.length-1); setDirty(true);
  status("Added. Save to keep it.");
  return true;
}
dlg.querySelectorAll('input[name="srHow"]').forEach(r=>r.onchange=()=>{
  const paste=r.value==="paste"&&r.checked;
  $("srBuildBox").hidden=paste; $("srPasteBox").hidden=!paste;
  (paste?$("srPaste"):$("srKw")).focus();
});
$("srPages").onchange=e=>{ draft.pages=+e.target.value; setDirty(true); };

/* ------------------------------------------------------------ builder */
function fillBuilder(){
  const sel=$("srIndeed");
  if(!sel.dataset.done){ sel.innerHTML=INDEED.map(c=>'<option value="'+c[0]+'">'+esc(c[1])+'</option>').join(""); sel.dataset.done="1"; }
  built();
}
function form(){
  return {kw:$("srKw").value.trim(),loc:$("srLoc").value.trim(),site:$("srSite").value,
    indeed:$("srIndeed").value,radius:+$("srRadius").value,posted:+$("srPosted").value,remote:$("srRemote").checked};
}
function built(){
  const f=form();
  $("srIndeedWrap").classList.toggle("hide",f.site!=="indeed");
  const geoMap=Object.assign({},KNOWN_GEO,meta.geo||{});
  const geo=f.site==="linkedin"&&f.loc?geoMap[f.loc.toLowerCase().replace(/\s+/g," ")]:"";
  $("srGeoHint").textContent=(f.site==="linkedin"&&f.loc&&!geo)
    ? "LinkedIn may read “"+f.loc+"” loosely and fall back to the country on your profile. Open the search once (Open on the site) with the extension loaded and the place is remembered for next time; or set the place on LinkedIn and paste that address below."
    : "";
  const url=f.kw?BUILD[f.site](f,geo):"";
  $("srBuilt").textContent=url||"Type a job title to build the link";
  $("srAddBuilt").disabled=!url;
  const a=$("srOpenBuilt");
  if(url){ a.href=url; a.removeAttribute("aria-disabled"); a.classList.remove("disabled"); }
  else { a.removeAttribute("href"); a.setAttribute("aria-disabled","true"); a.classList.add("disabled"); }
  return url;
}
["srKw","srLoc","srSite","srIndeed","srRadius","srPosted","srRemote"].forEach(id=>{
  $(id).addEventListener("input",built); $(id).addEventListener("change",built); });
$("srAddBuilt").onclick=()=>{ const url=built(); if(!url) return;
  const f=form(), site=siteOf(url);
  if(addToDraft(url,site+": "+f.kw+(f.loc?" · "+f.loc:""))) $("srKw").value="", built(); };
$("srAddPaste").onclick=()=>{ if(addToDraft($("srPaste").value)) $("srPaste").value=""; };
$("srPaste").addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); $("srAddPaste").click(); } });

/* ------------------------------------------------------------ share
 * A good set of searches is the most useful thing a user of this project
 * builds, so it can be handed to someone else as plain JSON. */
$("srExport").onclick=async()=>{
  const text=JSON.stringify(draft.searches.map(({label,url,enabled})=>({label,url,enabled:enabled!==false})),null,2);
  try{ await navigator.clipboard.writeText(text); status("Copied "+draft.searches.length+" searches. Paste them anywhere to share."); }
  catch(e){ $("srImportBox").classList.remove("hide"); $("srImport").value=text; $("srImport").select(); status("Could not reach the clipboard; the list is in the box below to copy by hand."); }
};
$("srImportBtn").onclick=()=>{ $("srImportBox").classList.toggle("hide"); $("srImport").focus(); };
$("srImportGo").onclick=()=>{
  let rows; try{ rows=JSON.parse($("srImport").value); }catch(e){ status("That is not a copied list (not valid JSON)."); return; }
  if(!Array.isArray(rows)){ status("That is not a copied list (expected a list of searches)."); return; }
  const clean=[], seen=new Set();
  rows.forEach(r=>{ if(!r||typeof r.url!=="string"||!/^https:\/\//i.test(r.url)||!siteOf(r.url)) return;
    const k=keyOf(r.url); if(seen.has(k)) return; seen.add(k);
    clean.push({label:String(r.label||r.url).slice(0,120),url:r.url,enabled:r.enabled!==false}); });
  if(!clean.length){ status("No LinkedIn, Indeed or StepStone searches in that list."); return; }
  if(!confirm("Replace the "+draft.searches.length+" searches here with the "+clean.length+" in the pasted list?")) return;
  draft.searches=clean; tabs.show("srTabList"); render(); setDirty(true);
  $("srImportBox").classList.add("hide"); $("srImport").value="";
  status("Replaced with "+clean.length+" searches. Save to keep them.");
};

/* ------------------------------------------------------------ save */
async function save(){
  $("srSave").disabled=$("srSaveRun").disabled=true;
  const r=await post("/searches/save",{searches:draft.searches,pages:draft.pages});
  $("srSave").disabled=$("srSaveRun").disabled=false;
  if(!r||r.ok===false){ status("Not saved: "+((r&&r.why)||"the pipeline did not answer")); return false; }
  meta=r; draft={pages:r.pages,searches:clone(r.searches)}; render(); setDirty(false);
  extNote(r.extension);
  toast("Saved searches updated");
  if(typeof tick==="function") tick();
  return true;
}
$("srSave").onclick=save;
$("srSaveRun").onclick=async()=>{
  if(dirty && !await save()) return;
  const r=await post("/trigger",{});
  if(!r||r.ok===false){ status("Saved, but not started: "+((r&&r.why)||"the pipeline did not answer")); return; }
  dlg.close(); clearTimeout(pollTimer);
  scrapeQueued(r);
};
function tryClose(){
  if(dirty && !confirm("Close without saving your changes?")) return false;
  clearTimeout(pollTimer); dlg.close(); return true;
}
$("srClose").onclick=tryClose;
dlg.addEventListener("cancel",e=>{ e.preventDefault(); tryClose(); });
dlg.addEventListener("click",e=>{ if(e.target===dlg) tryClose(); });   // the backdrop
$("btnSearches").onclick=()=>open();
// The extension's "Manage on the dashboard" opens /dashboard#searches.
if(location.hash==="#searches"){ history.replaceState(null,"",location.pathname); open(); }

/* ------------------------------------------------------------ Run scrape
 * Said here, not only in the toast's stock line: a run queued with no
 * extension listening would otherwise sit there looking like it started. */
function scrapeQueued(r){
  const x=r.extension||{}, n=r.searches;
  if(x.connected) toast("Scrape queued: "+n+" search"+(n===1?"":"es")+". The extension starts within a minute.");
  else toast((x.seen_at?"Queued, but the Chrome extension last checked in "+ago(x.age_s)+". ":"Queued, but no Chrome extension has connected yet. ")+
    "It starts when Chrome is open with the extension loaded.",false);
  if(typeof tick==="function") tick();
  if(typeof loadEvents==="function") loadEvents();
}
window.scrapeQueued=scrapeQueued;
window.openSearches=()=>open();

/* ------------------------------------------------------------ Start over */
async function erase(hard){
  const text=hard
    ? "Start completely fresh?\n\nErases the collected jobs and forgets that they were seen, so the next scrape collects them again.\n\n"+
      "Every built CV and cover letter moves to applications/archive, so the Applications page starts empty. Nothing is deleted, and already-built jobs still never come back into a batch.\n\n"+
      "Your applied list, your 30-day job history (jobs from earlier days stay skipped) and your saved searches are NOT touched."
    : "Erase the collected jobs?\n\nThe pipeline still remembers them as seen. Your applied list, built CVs and saved searches are not touched.";
  if(!confirm(text)) return;
  const b=$(hard?"btnEraseAll":"btnEraseJobs"); b.disabled=true;
  const r=await post("/reset",{hard});
  b.disabled=false;
  if(!r||r.ok===false){ toast("Could not erase: "+((r&&(r.why||r.error))||"the pipeline did not answer"),false); return; }
  toast("Erased: "+(r.archived||0)+" file(s) of jobs archived"+
    (r.build_archived?", "+r.build_archived+" day(s) of CVs moved to applications/archive":"")+
    ". The extension clears its own copy within a minute.");
  if(typeof tick==="function") tick();
  if(typeof loadEvents==="function") loadEvents();
}
$("btnEraseAll").onclick=()=>erase(true);
$("btnEraseJobs").onclick=()=>erase(false);
})();
