const $ = id => document.getElementById(id);
let result = null;
let keyConfigured = false;
let started = 0;
async function init() {
  try {
    const response = await fetch('/api/config');
    const config = await response.json();
    // Read the same registry as the CLI instead of retaining obsolete hard-coded seed links.
    $('sourcechips').replaceChildren();
    for (const source of config.sources.filter(source => source.purpose === 'lead_evidence')) {
      const link = document.createElement('a');
      link.href = source.url;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = `${source.title} · ${source.connector} / ${source.evidence_scope} ↗`;
      $('sourcechips').appendChild(link);
    }
    keyConfigured = config.api_key_configured;
    $('model').firstChild.textContent = config.model;
    $('keynotice').textContent = keyConfigured ? 'API key configured · Stored on the backend only' : 'No API key · Set it in .env and restart, or choose Demo';
    $('keynotice').classList.toggle('ready', keyConfigured);
    const context = await (await fetch('/api/context')).json();
    $('context').textContent = JSON.stringify(context, null, 2);
  } catch { $('keynotice').textContent = 'Cannot reach the backend. Check that the server is running.'; }
}
document.querySelectorAll('[data-preset]').forEach(button => button.addEventListener('click', () => {
  $('prompt').value = button.dataset.preset;
}));
$('mode').addEventListener('change', () => {
  const demo = $('mode').value === 'demo';
  $('permission').disabled = demo;
  $('keynotice').textContent = demo ? 'Synthetic demo: no network or LLM calls. The fixture is fixed; your prompt is not interpreted.' : keyConfigured ? 'API key configured · Stored on the backend only' : 'Set MOONSHOT_API_KEY in .env and restart the server.';
});
$('runform').addEventListener('submit', async event => {
  event.preventDefault();
  $('errorbox').hidden = true;
  const urls = $('urls').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
  if (urls.length > 4) return showError('Add no more than 4 URLs.');
  if ($('mode').value === 'live' && !keyConfigured) return showError('Set your API key in .env and restart the server, or switch to Demo.');
  if ($('mode').value === 'live' && !$('permission').checked) return showError('Review the terms and access permissions for the selected sources, then confirm the checkbox.');
  result = null;
  $('output').hidden = true;
  $('empty').hidden = false;
  $('empty').querySelector('h3').textContent = 'Processing your research task';
  $('empty').querySelector('p').textContent = 'Live research interprets your request, fetches pages, and extracts signals. This may take several minutes.';
  $('runbutton').disabled = true;
  $('copy').disabled = true;
  $('download').disabled = true;
  for (const id of ['docs','signals','errors']) $(id).textContent = '—';
  $('status').textContent = '● Task running';
  started = Date.now();
  const timer = setInterval(()=>{$('runtime').textContent = `${Math.floor((Date.now()-started)/1000)}s`;},1000);
  try {
    const response = await fetch('/api/run', { method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({prompt:$('prompt').value,mode:$('mode').value,additional_urls:urls,
        additional_source_class:$('sourceclass').value,permissions_confirmed:$('permission').checked,max_pages:Number($('pages').value)})});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
    result = data;
    $('output').textContent = JSON.stringify(data,null,2);
    $('output').hidden = false;
    $('empty').hidden = true;
    $('docs').textContent = data.documents.length;
    $('signals').textContent = data.signals.length;
    $('errors').textContent = data.errors.length;
    const labels = {completed:'Completed',needs_clarification:'Clarification needed · See questions',partial:'Partially completed · See errors',failed:'Failed · See errors'};
    $('status').textContent = `${data.is_synthetic?'Synthetic demo · ':''}${labels[data.status] || data.status}`;
    $('copy').disabled = false;
    $('download').disabled = false;
  } catch (error) {
    $('status').textContent = 'Run failed';
    showError(error.message);
  } finally {
    clearInterval(timer);
    $('runtime').textContent = `${((Date.now()-started)/1000).toFixed(1)}s`;
    $('runbutton').disabled = false;
  }
});
function showError(message) { $('errorbox').textContent = message; $('errorbox').hidden = false; }
$('copy').addEventListener('click',async()=>{
  try { await navigator.clipboard.writeText(JSON.stringify(result,null,2)); $('copy').textContent='Copied'; setTimeout(()=>$('copy').textContent='Copy',1500); }
  catch { showError('Clipboard access failed. Download the JSON instead.'); }
});
$('download').addEventListener('click',()=>{
  const blob = new Blob([JSON.stringify(result,null,2)], {type:'application/json;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href=url; a.download=`safety-signals-${result.run_id}.json`; a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
});
init();
