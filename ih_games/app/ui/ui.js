(function () {
  'use strict';
  const root = location.pathname.endsWith('/') ? location.pathname : location.pathname.replace(/[^/]*$/, '');
  const playersEl = document.getElementById('players');
  const libraryEl = document.getElementById('library');
  const statusEl = document.getElementById('service-status');
  let players = [];
  let selected = '';
  let requestedRoute = '';
  let artSearchAvailable = false;
  const detailCache = new Map();

  function routeKey(route) {
    const path = route && typeof route.path === 'string' ? route.path : '';
    const parts = path.split('/').filter(Boolean);
    return parts.length ? parts[parts.length - 1] : '';
  }
  function applyRequestedRoute() {
    if (!players.length || !requestedRoute) return;
    const next = requestedRoute === 'research' ? 'research' : players.some(p => p.key === requestedRoute) ? requestedRoute : '';
    if (!next || next === selected) return;
    if (next === 'research') selectResearch(); else selectPlayer(next);
  }
  window.addEventListener('message', function (event) {
    if (event.source !== window.parent || event.origin !== location.origin || !event.data || event.data.type !== 'home-assistant/properties') return;
    requestedRoute = routeKey(event.data.route);
    applyRequestedRoute();
  });
  if (window.parent !== window) window.parent.postMessage({ type: 'home-assistant/subscribe-properties', handleSafeArea: true }, '*');

  function el(tag, text, cls) {
    const n = document.createElement(tag);
    if (text !== undefined && text !== null) n.textContent = String(text);
    if (cls) n.className = cls;
    return n;
  }
  function fmtDate(value) {
    if (!value) return 'Not recorded';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
  }
  async function request(path, method) {
    const r = await fetch(root + path, { cache: 'no-store', method: method || 'GET', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: method === 'POST' ? '{}' : undefined });
    let data;
    try { data = await r.json(); } catch (_) { throw new Error('The Games service returned an unreadable response.'); }
    if (!r.ok || data.ok === false) throw new Error(data.error || data.why || 'The Games service could not load this data.');
    return data;
  }
  function showError(target, message) {
    target.replaceChildren(el('p', message, 'notice notice-error'));
  }
  function renderTabs() {
    playersEl.replaceChildren();
    const tabs = players.map(p => ({ key: p.key, name: p.name }));
    tabs.push({ key: 'research', name: 'Research' });
    tabs.forEach(function (p, i) {
      const b = el('button', p.name, 'player-tab');
      b.type = 'button'; b.id = 'player-tab-' + i; b.setAttribute('role', 'tab');
      b.setAttribute('aria-selected', String(p.key === selected));
      b.setAttribute('aria-controls', 'library'); b.tabIndex = p.key === selected ? 0 : -1;
      b.addEventListener('click', function () { p.key === 'research' ? selectResearch() : selectPlayer(p.key); });
      b.addEventListener('keydown', function (event) {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        event.preventDefault();
        const next = (i + (event.key === 'ArrowRight' ? 1 : players.length - 1)) % players.length;
        playersEl.children[next].focus();
      });
      playersEl.appendChild(b);
    });
  }
  function row(label, value) {
    const r = el('div', null, 'detail-row'); r.append(el('dt', label), el('dd', value)); return r;
  }
  function section(title, content) {
    const s = el('section', null, 'ih-card data-card');
    s.append(el('h2', title));
    if (Array.isArray(content) && content.length) {
      const dl = el('dl', null, 'detail-list'); content.forEach(function (x) { dl.appendChild(x); }); s.appendChild(dl);
    } else if (content instanceof Node) s.appendChild(content);
    else s.appendChild(el('p', Array.isArray(content) ? 'No entries.' : (content || 'No entries.'), 'ih-muted'));
    return s;
  }
  function dataTable(columns, rows) {
    if (!rows || !rows.length) return el('p', 'No entries.', 'ih-muted');
    const wrap = el('div', null, 'table-wrap'), table = el('table');
    const head = el('thead'), tr = el('tr'); columns.forEach(function (c) { tr.appendChild(el('th', c.label, null)); });
    head.appendChild(tr); table.appendChild(head);
    const body = el('tbody');
    rows.forEach(function (item) {
      const line = el('tr'); columns.forEach(function (c) { line.appendChild(el('td', c.value(item))); }); body.appendChild(line);
    });
    table.appendChild(body); wrap.appendChild(table); return wrap;
  }
  function detailsContent(payload) {
    const data = payload.details || {};
    const wrap = el('div', null, 'details-content');
    if (data.error) wrap.appendChild(el('p', data.error, 'notice notice-warning'));
    if (data.stale) wrap.appendChild(el('p', 'Showing the last saved Destiny data; the provider has not refreshed successfully.', 'notice notice-warning'));
    if (data.fetched) wrap.appendChild(el('p', 'Data refreshed ' + fmtDate(data.fetched), 'ih-muted'));
    if (data.resets) wrap.appendChild(section('Reset schedule', data.resets.map(function (x) { return row(x.kind === 'weekly' ? 'Weekly reset' : 'Daily reset', x.when + ' · ' + x.in); })));
    if (data.characters) wrap.appendChild(section('Characters', dataTable([
      { label: 'Class', value: x => x.class }, { label: 'Power', value: x => x.power == null ? 'Unknown' : x.power },
      { label: 'Hours', value: x => x.hours }, { label: 'Last played', value: x => fmtDate(x.last) }
    ], data.characters)));
    if (data.milestones) wrap.appendChild(section('All milestones (' + data.milestones.length + ')', dataTable([
      { label: 'Character', value: x => x.class }, { label: 'Milestone', value: x => x.name }, { label: 'Progress', value: x => x.progress }
    ], data.milestones)));
    if (data.open_quests) wrap.appendChild(section('Open quests (' + data.open_quests.length + ')', dataTable([
      { label: 'Quest', value: x => x.name }, { label: 'Maps', value: x => (x.maps || []).join(', ') || 'Any map' }
    ], data.open_quests)));
    if (data.needs) wrap.appendChild(section('Needs and materials (' + data.needs.length + ')', dataTable([
      { label: 'Item', value: x => x.item }, { label: 'Now', value: x => x.now }, { label: 'Later', value: x => x.later },
      { label: 'Have', value: x => x.have }, { label: 'Status', value: x => x.status }, { label: 'For', value: x => x.for }
    ], data.needs)));
    if (data.maps) wrap.appendChild(section('Quest maps (' + data.maps.length + ')', dataTable([
      { label: 'Map', value: x => x.map }, { label: 'Quests', value: x => x.count }, { label: 'Quest names', value: x => (x.quests || []).join(', ') }
    ], data.maps)));
    if (data.inventory) wrap.appendChild(section('Inventory advice (' + data.inventory.length + ')', dataTable([
      { label: 'Item', value: x => x.item }, { label: 'Surplus', value: x => x.surplus }, { label: 'Advice', value: x => x.action }, { label: 'Value', value: x => x.gain }
    ], data.inventory)));
    if (data.summary) wrap.appendChild(section('Summary', data.summary));
    if (data.upcoming) wrap.appendChild(section('Coming up', dataTable([
      { label: 'When', value: x => fmtDate(x.when) }, { label: 'Event', value: x => x.text }
    ], data.upcoming)));
    if (!wrap.childElementCount) wrap.appendChild(el('p', 'No game-specific details are available for this game yet.', 'ih-muted'));
    return wrap;
  }
  function gameCard(game, player) {
    const card = el('article', null, 'ih-card game-card');
    const top = el('div', null, 'game-heading');
    const title = el('div'); title.append(el('p', game.platform || 'GAME', 'ih-eyebrow'), el('h2', game.title));
    top.appendChild(title); card.appendChild(top);
    const artRow = el('div', null, 'art-row');
    const art = el('img', null, 'game-art'); art.alt = 'Official cover art for ' + game.title; art.loading = 'lazy'; art.hidden = true;
    const artControls = el('div', null, 'art-controls');
    const artButton = el('button', artSearchAvailable ? 'Find official box art' : 'Art search not configured', 'ih-button art-button');
    artButton.type = 'button'; artButton.disabled = !artSearchAvailable;
    const artStatus = el('p', artSearchAvailable ? 'Search uses the IH AI Layer provider quota.' : 'An administrator must configure the scoped AI Layer connection.', 'ih-muted art-status');
    function useArtwork(metadata) {
      art.alt = 'Official cover art for ' + game.title;
      art.hidden = false;
      art.src = root + 'api/art/' + encodeURIComponent(game.id) + '?v=' + Date.now();
      artButton.textContent = 'Refresh official box art';
      artStatus.replaceChildren(el('span', 'Using saved art from ' + (metadata.source_title || 'an official source') + '. '));
      const source = el('a', 'Source'); source.href = metadata.source_url; source.target = '_blank'; source.rel = 'noopener noreferrer'; artStatus.appendChild(source);
    }
    art.addEventListener('load', function () { art.hidden = false; });
    art.addEventListener('error', function () {
      art.hidden = true;
      artStatus.textContent = 'Saved box art could not be loaded. Try refreshing it.';
    });
    request('api/art-meta/' + encodeURIComponent(game.id)).then(function (saved) {
      if (saved.found && saved.artwork) useArtwork(saved.artwork);
    }).catch(function () {});
    artButton.addEventListener('click', async function () {
      artButton.disabled = true; artButton.textContent = 'Searching official sources…'; artStatus.textContent = 'Asking the IH AI Layer to find publisher or official store art.';
      try {
        const result = await request('api/games/' + encodeURIComponent(game.id) + '/art?player=' + encodeURIComponent(player), 'POST');
        useArtwork(result.artwork);
      } catch (e) { artButton.textContent = 'Try official art search again'; artStatus.textContent = e.message; }
      artButton.disabled = false;
    });
    artControls.append(artButton, artStatus); artRow.append(art, artControls); card.appendChild(artRow);
    const facts = el('dl', null, 'game-facts');
    facts.append(row('Last seen', fmtDate(game.last_seen)), row('Play time', game.playtime_min == null ? 'Not known yet' : Math.floor(game.playtime_min / 60) + ' h ' + (game.playtime_min % 60) + ' min'));
    card.appendChild(facts);
    if (game.tools && game.tools.length) card.appendChild(el('p', 'Tools: ' + game.tools.join(', '), 'ih-muted'));
    const toggle = el('button', 'View full details', 'ih-button details-toggle');
    toggle.type = 'button'; toggle.setAttribute('aria-expanded', 'false');
    const detail = el('div', null, 'game-details'); detail.hidden = true;
    toggle.addEventListener('click', async function () {
      const opening = detail.hidden;
      detail.hidden = !opening; toggle.setAttribute('aria-expanded', String(opening));
      toggle.textContent = opening ? 'Hide details' : 'View full details';
      if (!opening || detail.dataset.loaded) return;
      detail.replaceChildren(el('p', 'Loading complete game details…', 'ih-muted'));
      try {
        const cacheKey = player + '/' + game.id;
        const payload = detailCache.has(cacheKey) ? detailCache.get(cacheKey) : await request('api/games/' + encodeURIComponent(game.id) + '/details?player=' + encodeURIComponent(player));
        detailCache.set(cacheKey, payload); detail.replaceChildren(detailsContent(payload)); detail.dataset.loaded = 'true';
      } catch (e) { showError(detail, e.message); }
    });
    card.append(toggle, detail);
    return card;
  }
  async function selectPlayer(key) {
    selected = key; renderTabs(); libraryEl.setAttribute('aria-busy', 'true');
    libraryEl.replaceChildren(el('p', 'Loading game library…', 'ih-muted'));
    try {
      const data = await request('api/library?player=' + encodeURIComponent(key));
      const wrap = el('div', null, 'library-grid');
      const games = data.games || [];
      const tracked = games.filter(g => Array.isArray(g.tools) && g.tools.length).length;
      const playtimeKnown = games.filter(g => g.playtime != null).length;
      const stats = el('div', null, 'library-stats');
      [[games.length, games.length === 1 ? 'game' : 'games'], [tracked, 'with a tracker'], [playtimeKnown, 'with playtime']].forEach(function (item) {
        const box = el('div', null, 'library-stat'); box.append(el('strong', item[0]), el('span', item[1])); stats.appendChild(box);
      });
      if (!games.length) wrap.appendChild(el('section', 'No games are listed for this player yet.', 'ih-card empty-state'));
      else games.forEach(function (game) { wrap.appendChild(gameCard(game, key)); });
      libraryEl.replaceChildren(el('h2', players.find(p => p.key === key).name + '’s library', 'library-title'), stats, wrap);
    } catch (e) { showError(libraryEl, e.message); }
    libraryEl.setAttribute('aria-busy', 'false');
  }
  async function selectResearch() {
    selected = 'research'; renderTabs(); libraryEl.setAttribute('aria-busy', 'true');
    libraryEl.replaceChildren(el('p', 'Loading Games research…', 'ih-muted'));
    try {
      const data = await request('api/research');
      const items = data.items || [];
      const section = el('section', null, 'ih-card research-card');
      section.append(el('p', 'RESEARCH QUEUE', 'ih-eyebrow'), el('h2', 'Ideas to explore'));
      section.append(el('p', items.length + ' open ' + (items.length === 1 ? 'idea' : 'ideas'), 'research-count'));
      if (!data.available) section.append(el('p', 'The Home Assistant Games to-do list is temporarily unavailable.', 'notice notice-warning'));
      else if (!items.length) section.append(el('p', 'No game research is waiting. IH adds items here when a game needs a tracker.', 'ih-muted'));
      else {
        const list = el('ul', null, 'research-list');
        items.forEach(function (item) {
          const li = el('li'); li.append(el('span', item.what.replace(/^Games:\s*/i, '')),
            el('small', item.due ? 'Due ' + item.due : 'No due date')); list.appendChild(li);
        });
        section.appendChild(list);
      }
      section.append(el('p', 'These items are managed in the Claude to-do list. This view shows open Games research only.', 'ih-muted'));
      libraryEl.replaceChildren(section);
    } catch (e) { showError(libraryEl, e.message); }
    libraryEl.setAttribute('aria-busy', 'false');
  }
  async function start() {
    try {
      const [p, h] = await Promise.all([request('api/players'), request('api/status')]);
      players = p.players || [];
      artSearchAvailable = p.art_search_available === true;
      statusEl.textContent = h.healthy ? 'Games service available' : 'Games service unavailable';
      statusEl.classList.toggle('status-bad', !h.healthy);
      if (!players.length) throw new Error('No players are configured for Games yet.');
      const parts = location.pathname.split('/').filter(Boolean);
      const requested = requestedRoute || (parts.length >= 3 ? parts[parts.length - 1] : '');
      selected = requested === 'research' ? 'research' : players.some(p => p.key === requested) ? requested : players[0].key;
      renderTabs();
      if (selected === 'research') await selectResearch(); else await selectPlayer(selected);
    } catch (e) { statusEl.textContent = 'Could not connect to Games'; showError(libraryEl, e.message); libraryEl.setAttribute('aria-busy', 'false'); }
  }
  start();
}());
