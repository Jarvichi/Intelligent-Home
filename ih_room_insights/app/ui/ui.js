/* Room Insights page. Read-only. Every value is rendered with textContent; relative URLs only. */
(() => {
  const base = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
  const $ = (id) => document.getElementById(id);
  const num = (v) => typeof v === 'number' && isFinite(v) ? v : null;
  const fmt = (v, d) => num(v) === null ? '–' : String(Math.round(v * Math.pow(10, d)) / Math.pow(10, d));

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function when(iso) {
    const t = new Date(iso);
    return isNaN(t) ? 'unknown time' : t.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }
  function name(s) { return typeof s === 'string' && s ? s.replace(/_/g, ' ') : 'unknown'; }
  function td(text, cls) { return el('td', cls || '', text); }
  function stat(label, value) {
    const d = el('div', 'ri-stat');
    d.appendChild(el('b', '', value));
    d.appendChild(el('span', '', label));
    return d;
  }
  function mins(v) {
    const n = num(v);
    if (n === null) return '–';
    return n >= 90 ? Math.floor(n / 60) + ' h ' + Math.round(n % 60) + ' min' : Math.round(n) + ' min';
  }

  function render(j) {
    const pill = $('mode-pill');
    const live = j.mode === 'live';
    pill.setAttribute('status', j.ran ? (live ? 'ok' : 'warn') : 'unknown');
    pill.textContent = live ? 'Live' : 'Dry run';
    $('empty').hidden = !!j.ran;
    $('error').hidden = true;
    const rooms = Array.isArray(j.rooms) ? j.rooms : [];
    const sum = $('summary');
    sum.replaceChildren(
      stat('Mode', live ? 'Live (publishes sensors)' : 'Dry (computes only)'),
      stat('Last run', j.ran && j.at ? when(j.at) : 'Not yet'),
      stat('Rooms analysed', String(rooms.length)),
      stat('History', fmt(j.history_days, 0) + ' days'),
      stat('Version', typeof j.version === 'string' ? j.version : '–'));

    // rooms
    const body = $('rooms').tBodies[0];
    body.replaceChildren();
    const max = Math.max(1, ...rooms.map((r) => num(r.min_per_deg) || 0));
    for (const r of rooms) {
      const tr = el('tr');
      tr.appendChild(td(name(r.room), 'ri-room'));
      tr.appendChild(td(num(r.current) === null ? '–' : fmt(r.current, 1) + '°', 'n'));
      tr.appendChild(td(num(r.target) === null ? '–' : fmt(r.target, 1) + '°', 'n'));
      const m = num(r.min_per_deg);
      const cell = el('td');
      const bar = el('div', 'ri-bar');
      const track = el('div', 'track');
      const fill = document.createElement('i');
      fill.style.width = (m === null ? 0 : Math.max(2, Math.min(100, m / max * 100))) + '%';
      track.appendChild(fill);
      track.setAttribute('role', 'img');
      track.setAttribute('aria-label', m === null ? 'Not learned yet' : fmt(m, 1) + ' minutes per degree');
      bar.appendChild(track);
      bar.appendChild(el('span', '', m === null ? 'Not learned' : fmt(m, 1)));
      cell.appendChild(bar);
      tr.appendChild(cell);
      tr.appendChild(td(fmt(r.runs, 0), 'n'));
      tr.appendChild(td(r.minutes_to_target === 0 ? 'At target' : mins(r.minutes_to_target), 'n'));
      body.appendChild(tr);
    }
    $('rooms-empty').hidden = rooms.length > 0;
    $('rooms').parentElement.hidden = rooms.length === 0;

    // suspect valves
    const frozen = Array.isArray(j.frozen) ? j.frozen : [];
    const fl = $('frozen');
    fl.replaceChildren();
    for (const f of frozen) {
      const li = el('li');
      li.appendChild(el('b', '', name(f.room)));
      const why = typeof f.why === 'string' ? f.why : '';
      const since = f.since ? ' Last change: ' + when(f.since) + '.' : '';
      li.appendChild(el('span', '', (why ? why.charAt(0).toUpperCase() + why.slice(1) + '.' : '') + since));
      fl.appendChild(li);
    }
    $('frozen-empty').hidden = frozen.length > 0 || !j.ran;

    // mould
    const mould = Array.isArray(j.mould) ? j.mould : [];
    const mb = $('mould').tBodies[0];
    mb.replaceChildren();
    for (const m of mould) {
      const tr = el('tr');
      tr.appendChild(td(name(m.sensor), 'ri-room'));
      const c = el('td');
      const lvl = ['ok', 'watch', 'high'].includes(m.level) ? m.level : '';
      c.appendChild(el('span', 'ri-tag ' + lvl, typeof m.level === 'string' ? m.level : 'unknown'));
      tr.appendChild(c);
      tr.appendChild(td(num(m.now) === null ? '–' : fmt(m.now, 0) + '%', 'n'));
      const h = num(m.minutes_above);
      tr.appendChild(td(h === null ? '–' : fmt(h / 60, 1) + ' h', 'n'));
      mb.appendChild(tr);
    }
    $('mould').parentElement.hidden = mould.length === 0;
    $('mould-empty').hidden = mould.length > 0 || !j.ran;
    $('mould-note').textContent = 'High means humidity stayed above ' + fmt(j.mould_pct, 0) + '% for ' + fmt(j.mould_hours, 1) + ' h or more.';
  }

  async function load() {
    try {
      const r = await fetch(base + 'api/insights', { cache: 'no-store' });
      if (!r.ok) throw new Error('insights ' + r.status);
      render(await r.json());
    } catch (e) {
      $('error').hidden = false;
      $('mode-pill').setAttribute('status', 'error');
      $('mode-pill').textContent = 'Unavailable';
    }
  }
  if (window.IHNav) IHNav.setTitle('Room Insights');
  load();
  setInterval(load, 60000);
})();
