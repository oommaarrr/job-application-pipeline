/*
 * The Edit profile window (the pencil in the top bar).
 *
 * What you type goes to Claude through the bridge (scraper/profile_chat.py),
 * which edits the files in your profile folder and answers with what it
 * changed. Each message shows the files it touched, with the lines added and
 * removed; Undo last change puts them back. Uses $, esc, post, toast and tick
 * from dashboard.js.
 */
(function(){
const dlg=$("pcDlg"); if(!dlg) return;
const EXAMPLES=[
  "Add a project: ",
  "Add my new job: ",
  "I now speak German at B1",
  "Stop ranking frontend roles highly",
];
let pollTimer=null, lastSeen=-1, lastRunning=false;

function note(text,kind){ const n=$("pcNote"); n.textContent=text||"";
  n.className="an-note"+(kind?" "+kind:"")+(text?"":" hide"); }
function status(text){ $("pcStatus").textContent=text||""; }

$("pcExamples").innerHTML=EXAMPLES.map(t=>
  '<button type="button" class="chip pc-ex">'+esc(t.replace(/: $/,"…"))+'</button>').join("");
$("pcExamples").querySelectorAll(".pc-ex").forEach((b,i)=>b.onclick=()=>{
  const box=$("pcIn"); box.value=EXAMPLES[i]; box.focus();
  box.setSelectionRange(box.value.length,box.value.length); });

// One diff line per row, coloured by what happened to it.
function diffHtml(diff){
  return diff.split("\n").filter(l=>l&&!l.startsWith("---")&&!l.startsWith("+++")).map(l=>{
    const k=l[0]==="+"?"add":l[0]==="-"?"del":l.startsWith("@@")?"hunk":"";
    return '<span class="pc-dl '+k+'">'+esc(l.startsWith("@@")?"···":l)+"</span>";
  }).join("");
}
function changesHtml(list){
  if(!list||!list.length) return "";
  return '<ul class="pc-changes">'+list.map(c=>
    '<li><details><summary><code>'+esc(c.file)+'</code> <span class="muted">'+
    esc(c.kind)+'</span> <span class="pc-plus">+'+c.plus+'</span> <span class="pc-minus">−'+c.minus+
    '</span></summary><pre class="pc-diff">'+diffHtml(c.diff||"")+'</pre></details></li>').join("")+"</ul>";
}
function msgHtml(m){
  const who={you:"You",claude:"Claude",error:"Could not finish",undo:"Undo"}[m.role]||m.role;
  const ranker=(m.changes||[]).some(c=>c.file==="profile.md")
    ? '<p class="muted pc-after">The next ranking judges jobs against the new profile.</p>' : "";
  return '<li class="pc-msg '+esc(m.role)+'"><span class="pc-who">'+esc(who)+'</span>'+
    '<div class="pc-text">'+esc(m.text)+'</div>'+changesHtml(m.changes)+ranker+"</li>";
}

function render(d){
  $("pcFolder").textContent=d.folder?("in "+d.folder):"";
  const msgs=d.messages||[];
  $("pcIntro").classList.toggle("hide",msgs.length>0);
  if(msgs.length!==lastSeen||d.running!==lastRunning){
    $("pcLog").innerHTML=msgs.map(msgHtml).join("")+
      (d.running?'<li class="pc-msg claude working"><span class="pc-who">Claude</span>'+
        '<div class="pc-text muted">Reading your profile and making the change…</div></li>':"");
    lastSeen=msgs.length; lastRunning=d.running;
    const body=$("pcBody"); body.scrollTop=body.scrollHeight;
  }
  if(d.blocked) note("This profile cannot be edited here: "+d.blocked,"warn");
  else note("");
  $("pcSend").disabled=!!(d.running||d.blocked);
  $("pcUndo").disabled=!!(d.running||!d.can_undo);
  $("pcNew").disabled=!!d.running||!msgs.length;
  status(d.running?"Claude is working. You can close this window; it carries on.":"");
}

async function load(){
  let d;
  try{ d=await fetch("/profile/chat",{cache:"no-store"}).then(r=>r.json()); }
  catch(e){ note("The pipeline is not answering, so Claude cannot be reached.","bad"); return null; }
  render(d);
  clearTimeout(pollTimer);
  if(d.running&&dlg.open) pollTimer=setTimeout(load,1500);
  return d;
}
async function open(){
  if(!dlg.open) dlg.showModal();
  const d=await load();
  if(d&&!d.running) $("pcIn").focus();
}
async function send(){
  const box=$("pcIn"), text=box.value.trim();
  if(!text){ box.focus(); return; }
  $("pcSend").disabled=true;
  const r=await post("/profile/chat",{message:text});
  if(!r||r.ok===false){ status(""); note("Not sent: "+((r&&(r.why||r.error))||"the pipeline did not answer"),"bad");
    $("pcSend").disabled=false; return; }
  box.value=""; note("");
  await load();
}
$("pcSend").onclick=send;
$("pcIn").addEventListener("keydown",e=>{
  if(e.key==="Enter"&&(e.ctrlKey||e.metaKey)){ e.preventDefault(); send(); } });
$("pcUndo").onclick=async()=>{
  const r=await post("/profile/chat/undo",{});
  if(!r||r.ok===false){ note("Nothing was undone: "+((r&&(r.why||r.error))||"the pipeline did not answer"),"warn"); return; }
  toast("Undid the last change to your profile.");
  await load();
};
$("pcNew").onclick=async()=>{
  if(!confirm("Start a new conversation? Your profile files stay exactly as they are now, but Undo will no longer reach the changes made so far.")) return;
  const r=await post("/profile/chat/new",{});
  if(!r||r.ok===false){ note((r&&(r.why||r.error))||"the pipeline did not answer","warn"); return; }
  await load(); $("pcIn").focus();
};
function close(){ clearTimeout(pollTimer); dlg.close(); if(typeof tick==="function") tick(); }
$("pcClose").onclick=close;
dlg.addEventListener("cancel",e=>{ e.preventDefault(); close(); });
dlg.addEventListener("click",e=>{ if(e.target===dlg) close(); });   // the backdrop
$("btnProfile").onclick=open;
})();
