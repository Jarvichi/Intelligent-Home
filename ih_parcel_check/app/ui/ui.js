/* Parcel check page. Reads api/data (relative URL). Every value is written with textContent; no pictures are ever requested. */
(() => {
  const base = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
  const $ = (id) => document.getElementById(id);
  let seq = 0;

  function el(tag, text, cls) {
    const e = document.createElement(tag);
    if (text !== undefined) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  }
  const num = (v) => (typeof v === 'number' && isFinite(v) ? v : 0);
  const str = (v) => (typeof v === 'string' ? v : '');

  function when(ts) {
    const t = new Date(num(ts) * 1000);
    if (isNaN(t) || !ts) return '–';
    const today = new Date();
    const hm = t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return t.toDateString() === today.toDateString() ? hm : t.toLocaleDateString([], { day: 'numeric', month: 'short' }) + ' ' + hm;
  }

  function countRow(label, c) {
    const tr = document.createElement('tr');
    tr.appendChild(el('th', label)).scope = 'row';
    for (const k of ['checked', 'parcel', 'no_parcel', 'unsure', 'errors']) tr.appendChild(el('td', String(num(c && c[k]))));
    return tr;
  }

  function render(j) {
    const pill = $('service-status');
    const live = j.mode === 'live';
    $('mode').textContent = live ? 'Live mode' : 'DRY mode (nothing is posted)';
    pill.setAttribute('status', j.ai_layer_ok === false ? 'warn' : 'ok');
    pill.textContent = j.ai_layer_ok === false ? 'AI layer down' : 'Running';
    const last = $('last');
    last.textContent = j.last_check ? 'Last check ' + when(j.last_check) : 'No check yet';
    last.dataset.state = j.last_check ? 'current' : 'unknown';
    $('ai').textContent = j.ai_layer_ok === true ? 'AI layer: answering.' : j.ai_layer_ok === false ? 'AI layer: not answering. Checks are retried.' : 'AI layer: not asked yet.';
    const cb = $('counts-body');
    cb.replaceChildren(countRow('Today', j.today), countRow('7 days', j.week));
    const vb = $('verdicts-body');
    vb.replaceChildren();
    const vs = Array.isArray(j.verdicts) ? j.verdicts : [];
    for (const v of vs) {
      const tr = document.createElement('tr');
      tr.appendChild(el('td', when(v.time)));
      tr.appendChild(el('td', str(v.camera) + (v.check && v.check !== 'parcel' ? ' · ' + str(v.check) : '')));
      const td = document.createElement('td');
      td.appendChild(el('span', str(v.label), 'pc-v pc-' + (['yes', 'no', 'unsure'].includes(v.verdict) ? v.verdict : 'unsure')));
      if (v.answer) td.appendChild(el('span', ' “' + str(v.answer) + '”', 'ih-metadata'));
      tr.appendChild(td);
      tr.appendChild(el('td', str(v.ai)));
      vb.appendChild(tr);
    }
    $('verdicts-empty').hidden = vs.length > 0;
    const eb = $('errors');
    eb.replaceChildren();
    const es = Array.isArray(j.errors) ? j.errors : [];
    for (const e of es) eb.appendChild(el('li', when(e.time) + ' · ' + str(e.text)));
    $('errors-empty').hidden = es.length > 0;
  }

  async function refresh() {
    const mine = ++seq;
    $('refresh').disabled = true;
    try {
      const r = await fetch(base + 'api/data', { cache: 'no-store' });
      if (!r.ok) throw new Error('data ' + r.status);
      const j = await r.json();
      if (mine === seq) render(j);
    } catch (e) {
      if (mine !== seq) return;
      const pill = $('service-status');
      pill.setAttribute('status', 'error');
      pill.textContent = 'Not reachable';
      $('mode').textContent = 'Not reachable';
      $('ai').textContent = 'Could not load the data from the add-on.';
    } finally {
      if (mine === seq) $('refresh').disabled = false;
    }
  }

  $('refresh').addEventListener('click', refresh);
  refresh();
  setInterval(refresh, 30000);
})();
