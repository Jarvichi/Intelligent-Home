/* Working now card: polls api/workers every 5 s. Every value is rendered with textContent. */
(() => {
  const base = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
  const rows = document.getElementById('workers-rows'), empty = document.getElementById('workers-empty');
  if (!rows) return;
  const LABEL = { starting: 'Starting', assessing: 'Assessing', planning: 'Planning', implementing: 'Implementing', reporting: 'Reporting', reviewing: 'Reviewing' };
  const NAME = { queue: 'Work queue', overnight: 'Overnight worker' };
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
  function elapsed(s) {
    s = Math.max(0, Math.floor(s));
    if (s < 60) return s + ' s';
    const m = Math.floor(s / 60);
    return m < 60 ? m + ' min' : Math.floor(m / 60) + ' h ' + (m % 60) + ' min';
  }
  function render(v) {
    const list = v && Array.isArray(v.workers) ? v.workers : [];
    rows.replaceChildren();
    empty.hidden = list.length > 0;
    for (const w of list) {
      const r = el('div', 'wk-row');
      r.dataset.stalled = w.stalled ? 'true' : 'false';
      r.appendChild(el('div', 'wk-title', w.title || '(no title)'));
      const st = el('span', 'wk-stage', w.stalled ? 'Stalled' : (LABEL[w.stage] || String(w.stage)));
      st.dataset.stage = String(w.stage);
      r.appendChild(st);
      const meta = el('div', 'wk-meta');
      meta.appendChild(el('span', '', NAME[w.worker] || w.worker));
      meta.appendChild(el('span', '', 'running ' + elapsed(w.elapsed_s)));
      if (w.detail) meta.appendChild(el('span', '', w.detail));
      if (w.stalled) meta.appendChild(el('span', 'wk-flag', 'No update for ' + elapsed(w.age_s) + ' (last stage: ' + (LABEL[w.stage] || w.stage) + ')'));
      r.appendChild(meta);
      rows.appendChild(r);
    }
  }
  async function load() {
    try {
      const r = await fetch(base + 'api/workers', { cache: 'no-store' });
      if (r.ok) render(await r.json());
    } catch (e) { /* keep the last picture */ }
  }
  load();
  setInterval(load, 5000);
})();
