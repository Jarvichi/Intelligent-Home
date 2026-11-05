/* Shared IH add-on status page. Reads ui.json and api/status; every value is rendered with textContent. */
(() => {
  const base = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
  const $ = (id) => document.getElementById(id);
  const pill = $('service-status'), state = $('state'), checked = $('checked'), detail = $('detail');
  const caps = $('capabilities'), capsEmpty = $('capabilities-empty');
  let seq = 0;

  function setHealth(kind, pillText, stateText, checkedText, detailText) {
    pill.setAttribute('status', kind);
    pill.textContent = pillText;
    state.textContent = stateText;
    checked.textContent = checkedText;
    checked.dataset.state = kind === 'ok' ? 'current' : kind === 'error' ? 'unavailable' : 'unknown';
    detail.textContent = detailText || '';
    detail.hidden = !detailText;
  }

  async function refresh() {
    const mine = ++seq;
    $('check').disabled = true;
    setHealth('unknown', 'Checking…', 'Checking service…', 'Connecting to the local health endpoint.', '');
    try {
      const r = await fetch(base + 'api/status', { cache: 'no-store' });
      if (!r.ok) throw new Error('status ' + r.status);
      const j = await r.json();
      if (!j || typeof j.healthy !== 'boolean') throw new Error('unexpected response');
      if (mine !== seq) return;
      const t = new Date(j.checked_at < 1e12 ? j.checked_at * 1000 : j.checked_at);
      const when = isNaN(t) ? 'Check time not reported' : 'Checked ' + t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const d = typeof j.detail === 'string' ? j.detail : '';
      if (j.healthy) setHealth('ok', 'Available', 'Available', when, d);
      else setHealth('error', 'Unavailable', 'Unavailable', when, d);
    } catch (e) {
      if (mine !== seq) return;
      setHealth('error', 'Not reachable', 'Status unavailable', 'Could not read the status.', 'Could not reach the add-on status endpoint. The service state is unknown.');
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
      const items = Array.isArray(i.navigation) ? i.navigation.filter((x) => typeof x === 'string' && x) : [];
      caps.replaceChildren();
      for (const x of items) { const li = document.createElement('li'); li.textContent = x; caps.appendChild(li); }
      capsEmpty.textContent = 'No capabilities are listed.';
      capsEmpty.hidden = items.length > 0;
    } catch (e) {
      $('purpose').textContent = 'Add-on details could not be loaded.';
      $('description').textContent = 'Add-on details could not be loaded.';
      caps.replaceChildren();
      capsEmpty.textContent = 'Capabilities could not be loaded.';
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
