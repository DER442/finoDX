"""plugin_ui.py - the full-screen PLUGINS & VST manager page, served at http://127.0.0.1:8765/plugins
(MENU > PLUGINS & VST > PLUGIN MANAGER on the panel opens it). Kept in Python so the packaged app cannot miss it."""

PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DX Arranger - Plugins &amp; VST</title>
<style>
:root{--bg:#05070d;--pn:#0c1220;--ln:#1c2a44;--tx:#cfeaff;--dim:#6f88a8;--ac:#3fb6ff;--ok:#5be39a;--bad:#ff7b7b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.4 system-ui,Segoe UI,sans-serif}
header{display:flex;gap:12px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--ln);position:sticky;top:0;background:var(--bg);z-index:5}
header h1{font-size:16px;margin:0;letter-spacing:.08em}
header .sp{flex:1}
main{display:grid;grid-template-columns:minmax(300px,1fr) minmax(420px,1.6fr);gap:14px;padding:14px}
@media(max-width:900px){main{grid-template-columns:1fr}}
section{background:var(--pn);border:1px solid var(--ln);border-radius:8px;padding:12px}
h2{font-size:13px;margin:0 0 8px;color:var(--ac);letter-spacing:.1em}
button,select,input{font:inherit;color:var(--tx);background:#111b30;border:1px solid var(--ln);border-radius:5px;padding:5px 9px}
button{cursor:pointer}button:hover{border-color:var(--ac)}
button.on{background:#12406b;border-color:var(--ac)}
button.danger:hover{border-color:var(--bad);color:var(--bad)}
.row{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:6px 0}
.dim{color:var(--dim)}.bad{color:var(--bad)}.ok{color:var(--ok)}
.pl{display:flex;gap:6px;align-items:center;padding:5px 0;border-bottom:1px solid #111b30}
.pl .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tag{font-size:11px;border:1px solid var(--ln);border-radius:9px;padding:0 7px;color:var(--dim)}
.tag.i{color:var(--ok);border-color:var(--ok)}
.slot{border:1px solid var(--ln);border-radius:8px;padding:10px;margin-bottom:10px}
.slot.live{border-color:#1f6a52}
.slot h3{margin:0 0 6px;font-size:14px;display:flex;gap:8px;align-items:center}
.chs{display:grid;grid-template-columns:repeat(16,1fr);gap:3px;margin:6px 0}
.chs button{padding:3px 0;font-size:12px;text-align:center}
input[type=range]{padding:0;width:150px}
#list{max-height:60vh;overflow:auto}
.meter{height:5px;background:#111b30;border-radius:3px;overflow:hidden;margin:4px 0}.meter i{display:block;height:100%;background:linear-gradient(90deg,#2fcf86,#e8d34a 75%,#ff6b6b)}
.ins{border-top:1px dashed var(--ln);padding-top:6px}
.mbox{background:var(--pn);border:1px solid var(--ac);border-radius:8px;width:min(640px,94vw);max-height:86vh;margin:6vh auto;padding:12px;display:flex;flex-direction:column}
#mlist{overflow:auto;margin-top:8px}.prm{display:grid;grid-template-columns:1fr 2fr 90px;gap:8px;align-items:center;padding:3px 0;border-bottom:1px solid #111b30}.prm input{width:100%}
.note{font-size:12px;color:var(--dim);margin-top:6px}
</style></head><body>
<header><h1>PLUGINS &amp; VST</h1><span id="eng" class="dim"></span><span id="status" class="dim"></span><span class="sp"></span>
<label class="dim"><input type="checkbox" id="allparts"> play ALL parts (not drums only)</label>
<button id="panic" class="danger">PANIC</button><a href="/" style="color:var(--ac)">&larr; panel</a></header>
<div id="modal" style="display:none;position:fixed;inset:0;background:#000a;z-index:20"><div class="mbox"><div class="row"><b id="mt"></b><span style="flex:1"></span><input id="mq" placeholder="filter"><button id="mx">Close</button></div><div id="mlist"></div></div></div>
<main>
<section>
  <h2>LIBRARY</h2>
  <div class="row"><button id="scan">Scan</button><button id="rescan" title="Ask every plugin again">Full rescan</button>
  <button id="addf">Add folder...</button><span id="scanst" class="dim"></span></div>
  <div class="row"><input id="path" placeholder="or paste a folder / .vst3 path" style="flex:1"><button id="addp">Add</button></div>
  <div id="folders" class="note"></div>
  <div class="row"><input id="q" placeholder="search plugins" style="flex:1"></div>
  <div id="list"></div>
  <div class="note">VST3 only: VST2 (.dll) and CLAP plugins cannot be hosted. To install a plugin, run its own installer;
  it normally puts the .vst3 in Common Files\VST3, which is scanned automatically. Instruments go into a slot; effects are added as
  INSERTS on a slot or on the master.</div>
</section>
<section>
  <h2>MIXER</h2>
  <div id="rack"></div>
  <div id="master"></div>
  <div class="note">Each slot runs in its own helper process: if a plugin crashes, only that slot stops (it restarts by itself) and the music keeps playing. A MIDI channel that no slot takes plays on the normal internal sound. Tick several slots on a channel to layer.
  To hear bass / chords / pad in a plugin, switch on "play ALL parts" (style reloads).</div>
</section>
</main>
<script>
const $=id=>document.getElementById(id);
let D=null, pick={}, busy=false;
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function post(body){
  try{const r=await fetch('/api/plugins',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const j=await r.json(); if(j.error) alert(j.error); refresh(); return j;}
  catch(e){alert('Engine not reachable');}
}
async function refresh(){
  if(busy) return; busy=true;
  try{ D=await (await fetch('/api/plugins')).json(); render(); }catch(e){ $('status').textContent='engine not reachable'; }
  busy=false;
}
function render(){
  if(!D.slots){ $('status').textContent='sound engine starting...'; return; }
  $('status').textContent=D.audio_error?D.audio_error:(D.editors.length?'plugin window open':'');
  $('status').className=D.audio_error?'bad':'dim';
  $('eng').textContent=D.engine==='dxhost'?('C++ engine (DXHost)'+(D.latency_ms?' - delay compensation '+D.latency_ms+' ms':'')):('Python engine'+(D.engine_note?' - '+D.engine_note:''));
  $('scanst').textContent=D.scanning?('scanning '+D.scan_done+'/'+D.scan_total+' ...'):'';
  if(document.activeElement!==$('allparts')) $('allparts').checked=!D.drums_only;
  $('folders').innerHTML='Scanning: '+D.folders.map(f=>esc(f)+(D.user_folders.includes(f)?' <a href="#" data-rm="'+esc(f)+'">[remove]</a>':'')).join(' &nbsp;|&nbsp; ');
  const q=$('q').value.toLowerCase();
  const slotsOpts=D.slots.map(s=>'<option value="'+s.i+'">slot '+(s.i+1)+'</option>').join('');
  $('list').innerHTML=D.catalog.filter(e=>!q||(e.name+' '+e.file).toLowerCase().includes(q)).map((e,k)=>{
    const t=e.error?'<span class="tag bad" title="'+esc(e.error)+'">error</span>':e.instrument===true?'<span class="tag i">instrument</span>':e.instrument===false?'<span class="tag">effect</span>':'<span class="tag">?</span>';
    const sel=(pick[e.path+'|'+e.name]??0);
    return '<div class="pl"><span class="nm" title="'+esc(e.path)+'">'+esc(e.name)+'</span>'+t+
      '<select data-k="'+esc(e.path+'|'+e.name)+'">'+slotsOpts.replace('value="'+sel+'"','value="'+sel+'" selected')+'</select>'+
      '<button data-load="'+esc(e.path)+'" data-name="'+esc(e.name)+'"'+(e.instrument===false||e.error?' disabled':'')+'>Load</button></div>';
  }).join('')||'<div class="dim">Nothing found yet. Press Scan, or add the folder your plugins are installed in.</div>';
  $('rack').innerHTML=D.slots.map(s=>slotHtml(s)).join('');
  $('master').innerHTML=masterHtml();
}
function fxOptions(){
  return D.catalog.filter(e=>e.instrument===false&&!e.error).map(e=>'<option value="'+esc(e.path+'|'+e.name)+'">'+esc(e.name)+'</option>').join('');
}
function insertsHtml(which,fx){
  const w=String(which);
  return '<div class="row ins"><span class="dim">inserts</span>'+fx.map((f,k)=>'<span class="tag">'+esc(f.name)+'</span>'+
    '<button data-fxed="'+w+':'+k+'">Window</button>'+(D.engine==='dxhost'?'<button data-par="'+w+':'+k+'">Params</button>':'')+'<button data-fxby="'+w+':'+k+'" class="'+(f.bypass?'on':'')+'">Bypass</button>'+
    '<button data-fxrm="'+w+':'+k+'" class="danger">&#10005;</button>').join('')+
    (fx.length<D.max_inserts?'<select data-fxadd="'+w+'"><option value="">+ insert effect...</option>'+fxOptions()+'</select>':'')+'</div>';
}
function meter(p){return '<div class="meter"><i style="width:'+Math.min(100,p)+'%"></i></div>';}
function masterHtml(){
  return '<div class="slot live"><h3>MASTER</h3>'+meter(D.master.peak)+insertsHtml('master',D.master.fx)+'</div>';
}
function slotHtml(s){
  const live=s.loaded;
  const name=s.loading?'loading...':(s.name||'(empty)');
  const chs=Array.from({length:16},(_,c)=>'<button class="'+(s.channels.includes(c)?'on':'')+'" data-ch="'+s.i+':'+c+'">'+(c+1)+'</button>').join('');
  const parts=D.parts.map(p=>'<button data-part="'+s.i+':'+p+'" class="'+(s.parts.includes(p)||(p==='ALL CHANNELS'&&s.parts.includes('ALL'))?'on':'')+'">'+esc(p)+'</button>').join('');
  const ports='<select data-port="'+s.i+'"><option value="">send to MIDI port...</option>'+D.midi_ports.map(p=>'<option>'+esc(p)+'</option>').join('')+'</select>';
  return '<div class="slot'+(live?' live':'')+'"><h3>'+(s.i+1)+'&nbsp;'+esc(name)+(s.kind==='midi'?' <span class="tag">MIDI port</span>':'')+
   '<span style="flex:1"></span>'+(s.kind==='plugin'&&live?'<button data-ed="'+s.i+'">Open window</button>'+(D.engine==='dxhost'?'<button data-par="'+s.i+':inst">Params</button>':'')+'<button data-sv="'+s.i+'" title="remember the sound as it is now">Save sound</button>':'')+
   (s.name||s.channels.length?'<button class="danger" data-clr="'+s.i+'">Clear</button>':'')+'</h3>'+
   (s.error?'<div class="bad">'+esc(s.error)+'</div>':'')+
   meter(s.peak)+'<div class="row">'+parts+'</div><div class="chs">'+chs+'</div>'+
   '<div class="row"><label>level <input type="range" min="0" max="150" value="'+s.level+'" data-lv="'+s.i+'"> <span>'+s.level+'%</span></label>'+
   '<label>pan <input type="range" min="-100" max="100" value="'+s.pan+'" data-pan="'+s.i+'"> <span>'+s.pan+'</span></label>'+
   '<button data-mu="'+s.i+'" class="'+(s.mute?'on':'')+'">Mute</button><button data-so="'+s.i+'" class="'+(s.solo?'on':'')+'">Solo</button>'+
   '<label>out ch <select data-oc="'+s.i+'"><option value="0">keep</option>'+Array.from({length:16},(_,c)=>'<option value="'+(c+1)+'"'+(s.out_ch===c+1?' selected':'')+'>'+(c+1)+'</option>').join('')+'</select></label>'+
   '<button data-pg="'+s.i+'" class="'+(s.programs?'on':'')+'" title="let the style send program changes to the plugin">Programs</button>'+
   '<button data-vv="'+s.i+'" class="'+(s.vel_vol?'on':'')+'" title="for plugins that ignore the volume knobs">Volume via velocity</button></div>'+
   (s.kind==='plugin'&&live?insertsHtml(s.i,s.fx):'')+'<div class="row">'+ports+'</div></div>';
}
let modal=null, mAll=[];
async function raw(body){ try{ return await (await fetch('/api/plugins',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json(); }catch(e){ return {error:'Engine not reachable'}; } }
async function openParams(slot,target){
  const r=await raw({op:'params',slot:slot,target:target}); if(r.error){alert(r.error);return;}
  modal={slot:slot,target:target}; mAll=r.params; $('mt').textContent=(r.plugin||'Plugin')+' - parameters'; $('mq').value=''; $('modal').style.display='block'; drawParams();
}
function drawParams(){
  const q=$('mq').value.toLowerCase();
  $('mlist').innerHTML=mAll.filter(p=>!q||p.name.toLowerCase().includes(q)).map(p=>'<div class="prm"><span title="'+esc(p.name)+'">'+esc(p.name)+'</span><input type="range" min="0" max="1000" value="'+Math.round(p.value*1000)+'" data-pi="'+p.i+'"><span class="dim" id="pt'+p.i+'">'+esc(p.text)+'</span></div>').join('')||'<div class="dim">No parameters.</div>';
}
let ptimer=null;
document.addEventListener('input',e=>{ const i=e.target.dataset&&e.target.dataset.pi; if(i===undefined||!modal) return;
  clearTimeout(ptimer); const v=Number(e.target.value)/1000;
  ptimer=setTimeout(async()=>{ const r=await raw({op:'setparam',slot:modal.slot,target:modal.target,index:Number(i),value:v}); if(r.text!==undefined){const el=$('pt'+i); if(el) el.textContent=r.text;} },40); });
$('mq').oninput=drawParams; $('mx').onclick=()=>{ $('modal').style.display='none'; modal=null; };
document.addEventListener('click',e=>{
  const t=e.target, g=a=>t.dataset[a];
  if(t.dataset.rm){e.preventDefault();post({op:'removefolder',path:t.dataset.rm});return;}
  if(!D) return;
  if(g('load')){const sl=Number(document.querySelector('select[data-k="'+CSS.escape(g('load')+'|'+g('name'))+'"]').value);post({op:'load',slot:sl,path:g('load'),name:g('name')});}
  else if(g('ch')){const[i,c]=g('ch').split(':').map(Number);const cur=new Set(D.slots[i].channels);cur.has(c)?cur.delete(c):cur.add(c);post({op:'channels',slot:i,channels:[...cur]});}
  else if(g('part')){const k=g('part').indexOf(':');post({op:'part',slot:Number(g('part').slice(0,k)),part:g('part').slice(k+1)});}
  else if(g('ed')) post({op:'editor',slot:Number(g('ed'))});
  else if(g('sv')) post({op:'savestate',slot:Number(g('sv'))});
  else if(g('clr')) post({op:'clear',slot:Number(g('clr'))});
  else if(g('par')){const[w,k]=g('par').split(':');openParams(w==='master'?'master':Number(w),k);}
  else if(g('so')){const s=D.slots[g('so')];post({op:'set',slot:s.i,solo:!s.solo});}
  else if(g('fxed')){const[w,k]=g('fxed').split(':');post({op:'editor',slot:w==='master'?'master':Number(w),target:k});}
  else if(g('fxby')){const[w,k]=g('fxby').split(':');const f=(w==='master'?D.master.fx:D.slots[w].fx)[k];post({op:'fx_bypass',slot:w==='master'?'master':Number(w),index:Number(k),on:!f.bypass});}
  else if(g('fxrm')){const[w,k]=g('fxrm').split(':');post({op:'fx_remove',slot:w==='master'?'master':Number(w),index:Number(k)});}
  else if(g('mu')){const s=D.slots[g('mu')];post({op:'set',slot:s.i,mute:!s.mute});}
  else if(g('pg')){const s=D.slots[g('pg')];post({op:'set',slot:s.i,programs:!s.programs});}
  else if(g('vv')){const s=D.slots[g('vv')];post({op:'set',slot:s.i,vel_vol:!s.vel_vol});}
});
document.addEventListener('change',e=>{
  const t=e.target;
  if(t.dataset.k) pick[t.dataset.k]=Number(t.value);
  else if(t.dataset.oc) post({op:'set',slot:Number(t.dataset.oc),out_ch:Number(t.value)});
  else if(t.dataset.port&&t.value) post({op:'midiport',slot:Number(t.dataset.port),port:t.value});
  else if(t.dataset.fxadd&&t.value){const[path,name]=[t.value.slice(0,t.value.lastIndexOf('|')),t.value.slice(t.value.lastIndexOf('|')+1)];const w=t.dataset.fxadd;post({op:'fx_add',slot:w==='master'?'master':Number(w),path:path,name:name});}
  else if(t.dataset.pan) post({op:'set',slot:Number(t.dataset.pan),pan:Number(t.value)});
  else if(t.dataset.lv) post({op:'set',slot:Number(t.dataset.lv),level:Number(t.value)});
  else if(t.id==='allparts') post({op:'drums_only',value:!t.checked});
});
document.addEventListener('input',e=>{ if(e.target.dataset.lv) e.target.nextElementSibling.textContent=e.target.value+'%'; if(e.target.dataset.pan) e.target.nextElementSibling.textContent=e.target.value; });
$('scan').onclick=()=>post({op:'scan'});
$('rescan').onclick=()=>post({op:'scan',force:true});
$('addf').onclick=()=>post({op:'addfolder_dialog'});
$('addp').onclick=()=>{const v=$('path').value.trim();if(v){post({op:'addfolder',path:v});$('path').value='';}};
$('panic').onclick=()=>post({op:'panic'});
$('q').oninput=()=>D&&render();
refresh(); setInterval(()=>{ const a=document.activeElement; if(!modal&&(!a||!['range','select-one','text'].includes(a.type))) refresh(); },1200);
</script></body></html>
"""
