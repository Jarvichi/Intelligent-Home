/* Jobs page. Reads api/status and api/jobs (same origin, relative). Every value is rendered with textContent. */
(() => {
  'use strict';
  const L = window.JobsLogic;
  const base = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined && text !== null) n.textContent = String(text); return n; };
  const when = (ms, now) => L.formatWhen(ms, now) || 'Not reported';

  let data = null;        // last good read: { jobs, children, checked_ms }
  let readFailed = false; // the latest read failed
  let serviceOk = null;   // null = unknown
  let loaded = false;
  let busy = false;
  const open = new Set();

  async function getJson(path) {
    const r = await fetch(base + path, { cache: 'no-store' });
    if (!r.ok) throw new Error(path + ' ' + r.status);
    return r.json();
  }

  async function refresh() {
    if (busy) return;
    busy = true;
    const btn = $('check');
    btn.disabled = true; btn.textContent = 'Checking…';
    const [svc, jobs] = await Promise.allSettled([getJson('api/status'), getJson('api/jobs')]);
    serviceOk = svc.status === 'fulfilled' && svc.value && typeof svc.value.healthy === 'boolean' ? svc.value.healthy : null;
    try {
      if (jobs.status !== 'fulfilled') throw new Error('read failed');
      const j = jobs.value;
      if (!j || !j.available || !Array.isArray(j.jobs)) throw new Error('unavailable');
      const checked = typeof j.checked_at === 'number' ? (j.checked_at < 1e12 ? j.checked_at * 1000 : j.checked_at) : Date.now();
      data = { jobs: j.jobs.filter((x) => x && typeof x === 'object' && typeof x.name === 'string'), children: Array.isArray(j.children) ? j.children : [], checked_ms: checked };
      readFailed = false;
    } catch (e) { readFailed = true; }
    loaded = true;
    render();
    btn.disabled = false; btn.textContent = 'Check again';
    busy = false;
  }

  function health() {
    const pill = $('health');
    const set = (status, text) => { pill.setAttribute('status', status); pill.textContent = text; };
    if (!loaded) return set('unknown', 'Checking…');
    if (serviceOk === false) return set('error', 'Service unavailable');
    if (readFailed && !data) return set('error', 'Results unavailable');
    if (readFailed) return set('warn', 'Results out of date');
    if (!data) return set('unknown', 'Checking…');
    const s = L.summarise(data.jobs, data.checked_ms);
    if (s.failing.length) return set('error', s.failing.length + (s.failing.length === 1 ? ' job failing' : ' jobs failing'));
    if (serviceOk === null) return set('warn', 'Service state unknown');
    set('ok', 'All healthy');
  }

  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

  function renderSummary() {
    const sub = $('now-sub'), tiles = $('tiles');
    $('lede').textContent = "Runs the house's scheduled jobs and watches over the Jobs family." + (data ? ' Checked ' + when(data.checked_ms, data.checked_ms) + '.' : '');
    if (!loaded) { sub.textContent = 'Loading job results…'; tiles.hidden = true; return; }
    if (!data) { sub.textContent = 'Job results could not be read, so nothing can be said about the jobs yet.'; tiles.hidden = true; return; }
    const s = L.summarise(data.jobs, data.checked_ms), now = data.checked_ms;
    let text;
    if (!s.total) text = 'Jobs reports no jobs.';
    else {
      const parts = [plural(s.live, 'job is', 'jobs are') + ' live', s.dry + ' in test mode', s.off + ' switched off'];
      text = (s.failing.length ? plural(s.failing.length, 'job has', 'jobs have') + ' failed on the last run: ' + s.failing.join(', ') + '. ' : 'No job has failed. ') + parts.join(', ') + '.';
    }
    if (readFailed) text = 'Could not refresh. These results are from ' + when(data.checked_ms, Date.now()) + '. ' + text;
    sub.textContent = text;
    tiles.replaceChildren();
    if (!s.total) { tiles.hidden = true; return; }
    const tile = (cls, big, small) => { const t = el('div', 'jb-tile ' + cls); t.append(el('b', '', big), el('span', '', small)); tiles.append(t); };
    tile(s.failing.length ? 'bad' : 'ok', s.failing.length, (s.failing.length === 1 ? 'Job failing' : 'Jobs failing') + ' (' + plural(s.errors, 'error', 'errors') + ' in total)');
    tile('', s.last ? when(s.last.ms, now) : 'None', s.last ? 'Last run: ' + s.last.name : 'No job has run yet');
    tile('', s.next ? when(s.next.ms, now) : 'None', s.next ? 'Next run: ' + s.next.name : 'No next run is scheduled');
    const groups = Object.keys(s.waitingGroups).map((k) => s.waitingGroups[k] + ' ' + k).join(', ');
    tile(s.waiting ? 'warn' : 'ok', s.waiting, 'Not run yet' + (groups ? ' (' + groups + ')' : ''));
    tiles.hidden = false;
  }

  function childTitle(c) { return typeof c.title === 'string' && c.title ? c.title.replace(/^Intelligent Home - /, '') : String(c.key || 'Child add-on'); }

  function renderFamily() {
    const card = $('family-card');
    const kids = data ? data.children.filter((c) => c && typeof c === 'object') : [];
    if (!kids.length) { card.hidden = true; return; }
    card.hidden = false;
    $('family-sub').textContent = kids.map(childTitle).join(', ') + '. Whether each add-on is running and in which mode.';
    const rows = $('family-rows');
    rows.replaceChildren();
    for (const c of kids) {
      const up = c.up === true && c.healthy !== false;
      const row = el('div', 'jb-row');
      row.append(el('span', 'jb-dot' + (up ? (c.mode === 'live' ? '' : ' warn') : ' bad')));
      const g = el('div', 'grow');
      g.append(el('b', '', childTitle(c)));
      const seen = typeof c.seen_s_ago === 'number' ? ' Last heard from ' + Math.max(0, Math.round(c.seen_s_ago)) + ' s before the check.' : '';
      g.append(el('span', 'm', (c.mode === 'live' ? 'Live' : 'Test mode') + '. ' + (up ? 'Running.' : 'Not responding.') + seen + (c.version ? ' Version ' + c.version + '.' : '')));
      row.append(g);
      rows.append(row);
    }
    const note = $('family-note');
    const covered = builtinCovered();
    note.hidden = !covered.length;
    note.textContent = covered.length ? 'Built-in job' + (covered.length > 1 ? 's' : '') + ' ' + covered.join(' and ') + ' below ' + (covered.length > 1 ? 'are' : 'is') + ' in test mode and ' + (covered.length > 1 ? 'have' : 'has') + ' not run here, because the live child add-on does that work.' : '';
  }

  // Built-in jobs that a running, live child add-on with the same job name already does.
  function builtinCovered() {
    if (!data) return [];
    return data.jobs.filter((j) => j.kind === 'Built-in job' && L.modeOf(j) === 'dry' && !L.hasRun(j) &&
      data.children.some((c) => c && c.key === j.name && c.up === true && c.mode === 'live')).map((j) => j.name);
  }

  function itemFor(job, now) {
    const mode = L.modeOf(job), off = mode === 'off', fail = L.failed(job), wait = L.notRunYet(job);
    const item = el('div', 'jb-item');
    const bodyId = 'body-' + job.name.replace(/[^a-zA-Z0-9_-]/g, '_');
    const isOpen = open.has(job.name);
    const btn = el('button', 'jb-disc');
    btn.type = 'button'; btn.dataset.job = job.name;
    btn.setAttribute('aria-expanded', String(isOpen)); btn.setAttribute('aria-controls', bodyId);
    btn.append(el('span', 'jb-dot' + (off ? ' off' : fail ? ' bad' : mode === 'dry' ? ' warn' : '')));
    btn.firstChild.setAttribute('aria-hidden', 'true');
    const t = el('span', 't', job.name + ' ');
    t.append(el('span', 'jb-tag ' + (off ? '' : mode === 'dry' ? 'warn' : 'ok'), off ? 'Off' : mode === 'dry' ? 'Test mode' : 'Live'));
    if (fail) t.append(el('span', 'jb-tag bad', 'Failed'));
    const last = L.lastMs(job, now), next = L.nextMs(job);
    const sub = off ? 'Switched off' : L.scheduleText(job) + ' · ' + (last !== null ? 'last ' + when(last, now) : 'not run yet') + (next !== null ? ' · next ' + when(next, now) : '');
    const s = el('span', 's', sub);
    const chev = el('span', 'chev'); chev.setAttribute('aria-hidden', 'true');
    btn.append(t, s, chev);
    const body = el('div', 'jb-body'); body.id = bodyId; body.hidden = !isOpen;
    const covered = builtinCovered().indexOf(job.name) >= 0;
    if (covered) body.append(el('p', 'ih-metadata', 'Test mode, not run here. The live child add-on with the same name does this work.'));
    const result = job.last_ok === true ? 'Succeeded' : job.last_ok === false ? 'Failed' : (wait || off ? 'Not run yet' : 'Not reported');
    const rows = [
      ['Kind', L.kindLabel(job.kind)], ['Mode', L.modeLabel(mode)], ['Why', L.whyText(job.why)],
      ['Last run', last !== null ? when(last, now) : 'Not reported'],
      ['Last run mode', job.last_mode ? L.modeLabel(job.last_mode) : 'Not reported'],
      ['Result', result],
      ['Next run', next !== null ? when(next, now) : 'Not reported'],
      ['Runs', job.runs == null ? 'Not reported' : job.runs], ['Errors', job.errors == null ? 'Not reported' : job.errors],
      ['Last error', job.last_error ? job.last_error : 'None']
    ];
    const dl = el('dl', 'jb-kv');
    for (const [k, v] of rows) dl.append(el('dt', '', k), el('dd', '', v));
    body.append(dl);
    item.append(btn, body);
    return item;
  }

  function stateBox(kind, title, text) {
    const box = $('state');
    if (!kind) { box.hidden = true; box.replaceChildren(); return; }
    box.hidden = false; box.className = 'jb-state' + (kind === 'bad' ? ' bad' : '');
    box.replaceChildren(el('b', '', title), document.createTextNode(text));
  }

  function renderFilterOptions(c) {
    const f = $('f'), cur = f.value || 'all';
    const opts = [['all', 'All (' + c.all + ')'], ['live', 'Live (' + c.live + ')'], ['dry', 'Test mode (' + c.dry + ')'], ['off', 'Switched off (' + c.off + ')'], ['wait', 'Not run yet (' + c.wait + ')']];
    f.replaceChildren();
    for (const [v, label] of opts) { const o = el('option', '', label); o.value = v; f.append(o); }
    f.value = cur;
  }

  function renderList() {
    const list = $('list'), count = $('count');
    const focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.job : null;
    list.setAttribute('aria-busy', String(!loaded));
    if (!loaded) { list.replaceChildren(); $('off-box').hidden = true; count.textContent = ''; stateBox('info', 'Loading job results…', ' This takes a few seconds.'); return; }
    if (!data) {
      list.replaceChildren(); $('off-box').hidden = true; count.textContent = '';
      stateBox('bad', 'Job results could not be read.', ' This is a failed read, not an empty list. Check that the Jobs service is running, then choose Check again.');
      return;
    }
    const q = $('q').value, show = $('f').value, key = $('s').value, now = data.checked_ms;
    const all = data.jobs, c = L.counts(all);
    renderFilterOptions(c);
    const shown = L.sortJobs(L.filterJobs(all, q, show), key, now);
    const active = shown.filter((j) => L.modeOf(j) !== 'off'), off = shown.filter((j) => L.modeOf(j) === 'off');
    list.replaceChildren(...active.map((j) => itemFor(j, now)));
    const offBox = $('off-box'), offBody = $('off-body');
    offBox.hidden = !off.length;
    $('off-title').textContent = 'Switched off (' + off.length + ')';
    const offOpen = $('off-toggle').getAttribute('aria-expanded') === 'true' || show === 'off';
    if (off.length) {
      const wrap = el('div', 'jb-off-list');
      wrap.append(...off.map((j) => itemFor(j, now)));
      offBody.replaceChildren(wrap);
      $('off-toggle').setAttribute('aria-expanded', String(offOpen)); offBody.hidden = !offOpen;
    }
    count.textContent = shown.length + ' of ' + plural(all.length, 'job', 'jobs') + (readFailed ? '. Could not refresh; showing the last good read.' : '');
    if (!all.length) stateBox('info', 'No jobs reported.', ' Jobs is running but lists nothing. Review the add-on configuration.');
    else if (!shown.length) stateBox('info', 'No jobs match.', ' Clear the search or choose All to see every job.');
    else stateBox(readFailed ? 'bad' : null, 'Could not refresh job results.', ' The list below is from the last good read.');
    if (focused) { const again = document.querySelector('[data-job="' + CSS.escape(focused) + '"]'); if (again) again.focus(); }
  }

  function render() { health(); renderSummary(); renderFamily(); renderList(); }

  document.addEventListener('click', (e) => {
    const b = e.target.closest('.jb-disc');
    if (!b) return;
    const body = document.getElementById(b.getAttribute('aria-controls'));
    const now = b.getAttribute('aria-expanded') !== 'true';
    b.setAttribute('aria-expanded', String(now));
    if (body) body.hidden = !now;
    if (b.dataset.job) { if (now) open.add(b.dataset.job); else open.delete(b.dataset.job); }
  });
  ['q', 'f', 's'].forEach((id) => $(id).addEventListener('input', () => { if (loaded) renderList(); }));
  $('check').addEventListener('click', refresh);
  render();
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 30000);
})();
