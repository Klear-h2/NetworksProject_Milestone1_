const $ = id => document.getElementById(id);
let state = 'Idle';
const fields = ['target', 'production_ms', 'inspection_ms', 'n', 'k'];
function showError(message) { $('alert').hidden = !message; $('alert').textContent = message || ''; }
async function send(path, data={}) {
  showError('');
  try {
    const res = await fetch('/api/' + path, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    const result = await res.json();
    if (!res.ok) throw new Error(result.error || `HTTP ${res.status}`);
    render(result);
  } catch (error) { showError(error.message); }
}
$('settings').addEventListener('submit', e => {
  e.preventDefault();
  const obj={};
  for(const id of fields) {
    const control=$(id);
    if (!control.checkValidity()) { control.reportValidity(); return; }
    obj[id]=Number(control.value);
  }
  send('start', obj);
});
$('pause').onclick=()=>send('pause');
$('resume').onclick=()=>send('resume');
$('stop').onclick=()=>send('stop');
function render(info) {
  if(!info || !info.state) return;
  state=info.state;
  $('state').textContent=state;
  $('state').className='status '+ ({Idle:'idle',Running:'running',Paused:'paused',Fault:'faulted'}[state]||'idle');
  for (const name of ['produced','inspected','accepted','rejected']) $(name).textContent=info[name];
  $('queue').textContent=`${info.queue_occupancy} / ${info.queue_capacity}`;
  $('fill').style.width=100*info.queue_occupancy/info.queue_capacity+'%';
  $('a-status').textContent=info.esp32_a_online?'Online':'Offline';
  $('b-status').textContent=info.esp32_b_online?'Online':'Offline';
  $('batch').textContent=info.batch_id||'—';
  $('inflight').textContent=info.inflight||'—';
  $('backpressure').textContent=info.backpressure?'Queue full — production blocked':'';
  $('fault').textContent=info.fault_reason||'';
  $('start').disabled=state!=='Idle';
  $('pause').disabled=state!=='Running';
  $('resume').disabled=state!=='Paused';
  $('stop').disabled=state==='Idle';
  for(const id of fields) $(id).disabled=state!=='Idle';
  $('events').replaceChildren(...(info.events||[]).map(entry=>{
    const row=document.createElement('div'); row.className='event';
    const time=document.createElement('span');time.className='time';time.textContent=entry.time;
    const label=document.createElement('span');label.className='label';label.textContent=entry.event;
    const detail=document.createElement('span');detail.textContent=' '+(entry.job_id||entry.reason||entry.result||'');
    row.append(time,label,detail);return row;
  }));
}
let ws;
function connect() {
  ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws');
  ws.onopen=()=>{$('link').textContent='Live WebSocket connected'};
  ws.onmessage=e=>{try{render(JSON.parse(e.data))}catch(_) {}};
  ws.onclose=()=>{$('link').textContent='WebSocket disconnected — reconnecting';setTimeout(connect,1500)};
  ws.onerror=()=>ws.close();
}
connect();
fetch('/api/status').then(x=>x.json()).then(render).catch(()=>showError('Cannot reach gateway'));
