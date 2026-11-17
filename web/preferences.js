/*
 * Job preferences (Preferences in the top bar).
 *
 * What the person is looking for, as the settings the rules read: roles,
 * years, places, languages, and how their CV looks (scraper/preferences.py).
 * Claude writes them from the profile; anything changed here is the person's
 * and stays as they set it when the profile changes. Also draws the banner
 * that asks the person to check them after Claude wrote or changed them.
 * Uses $, esc, post, toast, dlgTabs and loadEvents from dashboard.js.
 */
(function(){
const dlg=$("pfDlg"); if(!dlg) return;
const LEVEL={a1:"A1",a2:"A2",b1:"B1",b2:"B2",c1:"C1",c2:"C2",native:"Native"};
const WORK={onsite:"On site",hybrid:"Hybrid",remote:"Remote"};
const FIELD={target_roles:"roles",not_my_field:"not your field",years_experience:"years",
  max_years_asked:"years limit",languages:"languages",places:"places",remote_anywhere:"remote",relocate:"relocating",
  accept_management:"management roles",job_kinds:"kinds of job",cv_shape:"CV style",cv_max_pages:"CV length",cv_state_years:"years on the CV"};
// Which tab each field is on, so a question can take the person to it.
const TAB_OF={places:"pfTabW",remote_anywhere:"pfTabW",relocate:"pfTabW",languages:"pfTabW",
  cv_shape:"pfTabC",cv_max_pages:"pfTabC",cv_state_years:"pfTabC"};
const FOCUS_OF={target_roles:"pfRoleIn",not_my_field:"pfNotIn",years_experience:"pfYears",
  max_years_asked:"pfMaxYears",accept_management:"pfMgmt",job_kinds:"pfKinds",places:"pfAddPlace",
  remote_anywhere:"pfRemote",relocate:"pfRelocate",languages:"pfAddLang",cv_shape:"pfShape",
  cv_max_pages:"pfPages",cv_state_years:"pfStateYears"};
let st=null;             // the form, in the shape the bridge saves
let meta={};             // the last GET /preferences
let dirty=false, pollTimer=null;

function clone(o){ return JSON.parse(JSON.stringify(o)); }
function note(text,kind){ const n=$("pfNote"); n.className="an-note"+(kind?" "+kind:"");
  n.textContent=text||""; n.classList.toggle("hide",!text); }
function status(t){ $("pfStatus").textContent=t||""; }
function setDirty(v){ dirty=v; status(v?"Not saved yet":""); }
function countryName(c){ const f=((meta.choices||{}).countries||[]).find(x=>x[0]===c); return f?f[1]:(c||""); }
function countryCode(t){ const v=String(t||"").trim().toLowerCase(); if(!v) return "";
  const f=((meta.choices||{}).countries||[]).find(x=>x[1].toLowerCase()===v||x[0].toLowerCase()===v);
  return f?f[0]:null; }
function model(m){ return String(m||"").startsWith("local:")?"the local model":"Claude"; }
function day(iso){ try{ return new Date(iso).toLocaleDateString([], {day:"numeric",month:"short"}); }catch(e){ return ""; } }

/* ------------------------------------------------------------------ open */
const tabs=dlgTabs(dlg.querySelector(".dlg-tabs"));
async function open(){
  note(""); status(""); tabs.show("pfTabR");
  if(!dlg.open) dlg.showModal();
  await load(true);
}
async function load(replace){
  let d; try{ d=await fetch("/preferences",{cache:"no-store"}).then(r=>r.json()); }
  catch(e){ note("The pipeline is not answering, so your preferences cannot be loaded.","bad"); return; }
  meta=d;
  fillStatic();
  if(replace || !st){ st=clone(d.preferences); setDirty(false); render(); }
  explain(d);
  clearTimeout(pollTimer);
  if(d.state==="writing" && dlg.open) pollTimer=setTimeout(()=>load(!dirty),2000);
}
function questions(d){
  const box=$("pfAsk"), qs=((d.meta||{}).questions)||[];
  box.classList.toggle("hide",!qs.length || !!d.example);
  if(!qs.length){ box.innerHTML=""; return; }
  box.innerHTML="<b>"+model((d.meta||{}).model)+" has "+(qs.length===1?"a question":qs.length+" questions")+
    " your profile leaves open.</b> Answer "+(qs.length===1?"it":"them")+" below, then Save.<ul>"+
    qs.map((q,i)=>"<li>"+esc(q.question)+(q.field?' <button type="button" class="go quiet sm" data-q="'+i+'">Show</button>':"")+"</li>").join("")+"</ul>";
  box.querySelectorAll("button[data-q]").forEach(b=>b.onclick=()=>{
    const f=qs[+b.dataset.q].field; tabs.show(TAB_OF[f]||"pfTabR");
    const el=$(FOCUS_OF[f]); if(!el) return;
    el.scrollIntoView({block:"center"});
    const t=el.matches("input,button")?el:el.querySelector("input,button"); if(t) t.focus();
  });
}
function explain(d){
  questions(d);
  const off=d.example||!d.has_profile;
  $("pfSave").disabled=!!off; $("pfFill").disabled=!!off||d.state==="writing";
  if(d.example){ note("The active profile is the fictional example, so these are its preferences and cannot be changed. Make your own profile first (Edit profile, or /make-profile with your CV).","warn"); return; }
  if(!d.has_profile){ note("There is no profile yet. Make one first (/make-profile with your CV), and Claude fills these in from it.","warn"); return; }
  if(d.state==="writing"){ note("Claude is reading your profile to fill these in. This takes under a minute."); return; }
  if(d.error && !d.exists){ note("Your preferences could not be filled in from your profile: "+d.error+(d.fix?". "+d.fix:"")+". You can also fill them in yourself and Save.","warn"); return; }
  if(d.error && d.stale){ note("Your profile changed, but these could not be updated from it: "+d.error+(d.fix?". "+d.fix:"")+". Jobs are judged against these until then; you can also change them yourself and Save.","warn"); return; }
  if(!d.exists){ note("Nothing set yet. Fill these in, or press Fill from my profile."); return; }
  const m=d.meta||{};
  if(!m.reviewed && (m.changed||[]).length)
    note(model(m.model)+" updated these from your profile on "+day(m.made_at)+": "+m.changed.map(f=>FIELD[f]||f).join(", ")+". Check them and Save.");
  else if(!m.reviewed)
    note(model(m.model)+" filled these in from your profile on "+day(m.made_at)+". Check them and Save.");
  else note("");
}

/* ---------------------------------------------------------------- render */
function fillStatic(){
  const dl=$("pfCountryList"), c=(meta.choices||{}).countries;
  if(!dl.dataset.done && c){ dl.innerHTML=c.map(x=>'<option value="'+esc(x[1])+'">').join(""); dl.dataset.done="1"; }
}
function chips(box,list,key){
  box.innerHTML=list.length
    ? list.map((t,i)=>'<span class="chip">'+esc(t)+'<button type="button" data-i="'+i+'" aria-label="Remove '+esc(t)+'">&#10005;</button></span>').join("")
    : '<span class="muted">None yet</span>';
  box.querySelectorAll("button[data-i]").forEach(b=>b.onclick=()=>{ st[key].splice(+b.dataset.i,1); render(); setDirty(true); });
}
function render(){
  chips($("pfRoles"),st.target_roles,"target_roles");
  chips($("pfNot"),st.not_my_field,"not_my_field");
  $("pfYears").value=st.years_experience==null?"":st.years_experience;
  $("pfMaxYears").value=st.max_years_asked==null?"":st.max_years_asked;
  $("pfMgmt").checked=!!st.accept_management;
  $("pfRemote").checked=!!st.remote_anywhere;
  $("pfRelocate").checked=!!st.relocate;
  $("pfStateYears").checked=!!st.cv_state_years;
  document.querySelectorAll('#pfShape input').forEach(i=>i.checked=(i.value===st.cv_shape));
  document.querySelectorAll('#pfPages input').forEach(i=>i.checked=(+i.value===st.cv_max_pages));
  renderPlaces(); renderLangs(); renderKinds();
  const by=meta.made_by||{};
  document.querySelectorAll("#pfDlg [data-you]").forEach(el=>{
    const yours=by[el.dataset.you]==="you";
    el.textContent=yours?"yours":""; el.classList.toggle("on",yours);
    el.title=yours?"You set this, so it stays as it is when your profile changes":"";
  });
  const max=(meta.choices||{}).max_items||12;
  $("pfAddPlace").disabled=st.places.length>=max; $("pfAddLang").disabled=st.languages.length>=max;
}
function renderKinds(){
  const box=$("pfKinds"), all=(meta.choices||{}).kinds||[];
  box.innerHTML=all.map(k=>'<label><input type="checkbox" value="'+esc(k[0])+'"'+
    ((st.job_kinds||[]).includes(k[0])?" checked":"")+'>'+esc(k[1][0].toUpperCase()+k[1].slice(1))+'</label>').join("");
  box.querySelectorAll("input").forEach(i=>i.onchange=()=>{
    st.job_kinds=all.map(k=>k[0]).filter(k=>box.querySelector('input[value="'+k+'"]').checked);
    setDirty(true); });
}
function renderPlaces(){
  const box=$("pfPlaces");
  if(!st.places.length){ box.innerHTML='<p class="pf-empty">Anywhere. Add a city or country to say where you would work.</p>'; return; }
  box.innerHTML=st.places.map((p,i)=>
    '<div class="pf-row" data-i="'+i+'">'+
      '<input type="text" class="pf-place" maxlength="60" placeholder="City or region, e.g. Dubai" aria-label="Place '+(i+1)+'" value="'+esc(p.place)+'">'+
      '<input type="text" class="pf-country" list="pfCountryList" maxlength="60" placeholder="Country" aria-label="Country of place '+(i+1)+'" value="'+esc(countryName(p.country))+'">'+
      '<span class="pf-work">'+Object.keys(WORK).map(w=>'<label><input type="checkbox" value="'+w+'"'+(p.work.includes(w)?" checked":"")+'>'+WORK[w]+'</label>').join("")+'</span>'+
      '<button type="button" class="icon-btn danger pf-del" title="Remove"><span class="ico i-trash" aria-hidden="true"></span><span class="sr-only">Remove place '+(i+1)+'</span></button>'+
    '</div>').join("");
  box.querySelectorAll(".pf-row").forEach(el=>{
    const p=st.places[+el.dataset.i];
    el.querySelector(".pf-place").oninput=e=>{ p.place=e.target.value; setDirty(true); };
    el.querySelector(".pf-country").onchange=e=>{
      const c=countryCode(e.target.value);
      if(c===null){ status("“"+e.target.value+"” is not a country in the list. Pick one from the suggestions, or leave it empty for a region."); return; }
      p.country=c; e.target.value=countryName(c); setDirty(true); };
    el.querySelectorAll(".pf-work input").forEach(cb=>cb.onchange=()=>{
      p.work=[...el.querySelectorAll(".pf-work input:checked")].map(x=>x.value); setDirty(true); });
    el.querySelector(".pf-del").onclick=()=>{ st.places.splice(+el.dataset.i,1); render(); setDirty(true); };
  });
}
function renderLangs(){
  const box=$("pfLangs"), levels=(meta.choices||{}).levels||Object.keys(LEVEL);
  if(!st.languages.length){ box.innerHTML='<p class="pf-empty">None yet.</p>'; return; }
  box.innerHTML=st.languages.map((l,i)=>
    '<div class="pf-row" data-i="'+i+'">'+
      '<input type="text" class="pf-lang" maxlength="40" placeholder="Language, e.g. German" aria-label="Language '+(i+1)+'" value="'+esc(l.language)+'">'+
      '<select class="pf-level" aria-label="Level in language '+(i+1)+'">'+["",...levels].map(v=>'<option value="'+v+'"'+(v===(l.level||"")?" selected":"")+'>'+(v?(LEVEL[v]||v):"Level not set")+'</option>').join("")+'</select>'+
      '<button type="button" class="icon-btn danger pf-del" title="Remove"><span class="ico i-trash" aria-hidden="true"></span><span class="sr-only">Remove language '+(i+1)+'</span></button>'+
    '</div>').join("");
  box.querySelectorAll(".pf-row").forEach(el=>{
    const l=st.languages[+el.dataset.i];
    el.querySelector(".pf-lang").oninput=e=>{ l.language=e.target.value; setDirty(true); };
    el.querySelector(".pf-level").onchange=e=>{ l.level=e.target.value; setDirty(true); };
    el.querySelector(".pf-del").onclick=()=>{ st.languages.splice(+el.dataset.i,1); render(); setDirty(true); };
  });
}

/* ---------------------------------------------------------------- inputs */
function addChip(inputId,key){
  const inp=$(inputId), v=inp.value.trim(); if(!v) return;
  const max=(meta.choices||{}).max_items||12;
  if(st[key].length>=max){ status("At most "+max+"."); return; }
  if(!st[key].some(x=>x.toLowerCase()===v.toLowerCase())) st[key].push(v);
  inp.value=""; render(); setDirty(true);
}
[["pfRoleIn","target_roles"],["pfNotIn","not_my_field"]].forEach(([id,key])=>{
  $(id).addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); addChip(id,key); } });
  $(id).addEventListener("change",()=>addChip(id,key));
});
function num(v){ const t=String(v).trim(); if(t==="") return null; const n=Math.round(+t); return Number.isFinite(n)&&n>=0&&n<=60?n:null; }
$("pfYears").oninput=e=>{ st.years_experience=num(e.target.value); setDirty(true); };
$("pfMaxYears").oninput=e=>{ st.max_years_asked=num(e.target.value); setDirty(true); };
$("pfMgmt").onchange=e=>{ st.accept_management=e.target.checked; setDirty(true); };
$("pfRemote").onchange=e=>{ st.remote_anywhere=e.target.checked; setDirty(true); };
$("pfRelocate").onchange=e=>{ st.relocate=e.target.checked; setDirty(true); };
$("pfStateYears").onchange=e=>{ st.cv_state_years=e.target.checked; setDirty(true); };
document.querySelectorAll('#pfShape input').forEach(i=>i.onchange=()=>{ st.cv_shape=i.value; setDirty(true); });
document.querySelectorAll('#pfPages input').forEach(i=>i.onchange=()=>{ st.cv_max_pages=+i.value; setDirty(true); });
$("pfAddPlace").onclick=()=>{ st.places.push({place:"",country:"",work:["onsite","hybrid","remote"]}); render(); setDirty(true);
  const all=$("pfPlaces").querySelectorAll(".pf-place"); all[all.length-1].focus(); };
$("pfAddLang").onclick=()=>{ st.languages.push({language:"",level:""}); render(); setDirty(true);
  const all=$("pfLangs").querySelectorAll(".pf-lang"); all[all.length-1].focus(); };

/* ------------------------------------------------------------- actions */
async function save(){
  // A chip still in its box counts: typing a role and pressing Save keeps it.
  addChip("pfRoleIn","target_roles"); addChip("pfNotIn","not_my_field");
  $("pfSave").disabled=true;
  const r=await post("/preferences",{preferences:st});
  $("pfSave").disabled=false;
  if(!r || r.ok===false){ status("Could not save: "+((r&&r.why)||"the pipeline did not answer")); return; }
  meta=r; st=clone(r.preferences); render(); setDirty(false); explain(r);
  dlg.close(); toast("Preferences saved");
  if(typeof loadEvents==="function") loadEvents();
}
$("pfSave").onclick=save;
$("pfFill").onclick=async()=>{
  if(!confirm("Replace everything here, including what you changed yourself, with what Claude reads in your profile?")) return;
  const r=await post("/preferences/refresh",{reset:true});
  if(!r || r.ok===false){ note("Could not ask Claude: "+((r&&r.why)||"the pipeline did not answer"),"bad"); return; }
  dirty=false; note("Claude is reading your profile to fill these in. This takes under a minute.");
  $("pfFill").disabled=true;
  clearTimeout(pollTimer); pollTimer=setTimeout(()=>load(true),2000);
};
function tryClose(){
  if(dirty && !confirm("Close without saving your changes?")) return;
  clearTimeout(pollTimer); dlg.close();
}
$("pfClose").onclick=tryClose;
dlg.addEventListener("cancel",e=>{ e.preventDefault(); tryClose(); });
dlg.addEventListener("click",e=>{ if(e.target===dlg) tryClose(); });   // the backdrop
$("btnPrefs").onclick=open;
if(location.hash==="#preferences"){ history.replaceState(null,"",location.pathname); open(); }

/* ---------------------------------------------------------------- banner */
// Called by dashboard.js on every refresh with /progress. One state per
// message, so the 2-second refresh does not rebuild it under a click.
window.prefsBanner=function(d){
  const p=d.preferences||{}, el=$("prefsBanner"); if(!el) return;
  let kind="", html="";
  if(p.state==="writing"){ kind="writing";
    html="<span><b>Setting your job preferences.</b> Claude is reading your profile to work out the roles, places and languages every job is judged against.</span>"; }
  else if(p.state==="failed"){ kind="failed:"+p.error;
    html="<span><b>Your job preferences could not be "+(p.stale?"updated from your changed profile, so jobs are still judged against the old ones":"set from your profile")+":</b> "+esc(p.error)+(p.fix?". "+esc(p.fix):"")+".</span>"+
      '<button class="go sm" type="button" data-pf="retry">Try again</button>'+
      '<button class="go sm" type="button" data-pf="open">Fill them in myself</button>'; }
  else if(p.state==="ready" && !p.reviewed && (p.questions||[]).length){
    const n=p.questions.length;
    kind="ask:"+n+":"+(p.made_at||"");
    html="<span><b>Claude has "+(n===1?"a question":n+" questions")+" about your job preferences</b> that your profile leaves open. Your answers decide which jobs you see.</span>"+
      '<button class="go primary sm" type="button" data-pf="open">Answer '+(n===1?"it":"them")+'</button>';
  }
  else if(p.state==="ready" && !p.reviewed){
    const ch=p.changed||[];
    kind="check:"+ch.join(",")+":"+(p.made_at||"");
    html=(ch.length
      ? "<span><b>Your job preferences were updated from your profile</b> ("+esc(ch.map(f=>FIELD[f]||f).join(", "))+"). Check they are right.</span>"
      : "<span><b>Your job preferences were set from your profile.</b> Every job is judged against them, so check they are right.</span>")+
      '<button class="go primary sm" type="button" data-pf="open">Check them</button>';
  }
  el.classList.toggle("hide",!kind);
  if(el.dataset.kind===kind) return;
  el.dataset.kind=kind; el.innerHTML=html;
  const o=el.querySelector('[data-pf="open"]'); if(o) o.onclick=open;
  const t=el.querySelector('[data-pf="retry"]');
  if(t) t.onclick=async()=>{ t.disabled=true; const r=await post("/preferences/refresh",{});
    if(!r||r.ok===false) toast("Could not start: "+((r&&r.why)||"the pipeline did not answer")); };
};
})();
