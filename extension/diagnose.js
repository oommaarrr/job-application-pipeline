/* Finds where LinkedIn is hiding the job list and the job ids. Reads only. */
(() => {
const L=[], say=s=>L.push(s), cl=s=>(s||"").replace(/\s+/g," ").trim();
say("URL "+location.pathname);

// 1. Is the list even in this document?
const frames=[...document.querySelectorAll("iframe")];
say(`\niframes: ${frames.length}`);
frames.slice(0,5).forEach(f=>say("   "+cl(f.getAttribute("src")).slice(0,80)));
const shadows=[];
(function walk(n){for(const el of n.querySelectorAll("*")){if(el.shadowRoot){shadows.push(el);walk(el.shadowRoot);}}})(document);
say(`shadow roots: ${shadows.length}`);
shadows.slice(0,5).forEach(e=>say("   "+e.tagName.toLowerCase()));

// 2. Every distinct 8-10 digit number in an attribute, and which attribute holds it.
const attrHits={};
for(const el of document.querySelectorAll("*"))
  for(const a of el.attributes){
    const m=a.value.match(/\b\d{8,10}\b/);
    if(!m) continue;
    const k=a.name;
    (attrHits[k]=attrHits[k]||new Set()).add(m[0]);
  }
say("\nATTRIBUTES CARRYING AN 8-10 DIGIT NUMBER  (attr: distinct values)");
Object.entries(attrHits).sort((a,b)=>b[1].size-a[1].size).slice(0,15)
  .forEach(([k,v])=>say(`  ${String(v.size).padStart(4)}  ${k}   e.g. ${[...v][0]}`));

// 3. data-testid inventory.
const t={};for(const el of document.querySelectorAll("[data-testid]")){const v=el.getAttribute("data-testid");t[v]=(t[v]||0)+1;}
say("\nDATA-TESTID  (count, name)");
Object.entries(t).sort((a,b)=>b[1]-a[1]).slice(0,30).forEach(([k,n])=>say(`  ${String(n).padStart(4)}  ${k}`));

// 4. The list, found by shape rather than by name: the element with the most
//    children that read like a job card.
const cardish=el=>{const x=cl(el.innerText);return x.length>25&&x.length<400&&(el.innerText||"").split("\n").filter(Boolean).length>=2;};
let best=null,bestN=0;
for(const el of document.querySelectorAll("ul,ol,div,section")){
  const n=[...el.children].filter(cardish).length;
  if(n>bestN){bestN=n;best=el;}
}
say(`\nBEST LIST CANDIDATE: ${best?best.tagName.toLowerCase():"-"} with ${bestN} card-like children`);
if(best){
  const sig=el=>{if(!el)return"-";const at=[...el.attributes].filter(a=>!/^class$|^style$/.test(a.name)).slice(0,5).map(a=>`${a.name}="${cl(a.value).slice(0,40)}"`);return el.tagName.toLowerCase()+(at.length?" ["+at.join(" ")+"]":"");};
  [...best.children].filter(cardish).slice(0,2).forEach((row,i)=>{
    say(`\n  CARD ${i}  ${sig(row)}`);
    say(`    text: ${cl(row.innerText).slice(0,120)}`);
    let d=0;
    (function down(el,dep){if(dep>3)return;for(const c of el.children){
      const isA=c.tagName==="A";
      say("      "+"  ".repeat(dep)+sig(c)+(isA?`  href=${cl(c.getAttribute("href")).slice(0,60)}`:""));
      if(++d<14) down(c,dep+1);}})(row,0);
  });
}
const out=L.join("\n");console.log(out);try{copy(out);console.log("\n(copied)")}catch{}
return "done";
})();
