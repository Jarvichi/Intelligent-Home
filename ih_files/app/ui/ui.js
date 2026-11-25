/* Files page. Relative URLs only; every value is set as plain text. */
(() => {
  const base = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
  const $ = (id) => document.getElementById(id);
  let seq = 0;
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
  const gb = (n) => (n / 1073741824).toFixed(n >= 10737418240 ? 0 : 1) + ' GB';
  const clock = (t) => t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  function setHealth(kind, pill, state, checked) {
    const p = $('service-status');
    p.setAttribute('status', kind);
    p.textContent = pill;
    $('state').textContent = state;
    $('checked').textContent = checked;
    $('checked').dataset.state = kind === 'ok' ? 'current' : kind === 'error' ? 'unavailable' : 'unknown';
  }

  function render(j) {
    $('version').textContent = typeof j.version === 'string' ? j.version : '–';
    $('port').textContent = Number.isInteger(j.smb_port) ? String(j.smb_port) + ' (inside the add-on; the outside port is in its network settings)' : '–';
    const d = j.disk, bar = $('disk-bar');
    if (d && d.total > 0) {
      $('free').textContent = gb(d.free) + ' of ' + gb(d.total);
      const pct = Math.max(0, Math.min(100, d.used / d.total * 100));
      bar.hidden = false;
      bar.setAttribute('aria-valuenow', String(Math.round(pct)));
      bar.firstElementChild.style.width = pct + '%';
      bar.firstElementChild.className = pct >= 90 ? 'full' : '';
      $('disk-note').textContent = pct >= 90 ? 'Space is running low.' : '';
    } else {
      $('free').textContent = 'Not reported';
      bar.hidden = true;
      $('disk-note').textContent = '';
    }
    const ss = $('sessions'); ss.replaceChildren();
    const sessions = Array.isArray(j.sessions) ? j.sessions : [];
    for (const s of sessions) {
      const row = el('div', 'f-row');
      row.appendChild(el('div', 'f-name', (s.user || 'Unknown') + ' on ' + s.share));
      row.appendChild(el('div', 'f-sub', 'From ' + s.machine + (s.since ? ' since ' + s.since : '')));
      ss.appendChild(row);
    }
    const se = $('sessions-empty');
    se.textContent = j.sessions_known === false ? 'Connections could not be read.' : 'Nobody is connected.';
    se.hidden = sessions.length > 0;
    const sh = $('shares'); sh.replaceChildren();
    const shares = Array.isArray(j.shares) ? j.shares : [];
    for (const s of shares) {
      const row = el('div', 'f-row');
      row.appendChild(el('div', 'f-name', s.name));
      row.appendChild(el('div', 'f-sub', (s.comment ? s.comment + '. ' : '') + 'Folder ' + s.path + (s.folder_exists ? '' : ' (not found yet)')));
      const chips = el('ul', 'f-chips');
      for (const a of (s.access || [])) {
        chips.appendChild(el('li', 'f-chip' + (a.mode === 'read-write' ? ' rw' : ''), a.user + ': ' + a.mode));
      }
      if (s.open_to_all) chips.appendChild(el('li', 'f-chip', 'Any valid login'));
      row.appendChild(chips);
      sh.appendChild(row);
    }
    $('shares-empty').hidden = shares.length > 0;
    const lg = $('logins'); lg.replaceChildren();
    const logins = Array.isArray(j.logins) ? j.logins : [];
    for (const l of logins) lg.appendChild(el('li', 'f-chip' + (l.login_enabled ? ' rw' : ' off'), l.name + (l.login_enabled ? '' : ' (no password set)')));
    $('logins-empty').hidden = logins.length > 0;
  }

  async function refresh() {
    const mine = ++seq;
    $('check').disabled = true;
    setHealth('unknown', 'Checking…', 'Checking…', 'Checking now');
    try {
      const r = await fetch(base + 'api/status', { cache: 'no-store' });
      if (!r.ok) throw new Error('status ' + r.status);
      const j = await r.json();
      if (!j || typeof j.healthy !== 'boolean') throw new Error('unexpected response');
      if (mine !== seq) return;
      const when = 'Checked ' + clock(new Date((j.checked_at || Date.now() / 1000) * 1000));
      if (j.healthy) setHealth('ok', 'Running', 'File sharing is running', when);
      else setHealth('error', 'Not running', 'File sharing is not running', when);
      render(j);
    } catch (e) {
      if (mine !== seq) return;
      setHealth('error', 'Not reachable', 'Not reachable', 'Checked ' + clock(new Date()));
    } finally {
      if (mine === seq) $('check').disabled = false;
    }
  }

  $('check').addEventListener('click', refresh);
  refresh();
  setInterval(refresh, 30000);
})();
