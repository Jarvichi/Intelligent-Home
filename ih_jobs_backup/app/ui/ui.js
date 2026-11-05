/* Shared IH add-on status page. Reads ui.json, api/status and (optional) api/today; every value is rendered with textContent. */
(() => {
  const base = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
  const $ = (id) => document.getElementById(id);
  const pill = $('service-status'), state = $('state'), checked = $('checked'), detail = $('detail');
  const caps = $('capabilities'), capsEmpty = $('capabilities-empty');
  const todayCard = $('today-card'), todayRows = $('today-rows');
  let seq = 0;

  function clock(t) {
    return t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function setHealth(kind, pillText, stateText, checkedText, detailText) {
    pill.setAttribute('status', kind);
    pill.textContent = pillText;
    state.textContent = stateText;
    checked.textContent = checkedText;
    checked.dataset.state = kind === 'ok' ? 'current' : kind === 'error' ? 'unavailable' : 'unknown';
    detail.textContent = detailText || '';
    detail.hidden = !detailText;
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  const num = (v) => typeof v === 'number' && isFinite(v) ? v : null;

  function renderToday(j) {
    const rows = j && Array.isArray(j.rows) ? j.rows.filter((r) => r && typeof r === 'object' && typeof r.name === 'string' && r.name) : [];
    if (!rows.length) { todayCard.hidden = true; todayRows.replaceChildren(); return; }
    $('today-title').textContent = typeof j.title === 'string' && j.title ? j.title : 'Today';
    const sub = $('today-sub');
    sub.textContent = typeof j.subtitle === 'string' ? j.subtitle : '';
    sub.hidden = !sub.textContent;
    const foot = $('today-foot');
    foot.textContent = typeof j.footnote === 'string' ? j.footnote : '';
    foot.hidden = !foot.textContent;
    todayRows.replaceChildren();
    for (const r of rows) {
      const row = el('div', 'today-row');
      row.appendChild(el('div', 'today-name', r.name));
      const score = num(r.score);
      const value = typeof r.value === 'string' && r.value ? r.value.slice(0, 24) : '';
      // value = short text in the score slot (e.g. "9.7°"); plain = no score slot at all (name + note rows). Without either, the old "–" shows.
      if (value) row.appendChild(el('div', 'today-score', value));
      else if (score !== null || r.plain !== true) row.appendChild(el('div', 'today-score', score === null ? '–' : Math.round(score) + '%'));
      if (typeof r.model === 'string' && r.model) row.appendChild(el('div', 'today-model', r.model));
      const used = num(r.used), cap = num(r.cap);
      const pct = num(r.pct);   // optional bar without used/cap (0-100)
      if (pct !== null) {
        const bar = el('div', 'today-bar');
        bar.setAttribute('role', 'progressbar');
        bar.setAttribute('aria-valuemin', '0');
        bar.setAttribute('aria-valuemax', '100');
        bar.setAttribute('aria-valuenow', String(Math.max(0, Math.min(100, Math.round(pct)))));
        bar.setAttribute('aria-label', r.name);
        const fill = document.createElement('i');
        fill.style.width = Math.max(0, Math.min(100, pct)) + '%';
        bar.appendChild(fill);
        row.appendChild(bar);
      } else if (used !== null && cap !== null && cap > 0) {
        const bar = el('div', 'today-bar');
        bar.setAttribute('role', 'progressbar');
        bar.setAttribute('aria-valuemin', '0');
        bar.setAttribute('aria-valuemax', String(cap));
        bar.setAttribute('aria-valuenow', String(Math.min(used, cap)));
        bar.setAttribute('aria-label', r.name + ' allowance used');
        const fill = document.createElement('i');
        fill.style.width = Math.max(0, Math.min(100, used / cap * 100)) + '%';
        if (used >= cap) fill.className = 'full';
        bar.appendChild(fill);
        row.appendChild(bar);
      }
      const use = el('div', 'today-use');
      const unit = typeof r.unit === 'string' && r.unit ? ' ' + r.unit : '';
      use.appendChild(el('span', '', used !== null && cap !== null ? used + ' of ' + cap + unit : (typeof r.note === 'string' ? r.note : '')));
      if (typeof r.state === 'string' && r.state) use.appendChild(el('span', 'today-tag', r.state));
      row.appendChild(use);
      if (used !== null && cap !== null && typeof r.note === 'string' && r.note) row.appendChild(el('div', 'today-note', r.note));
      todayRows.appendChild(row);
    }
    todayCard.hidden = false;
  }

  async function loadToday(mine) {
    try {
      const r = await fetch(base + 'api/today', { cache: 'no-store' });
      if (!r.ok) throw new Error('today ' + r.status);
      const j = await r.json();
      if (mine === seq) renderToday(j);
    } catch (e) {
      if (mine === seq) renderToday(null);
    }
  }

  async function refresh() {
    const mine = ++seq;
    $('check').disabled = true;
    setHealth('unknown', 'Checking…', 'Checking…', 'Checking now', '');
    loadToday(mine);
    try {
      const r = await fetch(base + 'api/status', { cache: 'no-store' });
      if (!r.ok) throw new Error('status ' + r.status);
      const j = await r.json();
      if (!j || typeof j.healthy !== 'boolean') throw new Error('unexpected response');
      if (mine !== seq) return;
      const t = new Date(j.checked_at < 1e12 ? j.checked_at * 1000 : j.checked_at);
      const when = isNaN(t) ? 'Check time not reported' : 'Checked ' + clock(t);
      const d = typeof j.detail === 'string' ? j.detail : '';
      if (j.healthy) setHealth('ok', 'Available', 'Running', when, d);
      else setHealth('error', 'Unavailable', 'Not running', when, d);
    } catch (e) {
      if (mine !== seq) return;
      setHealth('error', 'Not reachable', 'Not reachable', 'Checked ' + clock(new Date()), 'Could not reach the add-on status endpoint. The service state is unknown.');
    } finally {
      if (mine === seq) $('check').disabled = false;
    }
  }

  async function loadInfo() {
    try {
      const r = await fetch(base + 'ui.json', { cache: 'no-store' });
      if (!r.ok) throw new Error('info ' + r.status);
      const i = await r.json();
      const title = typeof i.title === 'string' && i.title ? i.title : 'Intelligent Home add-on';
      $('page-title').textContent = title;
      document.title = title + ' · Intelligent Home';
      $('purpose').textContent = typeof i.purpose === 'string' ? i.purpose : '';
      $('purpose').hidden = !$('purpose').textContent;
      $('description').textContent = typeof i.description === 'string' && i.description ? i.description : 'No description is provided.';
      $('access').textContent = typeof i.access === 'string' && i.access ? 'Access: ' + i.access : '';
      $('access').hidden = !$('access').textContent;
      const items = Array.isArray(i.navigation) ? i.navigation.filter((x) => typeof x === 'string' && x.trim()) : [];
      caps.replaceChildren();
      for (const x of items) { const li = document.createElement('li'); li.textContent = x.trim().charAt(0).toUpperCase() + x.trim().slice(1); caps.appendChild(li); }
      capsEmpty.textContent = 'Nothing is listed yet.';
      capsEmpty.hidden = items.length > 0;
    } catch (e) {
      $('purpose').textContent = 'Add-on details could not be loaded.';
      $('description').textContent = 'Add-on details could not be loaded.';
      caps.replaceChildren();
      capsEmpty.textContent = 'This list could not be loaded.';
      capsEmpty.hidden = false;
    }
  }

  const toggle = $('about-toggle'), body = $('about-body');
  toggle.addEventListener('click', () => {
    const open = toggle.getAttribute('aria-expanded') !== 'true';
    toggle.setAttribute('aria-expanded', String(open));
    body.hidden = !open;
  });
  $('check').addEventListener('click', refresh);
  capsEmpty.hidden = false;
  loadInfo();
  refresh();
  setInterval(refresh, 30000);
})();
