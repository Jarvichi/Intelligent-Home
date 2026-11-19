'use strict';
(() => {
  const $ = s => document.querySelector(s);
  let info = {speakers: [], voices: [], engines: [], mode: 'dry'}, timer = null, sortKey = 'last_used', sortDir = -1, player = null;
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
    const p = new URLSearchParams({q: $('#q').value.trim(), engine: $('#f-engine').value, voice: $('#f-voice').value, pinned: $('#f-pin').value});
    const d = await api('api/clips?' + p);
    const rows = $('#rows'); rows.replaceChildren();
    const val = c => sortKey === 'voice' ? c.engine + c.voice : sortKey === 'text' ? c.text.toLowerCase() : sortKey === 'pinned' ? (c.pinned ? 1 : 0) : c[sortKey];
    d.clips.sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : 0) * sortDir);
    document.querySelectorAll('th .sort').forEach(h => h.parentElement.setAttribute('aria-sort', h.dataset.k === sortKey ? (sortDir > 0 ? 'ascending' : 'descending') : 'none'));
    d.clips.forEach(c => {
      const tr = el('tr'), msg = el('td', c.text, 'msg'), pin = el('td', undefined, 'pincell');
      pin.append(el('span', c.pinned ? 'Pinned' : '-', c.pinned ? 'tag pinned' : 'muted'));
      if (c.auto_pinned) pin.append(el('span', 'auto', 'tag auto'));
      if (c.label) msg.append(el('span', c.label, 'tag'));
      tr.append(pin, msg, el('td', (c.engine.replace('tts.', '')) + (c.voice ? ' / ' + c.voice : '')), el('td', kb(c.size)), el('td', String(c.hits)), el('td', String(c.uses_30d)), el('td', ago(c.last_used)));
      const b = el('td', undefined, 'btns');
      const btn = (t, f, cls) => { const x = el('button', t, cls); x.type = 'button'; x.onclick = () => Promise.resolve(f()).catch(err); b.append(x); return x; };
      btn('Listen', () => listen(c));
      btn('Play on speaker', async () => say(await api('api/clips/' + c.id + '/play', {speaker: $('#speaker').value})));
      btn(c.pinned ? 'Unpin' : 'Pin', async () => { await api('api/clips/' + c.id + '/pin', {pinned: !c.pinned}); await load(); }, c.pinned ? 'pinned' : '');
      btn('Delete', async () => { if (confirm('Delete this clip?')) { await api('api/clips/' + c.id + '/delete', {}); await load(); } });
      tr.append(b); rows.append(tr);
    });
    $('#empty').hidden = d.clips.length > 0;
    if (d.stats) meter(d.stats);
  }
  function meter(st) {
    const pct = Math.min(100, st.percent);
    $('#stats').textContent = st.clips + ' clips (' + st.pinned + ' pinned' + (st.auto_pinned ? ', ' + st.auto_pinned + ' auto' : '') + ')' + (st.auto_pin_uses ? '. Auto-pin: ' + st.auto_pin_uses + ' uses in ' + st.auto_pin_days + ' days.' : '');
    $('#bar-fill').style.width = pct + '%'; $('#bar').setAttribute('aria-valuenow', String(Math.round(pct))); $('#bar').classList.toggle('full', st.percent >= 90);
    $('#bar-text').textContent = kb(st.bytes) + ' of ' + kb(st.max_bytes) + ' (' + st.percent + '%)';
  }
  // Listen plays through the ingress itself (relative URL, no signed link, so it needs neither base_url nor api_token). If the audio element refuses
  // the stream, the file is fetched and played from memory instead.
  async function listen(c) {
    if (player) { player.pause(); player = null; }
    const url = 'api/clips/' + c.id + '/audio', a = new Audio(); player = a;
    try { a.src = url; await a.play(); return; } catch (e) { /* fall back below */ }
    const r = await fetch(url);
    if (!r.ok) throw new Error('Could not load the clip.');
    const blob = await r.blob(), b = new Audio(URL.createObjectURL(blob)); player = b;
    try { await b.play(); } catch (e) { throw new Error('This browser could not play the clip.'); }
  }
  function say(r) { $('#status').textContent = r.status === 'dry' ? 'Dry mode: nothing was played.' : r.status === 'spoken' ? 'Played.' : 'Not played (' + r.status + (r.reason ? ': ' + r.reason : '') + ').'; }
  const err = e => { $('#status').textContent = e.message; };
  const later = () => { clearTimeout(timer); timer = setTimeout(() => load().catch(err), 250); };
  $('#q').oninput = later; $('#f-voice').onchange = $('#f-engine').onchange = $('#f-pin').onchange = () => load().catch(err);
  document.querySelectorAll('th .sort').forEach(h => h.onclick = () => { sortDir = sortKey === h.dataset.k ? -sortDir : (['text', 'voice'].includes(h.dataset.k) ? 1 : -1); sortKey = h.dataset.k; load().catch(err); });
  $('#clear').onclick = () => { $('#q').value = ''; $('#f-voice').value = '*'; $('#f-engine').value = ''; $('#f-pin').value = ''; load().catch(err); };
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
