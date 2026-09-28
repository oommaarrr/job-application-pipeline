/*
 * The Arbeitnow settings dialog (the gear next to "+ Arbeitnow").
 *
 * Arbeitnow's feed cannot be searched, so the pipeline reads the whole week
 * and keeps jobs close in meaning to these searches (scraper/arbeitnow_match.py).
 * This page edits the searches and rules and shows, live, what a pull would
 * keep. Uses $, esc, post, toast, tick and loadEvents from dashboard.js.
 */
(function(){
const dlg=$("anDlg"); if(!dlg) return;
const LEVEL_TEXT={wide:"Wide",balanced:"Balanced",close:"Close"};
const LEVEL_HELP={wide:"more jobs, some loosely related",balanced:"the default",close:"only the clearest matches"};
let state=null;          // the form, in the shape the bridge saves
let meta={};             // countries, levels and the rest from GET /arbeitnow/settings
let dirty=false, prevTimer=null, prevSeq=0, pollTimer=null, lastPreview=null;

function blank(){ return {searches:[{name:"",looking_for:"",on:true}],strictness:"balanced",
  countries:[],remote_elsewhere:true,student_roles:false,days:7}; }
function clone(o){ return JSON.parse(JSON.stringify(o)); }
function countryName(c){ const f=(meta.countries||[]).find(x=>x[0]===c); return f?f[1]:c; }
function note(text,kind){ const n=$("anNote"); n.className="an-note"+(kind?" "+kind:"");
  n.textContent=text||""; n.classList.toggle("hide",!text); }
function status(t){ $("anStatus").textContent=t||""; }
function usable(){ return state && state.searches.some(s=>s.on&&(s.looking_for||s.name).trim()); }
function setDirty(v){ dirty=v; status(v?"Not saved yet":""); syncButtons(); }
function syncButtons(){ const ok=usable(); $("anSave").disabled=!ok; $("anSavePull").disabled=!ok; }

/* ------------------------------------------------------------------ open */
// The footer count links to the Preview tab, so it hides while that is open.
const tabs=dlgTabs(dlg.querySelector(".dlg-tabs"),id=>$("anFootCount").classList.toggle("hide",id==="anTabP"));
$("anHowBtn").onclick=()=>{ const h=$("anHow"), show=h.classList.toggle("hide")===false;
  $("anHowBtn").setAttribute("aria-expanded",String(show)); };
$("anFootCount").onclick=()=>tabs.show("anTabP");
function foot(text){ $("anFootCount").textContent=text||""; }
async function open(){
  note(""); status(""); lastPreview=null;
  $("anExamples").innerHTML=""; $("anNear").innerHTML=""; $("anNearWrap").classList.add("hide");
  $("anSummary").textContent=""; $("anCount").textContent="Preview"; $("anFeed").textContent="";
  foot(""); $("anTabN").textContent=""; tabs.show("anTabS");
  $("anSearches").innerHTML='<p class="muted">Loading your searches\u2026</p>';
  if(!dlg.open) dlg.showModal();
  await load(true);
}
async function load(first){
  let d; try{ d=await fetch("/arbeitnow/settings",{cache:"no-store"}).then(r=>r.json()); }
  catch(e){ note("The pipeline is not answering, so the settings cannot be loaded.","bad"); return; }
  meta=d;
  fillStatic();
  if(first || !state){
    if(d.settings){ state=clone(d.settings); if(!state.searches.length) state.searches=blank().searches; }
    else state=blank();
    setDirty(false); render(); schedulePreview(0);
  }
  explain(d);
  // Still being written from the profile (first open): check back until it lands.
  clearTimeout(pollTimer);
  if(d.suggesting && dlg.open) pollTimer=setTimeout(()=>waitForModel(first&&!d.settings),2000);
}
function explain(d){
  const s=d.settings;
  if(d.suggesting){ note("The local model is reading your profile to suggest searches. This takes about half a minute."); return; }
  if(d.suggest_error && !s){ note("The local model could not suggest searches ("+d.suggest_error+"). Describe what you are looking for below, or start Ollama and press Fill from my profile.","warn"); return; }
  if(!s){ note(d.has_profile?"":"There is no profile yet, so nothing can be suggested. Describe the jobs you want below, or make your profile first and press Fill from my profile.","warn"); return; }
  if(d.profile_changed){ note("Your profile has changed since these were saved. Press Fill from my profile to have the local model suggest them again.","warn"); return; }
  if(s.made_by==="model") note("Written by the local model from your profile ("+(s.profile||"profile")+"). Change anything you like; once you save, they are yours and are never rewritten.");
  else note("");
}
async function waitForModel(replaceForm){
  let d; try{ d=await fetch("/arbeitnow/settings",{cache:"no-store"}).then(r=>r.json()); }catch(e){ return; }
  meta=d;
  if(d.suggesting){ explain(d); pollTimer=setTimeout(()=>waitForModel(replaceForm),2000); return; }
  if(d.suggestion){ state=clone(d.suggestion); render(); setDirty(true);
    note("Suggested by the local model from your profile. Check it, change anything, then Save."); schedulePreview(0); return; }
  if(replaceForm && d.settings && !dirty){ state=clone(d.settings); render(); setDirty(false); schedulePreview(0); }
  explain(d);
  if(d.suggest_error) note("The local model could not suggest searches: "+d.suggest_error,"warn");
}

/* ---------------------------------------------------------------- render */
function fillStatic(){
  const dl=$("anCountryList");
  if(!dl.dataset.done && meta.countries){ dl.innerHTML=meta.countries.map(c=>'<option value="'+esc(c[1])+'">').join(""); dl.dataset.done="1"; }
  const ds=$("anDays");
  if(!ds.dataset.done && meta.days){ ds.innerHTML=meta.days.map(n=>'<option value="'+n+'">'+n+'</option>').join(""); ds.dataset.done="1"; }
  const lv=$("anLevel");
  if(!lv.dataset.done && meta.levels){
    lv.innerHTML=meta.levels.map(l=>'<label title="'+esc(LEVEL_HELP[l]||"")+'"><input type="radio" name="anLevel" value="'+l+'">'+
      '<span>'+esc(LEVEL_TEXT[l]||l)+'</span><small data-level="'+l+'">&nbsp;</small></label>').join("");
    lv.querySelectorAll("input").forEach(i=>i.onchange=()=>{ state.strictness=i.value; setDirty(true); schedulePreview(0); });
    lv.dataset.done="1";
  }
}
function render(){
  const box=$("anSearches");
  box.innerHTML=state.searches.map((s,i)=>
    '<div class="an-search'+(s.on?"":" off")+'" data-i="'+i+'">'+
      '<div class="an-search-top">'+
        '<input class="an-name" type="text" maxlength="60" placeholder="Job title, e.g. Product Designer" aria-label="Search '+(i+1)+' name" value="'+esc(s.name)+'">'+
        '<span class="toggle" title="'+(s.on?"On":"Off: kept, not used")+'"><input type="checkbox" class="an-on" aria-label="Use search '+(i+1)+'"'+(s.on?" checked":"")+'><i aria-hidden="true"></i></span>'+
        '<button type="button" class="icon-btn danger an-del" title="Remove"><span class="ico i-trash" aria-hidden="true"></span><span class="sr-only">Remove search '+(i+1)+'</span></button>'+
      '</div>'+
      '<textarea class="an-text" maxlength="400" rows="2" aria-label="Search '+(i+1)+': what the job is" '+
        'placeholder="The role, what the work is, the field and the main tools. For example: Designer for a B2B SaaS product, owning user research, flows and UI in Figma.">'+esc(s.looking_for)+'</textarea>'+
      '<div class="an-search-foot" data-count="'+i+'"></div>'+
    '</div>').join("");
  box.querySelectorAll(".an-search").forEach(el=>{
    const i=+el.dataset.i, s=state.searches[i];
    el.querySelector(".an-name").oninput=e=>{ s.name=e.target.value; setDirty(true); schedulePreview(); };
    const ta=el.querySelector(".an-text");
    fit(ta);
    ta.oninput=e=>{ s.looking_for=e.target.value; fit(ta); setDirty(true); schedulePreview(); };
    el.querySelector(".an-on").onchange=e=>{ s.on=e.target.checked; el.classList.toggle("off",!s.on); setDirty(true); schedulePreview(0); };
    el.querySelector(".an-del").onclick=()=>{ state.searches.splice(i,1);
      if(!state.searches.length) state.searches=blank().searches; render(); setDirty(true); schedulePreview(0); };
  });
  $("anAdd").disabled=state.searches.length>=(meta.max_searches||8);
  renderCountries();
  $("anRemote").checked=!!state.remote_elsewhere;
  $("anRemote").disabled=!state.countries.length;
  $("anStudent").checked=!!state.student_roles;
  $("anDays").value=String(state.days);
  document.querySelectorAll('#anLevel input').forEach(i=>i.checked=(i.value===state.strictness));
  syncButtons();
}
// A description box as tall as its text, so a long one is never cut off on a
// narrow screen.
function fit(ta){ ta.style.height="auto"; ta.style.height=(ta.scrollHeight+2)+"px"; }
function renderCountries(){
  const box=$("anCountries");
  box.innerHTML=state.countries.length
    ? state.countries.map(c=>'<span class="chip">'+esc(countryName(c))+
        '<button type="button" data-c="'+esc(c)+'" aria-label="Remove '+esc(countryName(c))+'">&#10005;</button></span>').join("")
    : '<span class="muted">Anywhere</span>';
  box.querySelectorAll("button[data-c]").forEach(b=>b.onclick=()=>{
    state.countries=state.countries.filter(c=>c!==b.dataset.c); render(); setDirty(true); schedulePreview(0); });
}
function addCountry(){
  const inp=$("anCountryIn"), v=inp.value.trim(); if(!v) return;
  const low=v.toLowerCase();
  const hit=(meta.countries||[]).find(c=>c[1].toLowerCase()===low||c[0].toLowerCase()===low);
  if(!hit){ status("“"+v+"” is not a country in the list. Pick one from the suggestions."); return; }
  if(!state.countries.includes(hit[0])) state.countries.push(hit[0]);
  inp.value=""; render(); setDirty(true); schedulePreview(0);
}
$("anCountryIn").addEventListener("change",addCountry);
$("anCountryIn").addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); addCountry(); } });
$("anRemote").onchange=e=>{ state.remote_elsewhere=e.target.checked; setDirty(true); schedulePreview(0); };
$("anStudent").onchange=e=>{ state.student_roles=e.target.checked; setDirty(true); schedulePreview(0); };
$("anDays").onchange=e=>{ state.days=+e.target.value; setDirty(true); schedulePreview(0); };
$("anAdd").onclick=()=>{ if(state.searches.length>=(meta.max_searches||8)) return;
  state.searches.push({name:"",looking_for:"",on:true}); render(); setDirty(true);
  const all=$("anSearches").querySelectorAll(".an-name"); all[all.length-1].focus(); };

/* --------------------------------------------------------------- preview */
function schedulePreview(ms){ clearTimeout(prevTimer); prevTimer=setTimeout(preview, ms==null?900:ms); }
async function preview(){
  if(!dlg.open || !state) return;
  if(!usable()){ foot(""); $("anTabN").textContent=""; $("anCount").textContent="Preview"; $("anSummary").textContent="Describe at least one search to see what it would keep.";
    $("anExamples").innerHTML=""; $("anNearWrap").classList.add("hide"); return; }
  const seq=++prevSeq;
  $("anFeed").textContent="updating…";
  const r=await post("/arbeitnow/preview",{settings:state});
  if(seq!==prevSeq || !dlg.open) return;          // a newer preview is on its way
  if(!r || r.ok===false){ $("anFeed").textContent=""; $("anSummary").textContent="No preview: "+((r&&r.why)||"the pipeline did not answer"); return; }
  if(r.preparing){
    const p=r.preparing;
    $("anCount").textContent="Getting this week’s jobs";
    foot("Getting this week’s jobs\u2026"); $("anTabN").textContent="";
    $("anFeed").textContent="";
    $("anSummary").textContent=(p.stage?p.stage.charAt(0).toUpperCase()+p.stage.slice(1):"Starting")+
      (p.total?(": "+p.done+" of "+p.total):"")+". The first time takes a few minutes; after that it is quick.";
    prevTimer=setTimeout(preview,2000); return;
  }
  lastPreview=r.preview;
  if(r.prep_error) status("The jobs could not be refreshed ("+r.prep_error+"); the preview uses the last download.");
  renderPreview(lastPreview);
}
function renderPreview(p){
  if(!p) return;
  document.querySelectorAll("#anLevel small[data-level]").forEach(el=>{
    const n=(p.levels||{})[el.dataset.level]; el.textContent=(n==null?" ":n+" jobs"); });
  const lvl=state.strictness, kept=(p.levels||{})[lvl]!=null?p.levels[lvl]:p.kept;
  $("anCount").textContent=kept.toLocaleString()+" job"+(kept===1?"":"s")+" would be kept";
  foot(kept.toLocaleString()+" job"+(kept===1?"":"s")+" would be kept");
  $("anTabN").textContent=kept.toLocaleString();
  const d=p.dropped||{};
  const parts=[];
  if(d.location) parts.push(d.location.toLocaleString()+" in other places");
  if(d.student) parts.push(d.student.toLocaleString()+" internships or working-student jobs");
  if(d.old) parts.push(d.old.toLocaleString()+" older than "+state.days+" day"+(state.days===1?"":"s"));
  const n=x=>Number(x||0).toLocaleString();
  $("anSummary").textContent="Of "+n(p.total)+" jobs this week, "+n(p.eligible)+" pass the rules"+
    (parts.length?(" ("+parts.join(", ")+" left out)"):"")+"."+
    (p.unscored?(" "+p.unscored+" are not read yet and wait for the next pull."):"");
  $("anFeed").textContent=p.feed_at?("jobs from "+new Date(p.feed_at).toLocaleString([], {weekday:"short",hour:"2-digit",minute:"2-digit"})):"";
  const row=j=>'<li><span class="t">'+esc(j.title)+'</span><span class="s">'+esc(j.search)+'</span>'+
    '<span class="m">'+esc([j.company,j.location].filter(Boolean).join(" · "))+'</span></li>';
  $("anExamples").innerHTML=(p.examples||[]).map(row).join("")||'<li class="muted">Nothing on Arbeitnow this week is close enough. Try Wide, or describe the work in other words.</li>';
  $("anNear").innerHTML=(p.near||[]).map(j=>row({...j,location:""})).join("");
  $("anNearWrap").classList.toggle("hide",!(p.near||[]).length);
  // The bridge drops cards with neither a name nor a description before it
  // counts, so its positions skip those; map them back to the cards here.
  const kept_by=[]; let k=0;
  state.searches.forEach(s=>{ kept_by.push((s.name||"").trim()||(s.looking_for||"").trim()?(p.per_index||[])[k++]:null); });
  document.querySelectorAll("#anSearches [data-count]").forEach(el=>{
    const i=+el.dataset.count, s=state.searches[i], n=kept_by[i];
    el.textContent=(s&&s.on&&n!=null)?(n+" kept by this search"):"";
  });
}

/* ------------------------------------------------------------- actions */
async function save(){
  if(!usable()){ status("Describe at least one search first."); return false; }
  $("anSave").disabled=true;
  const r=await post("/arbeitnow/settings",{settings:state});
  syncButtons();
  if(!r || r.ok===false){ status("Could not save: "+((r&&r.why)||"the pipeline did not answer")); return false; }
  state=clone(r.settings); render(); setDirty(false);
  meta.settings=r.settings; explain(meta);
  toast("Arbeitnow searches saved");
  return true;
}
$("anSave").onclick=save;
$("anSavePull").onclick=async()=>{
  if(!await save()) return;
  const r=await post("/arbeitnow",{});
  if(r && r.ok!==false){ dlg.close(); toast("Arbeitnow pulling — its jobs join the pool in a few minutes."); }
  else status("Saved, but the pull did not start: "+((r&&r.why)||"the pipeline did not answer"));
  if(typeof tick==="function") tick();
  if(typeof loadEvents==="function") loadEvents();
};
$("anFill").onclick=async()=>{
  if(dirty && !confirm("Replace what is in the form with the local model’s suggestion?")) return;
  const r=await post("/arbeitnow/suggest",{});
  if(!r || r.ok===false){ note("Could not ask the local model: "+((r&&r.why)||"the pipeline did not answer"),"bad"); return; }
  note("The local model is reading your profile to suggest searches. This takes about half a minute.");
  clearTimeout(pollTimer); pollTimer=setTimeout(()=>waitForModel(false),2000);
};
function tryClose(){
  if(dirty && !confirm("Close without saving your changes?")) return false;
  clearTimeout(pollTimer); clearTimeout(prevTimer); dlg.close(); return true;
}
$("anClose").onclick=tryClose;
dlg.addEventListener("cancel",e=>{ e.preventDefault(); tryClose(); });
dlg.addEventListener("click",e=>{ if(e.target===dlg) tryClose(); });   // the backdrop
$("btnAnSettings").onclick=open;
// The extension's "Set them on the dashboard" link opens /dashboard#arbeitnow.
if(location.hash==="#arbeitnow"){ history.replaceState(null,"",location.pathname); open(); }
})();
