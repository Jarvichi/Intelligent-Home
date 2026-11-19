'use strict';
(() => {
  const $ = s => document.querySelector(s);
  let info = {speakers: [], voices: [], engines: [], mode: 'dry'}, timer = null;
  const el = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
  const api = async (path, body) => {
    const r = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || 'That did not work.');
    return j;
  };
  const kb = n => n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';
  const ago = t => { const s = Math.max(0, Date.now() / 1000 - t); return s < 90 ? 'just now' : s < 5400 ? Math.round(s / 60) + ' min ago' : s < 129600 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' days ago'; };
  const fill = (sel, items, first) => { const keep = sel.value; sel.replaceChildren(); (first ? [first] : []).forEach(([v, t]) => sel.append(new Option(t, v))); items.forEach(i => sel.append(new Option(i, i))); if ([...sel.options].some(o => o.value === keep)) sel.value = keep; };
  async function loadInfo() {
    info = await api('api/info');
    $('#mode').textContent = (info.mode === 'live' ? 'Live: playing on a speaker really plays.' : 'Dry mode: nothing is played on a speaker, clips can still be saved.') + (info.can_play ? '' : ' Playing needs base_url and api_token set in the add-on options.');
    fill($('#f-voice'), info.voices, ['*', 'All voices']); $('#f-voice').insertBefore(new Option('Engine default', ''), $('#f-voice').options[1] || null); fill($('#f-engine'), info.engines, ['', 'All engines']);
    fill($('#gen-voice'), info.voices, ['', 'Engine default']); fill($('#speaker'), info.speakers); fill($('#gen-speaker'), info.speakers);
  }
  async function load() {
    const p = new URLSearchParams({q: $('#q').value.trim(), engine: $('#f-engine').value, voice: $('#f-voice').value});
    const d = await api('api/clips?' + p);
    const rows = $('#rows'); rows.replaceChildren();
    d.clips.forEach(c => {
      const tr = el('tr'), msg = el('td', c.text, 'msg');
      if (c.label) msg.append(el('span', c.label, 'tag')); if (c.pinned) msg.append(el('span', 'pinned', 'tag'));
      tr.append(msg, el('td', (c.engine.replace('tts.', '')) + (c.voice ? ' / ' + c.voice : '')), el('td', kb(c.size)), el('td', String(c.hits)), el('td', ago(c.last_used)));
      const b = el('td', undefined, 'btns');
      const btn = (t, f, cls) => { const x = el('button', t, cls); x.type = 'button'; x.onclick = f; b.append(x); return x; };
      btn('Listen', () => { const a = new Audio('api/clips/' + c.id + '/audio'); a.play().catch(() => msg.append(el('span', 'cannot play here', 'tag'))); });
      btn('Play on speaker', async () => say(await api('api/clips/' + c.id + '/play', {speaker: $('#speaker').value})));
      btn(c.pinned ? 'Unpin' : 'Pin', async () => { await api('api/clips/' + c.id + '/pin', {pinned: !c.pinned}); load(); }, c.pinned ? 'pinned' : '');
      btn('Delete', async () => { if (confirm('Delete this clip?')) { await api('api/clips/' + c.id + '/delete', {}); load(); } });
      tr.append(b); rows.append(tr);
    });
    $('#empty').hidden = d.clips.length > 0;
    if (d.stats) $('#stats').textContent = d.stats.clips + ' clips (' + d.stats.pinned + ' pinned), ' + kb(d.stats.bytes) + ' of ' + kb(d.stats.max_bytes);
  }
  function say(r) { $('#status').textContent = r.status === 'dry' ? 'Dry mode: nothing was played.' : r.status === 'spoken' ? 'Played.' : 'Not played (' + r.status + (r.reason ? ': ' + r.reason : '') + ').'; }
  const err = e => { $('#status').textContent = e.message; };
  const later = () => { clearTimeout(timer); timer = setTimeout(() => load().catch(err), 250); };
  $('#q').oninput = later; $('#f-voice').onchange = $('#f-engine').onchange = () => load().catch(err);
  $('#clear').onclick = () => { $('#q').value = ''; $('#f-voice').value = '*'; $('#f-engine').value = ''; load().catch(err); };
  let act = 'save';
  document.querySelectorAll('#gen button[type=submit]').forEach(b => b.onclick = () => { act = b.dataset.act; });
  $('#gen').onsubmit = async ev => {
    ev.preventDefault(); const f = ev.target.elements, st = $('#gen-status'); st.textContent = 'Working...';
    try {
      const body = {text: f.text.value, voice: f.voice.value, label: f.label.value};
      const d = act === 'play' ? await api('api/generate-play', Object.assign(body, {speaker: f.speaker.value})) : await api('api/generate', body);
      st.textContent = 'Saved.' + (d.play ? ' ' + (d.play.status === 'dry' ? 'Dry mode: nothing was played.' : d.play.status === 'spoken' ? 'Played.' : 'Not played (' + d.play.status + ').') : '');
      load();
    } catch (e) { st.textContent = e.message; }
  };
  loadInfo().then(load).catch(err);
})();
