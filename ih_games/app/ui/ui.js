(function () {
  'use strict';
  const root = location.pathname.endsWith('/') ? location.pathname : location.pathname.replace(/[^/]*$/, '');
  const playersEl = document.getElementById('players');
  const researchEl = document.getElementById('research');
  const libraryEl = document.getElementById('library');
  const statusEl = document.getElementById('service-status');
  let players = [];
  let selected = '';
  let requestedRoute = '';
  let artSearchAvailable = false;
  const detailCache = new Map();
  const expandedCards = new Set();
  const libraryPreferences = new Map();
  let viewRequest = 0;
  const gameComponentCache = new Map();

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
  function fmtDate(value, dateOnly) {
    if (!value) return 'Not recorded';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString(undefined, dateOnly ? { day: 'numeric', month: 'short', year: 'numeric' } : { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
  function fmtPlaytime(value) {
    if (value == null || !Number.isFinite(Number(value)) || Number(value) < 0) return 'Unavailable';
    const minutes = Math.floor(Number(value));
    const hours = Math.floor(minutes / 60);
    const remainder = minutes % 60;
    if (!hours) return minutes.toLocaleString() + ' min';
    return hours.toLocaleString() + (hours === 1 ? ' hour' : ' hours') + (remainder ? ' ' + remainder + ' min' : '');
  }
  function filteredGames(games, preferences) {
    const query = preferences.query.trim().toLocaleLowerCase();
    const result = games.filter(game => String(game.title || '').toLocaleLowerCase().includes(query) &&
      (!preferences.platform || (game.platform || 'Other') === preferences.platform));
    const titleOrder = (a, b) => String(a.title).localeCompare(String(b.title));
    const played = value => { const date = Date.parse(value); return Number.isFinite(date) ? date : -Infinity; };
    const playtime = value => value == null || !Number.isFinite(Number(value)) ? -Infinity : Number(value);
    result.sort((a, b) => {
      if (preferences.sort === 'title') return titleOrder(a, b);
      const av = preferences.sort === 'playtime' ? playtime(a.playtime_min) : played(a.last_seen);
      const bv = preferences.sort === 'playtime' ? playtime(b.playtime_min) : played(b.last_seen);
      return av === bv ? titleOrder(a, b) : av > bv ? -1 : 1;
    });
    return result;
  }
  function researchItem(item) {
    const text = String(item.what || '').replace(/^Games:\s*/i, '').trim();
    const tracker = text.match(/^research ways to track progress\/help for (.+)$/i);
    const recommendation = text.match(/^(?:recommend|recommendation|suggest)[:\s]+(.+)$/i);
    return tracker ? { title: tracker[1], kind: 'Tracker research', question: 'How can progress and useful game information be tracked?', next: 'Next: review tracker options; no tracker is promised yet.' } :
      recommendation ? { title: recommendation[1], kind: 'Recommendation', question: text, next: 'Next: review whether this belongs in the library.' } :
      { title: text || 'Game research', kind: 'Research', question: 'Review this research request.', next: 'Next: clarify the request and decide what to investigate.' };
  }
  function clockTime(ms) {
    const d = new Date(ms); const two = function (n) { return String(n).padStart(2, '0'); };
    return two(d.getHours()) + ':' + two(d.getMinutes());
  }
  async function request(path, method, payload) {
    const r = await fetch(root + path, { cache: 'no-store', method: method || 'GET', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: method === 'POST' ? JSON.stringify(payload || {}) : undefined });
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
    researchEl.setAttribute('aria-pressed', String(selected === 'research'));
    tabs.forEach(function (p, i) {
      const b = el('button', p.name, 'player-tab');
      b.type = 'button'; b.id = 'player-tab-' + i; b.setAttribute('role', 'tab');
      b.setAttribute('aria-selected', String(p.key === selected));
      b.setAttribute('aria-controls', 'library'); b.tabIndex = p.key === selected || (selected === 'research' && i === 0) ? 0 : -1;
      b.addEventListener('click', function () { selectPlayer(p.key); });
      b.addEventListener('keydown', function (event) {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (i + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length;
        playersEl.children[next].focus();
      });
      playersEl.appendChild(b);
    });
  }
  function row(label, value) {
    const r = el('div', null, 'detail-row'); r.append(el('dt', label), el('dd', value)); return r;
  }
  function section(title, content) {
    const s = el('ih-card', null, 'data-card');
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
      const line = el('tr'); columns.forEach(function (c) { const cell = el('td', c.value(item)); cell.setAttribute('data-label', c.label); line.appendChild(cell); }); body.appendChild(line);
    });
    table.appendChild(body); wrap.appendChild(table); return wrap;
  }
  function loadGameComponent(name) {
    if (!/^[a-z0-9_-]{1,64}$/.test(name || '')) return Promise.reject(new Error('Invalid game UI component.'));
    if (gameComponentCache.has(name)) return gameComponentCache.get(name);
    const pending = new Promise(function (resolve, reject) {
      const script = document.createElement('script');
      script.src = root + 'games/' + name + '.js?v=5'; script.async = true;
      script.onload = function () {
        const component = window.IHGameComponents && window.IHGameComponents[name];
        component && typeof component.render === 'function' ? resolve(component) : reject(new Error('The game UI component did not register.'));
      };
      script.onerror = function () { reject(new Error('The game UI component could not be loaded.')); };
      document.head.appendChild(script);
    });
    gameComponentCache.set(name, pending);
    return pending;
  }
  async function detailsContent(payload, ctx) {
    const data = payload.details || {};
    if (payload.ui_component) {
      try {
        const component = await loadGameComponent(payload.ui_component);
        const gameId = payload.game_id || payload.game;
        const base = 'api/games/' + encodeURIComponent(gameId);
        const player = ctx && ctx.player;
        const helpers = { el, section, row, dataTable, fmtDate, gameId };
        if (player) {
          helpers.command = function (cmd, args) { return request(base + '/cmd?player=' + encodeURIComponent(player), 'POST', { cmd: cmd, args: args || [] }); };
          helpers.reload = async function () {
            const fresh = await request(base + '/details?player=' + encodeURIComponent(player));
            const saved = Date.now(); detailCache.set(ctx.key, { payload: fresh, saved: saved }); if (ctx.onFetched) ctx.onFetched(saved);
            return fresh.details || {};
          };
        }
        return component.render(data, helpers);
      } catch (error) {
        return el('p', error.message, 'notice notice-warning');
      }
    }
    const wrap = el('div', null, 'details-content ih-detail-stack');
    if (data.error) wrap.appendChild(el('p', data.error, 'notice notice-warning'));
    if (data.stale) wrap.appendChild(el('p', 'Showing the last saved game data; the provider has not refreshed successfully.', 'notice notice-warning'));
    if (data.fetched) wrap.appendChild(el('p', 'Data refreshed ' + fmtDate(data.fetched), 'data-refreshed'));
    if (data.summary) wrap.appendChild(section('Summary', data.summary));
    if (Array.isArray(data.upcoming)) {
      if (data.upcoming.length) wrap.appendChild(section('Coming up', dataTable([
        { label: 'When', value: x => fmtDate(x.when) }, { label: 'Event', value: x => x.text }
      ], data.upcoming)));
      else wrap.appendChild(el('p', 'No upcoming events.', 'upcoming-empty'));
    }
    if (!wrap.childElementCount) wrap.appendChild(el('p', 'No game-specific details are available for this game yet.', 'ih-muted'));
    return wrap;
  }
  function gameCard(game, player) {
    const card = el('ih-card', null, 'game-card');
    const expansionKey = JSON.stringify([player, game.id]);
    card.setAttribute('role', 'article'); card.setAttribute('aria-label', game.title);
    const cover = el('div', null, 'game-cover');
    const fallback = el('div', null, 'game-cover-fallback');
    fallback.append(el('span', 'GAME LIBRARY', 'cover-kicker'), el('strong', game.title.split(/\s+/).map(x => x[0]).join('').slice(0, 3).toUpperCase(), 'cover-monogram'));
    const art = el('img', null, 'game-art'); art.alt = 'Cover art for ' + game.title; art.loading = 'lazy'; art.hidden = true;
    cover.append(fallback, art);
    const artButton = el('button', null, 'art-action');
    artButton.type = 'button'; artButton.disabled = !artSearchAvailable;
    function artIcon(mode) {
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', mode === 'refresh' ? 'M20 7v5h-5M4 17v-5h5m-3.5-2A7 7 0 0 1 18 7l2 5M4 12l2 5a7 7 0 0 0 12.5-1' : 'M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16Zm6-2 4 4');
      path.setAttribute('fill', 'none'); path.setAttribute('stroke', 'currentColor'); path.setAttribute('stroke-width', '2'); path.setAttribute('stroke-linecap', 'round'); path.setAttribute('stroke-linejoin', 'round');
      svg.appendChild(path); return svg;
    }
    function setArtAction(mode, label) {
      const action = label || (mode === 'refresh' ? 'Refresh' : 'Find') + ' cover art for ' + game.title;
      artButton.replaceChildren(artIcon(mode)); artButton.setAttribute('aria-label', action); artButton.title = action;
    }
    setArtAction('search', artSearchAvailable ? 'Find cover art for ' + game.title : 'Cover art search is not configured');
    cover.appendChild(artButton);

    const main = el('div', null, 'game-main');
    const top = el('div', null, 'game-heading');
    top.append(el('span', game.platform || 'GAME', 'platform-pill'), el('h2', game.title));
    main.appendChild(top);
    const facts = el('dl', null, 'game-facts');
    facts.append(row('Last seen', fmtDate(game.last_seen, true)), row('Play time', fmtPlaytime(game.playtime_min)));
    main.appendChild(facts);
    let artStatus = null;
    function showArtStatus(message, className) {
      if (!artStatus) {
        artStatus = el('p', null, 'ih-muted art-status');
        main.insertBefore(artStatus, facts.nextSibling);
      }
      artStatus.className = className || 'ih-muted art-status';
      artStatus.textContent = message;
    }
    function useArtwork() {
      art.alt = 'Cover art for ' + game.title;
      art.hidden = false;
      fallback.hidden = true;
      cover.classList.add('has-art');
      art.src = root + 'api/art/' + encodeURIComponent(game.id) + '?v=' + Date.now();
      setArtAction('refresh');
    }
    art.addEventListener('load', function () {
      art.hidden = false; fallback.hidden = true; cover.classList.add('has-art');
    });
    art.addEventListener('error', function () {
      art.hidden = true;
      fallback.hidden = false;
      cover.classList.remove('has-art');
      showArtStatus('Saved cover art could not be loaded. Try refreshing it.', 'notice notice-warning art-status');
    });
    request('api/art-meta/' + encodeURIComponent(game.id)).then(function (saved) {
      if (saved.found) useArtwork();
    }).catch(function () {});
    artButton.addEventListener('click', async function () {
      artButton.disabled = true; artButton.setAttribute('aria-busy', 'true'); showArtStatus('Searching for cover art…');
      try {
        await request('api/games/' + encodeURIComponent(game.id) + '/art?player=' + encodeURIComponent(player), 'POST');
        useArtwork();
        if (artStatus) { artStatus.remove(); artStatus = null; }
      } catch (e) { setArtAction(art.hidden ? 'search' : 'refresh'); showArtStatus(e.message, 'notice notice-error art-status'); }
      artButton.disabled = !artSearchAvailable; artButton.removeAttribute('aria-busy');
    });
    if (game.tools && game.tools.length) main.appendChild(el('p', 'Trackers · ' + game.tools.map(name => name.replace(/[_-]+/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase())).join(', '), 'game-tools'));
    const toggle = el('button', null, 'details-toggle ih-disclosure');
    toggle.type = 'button'; toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-label', 'Show details for ' + game.title); toggle.title = 'Show details';
    const detail = el('div', null, 'game-details'); detail.hidden = true;
    const refreshed = el('span', null, 'refreshed-at');
    function showRefreshed(ms) { refreshed.textContent = 'refreshed ' + clockTime(ms); }
    const savedDetails = detailCache.get(expansionKey);
    if (savedDetails) showRefreshed(savedDetails.saved);
    const detailId = 'game-details-' + encodeURIComponent(player) + '-' + encodeURIComponent(game.id);
    detail.id = detailId; toggle.setAttribute('aria-controls', detailId);
    let detailLoading = false;
    async function setExpanded(opening) {
      detail.hidden = !opening; toggle.setAttribute('aria-expanded', String(opening));
      toggle.setAttribute('aria-label', (opening ? 'Hide' : 'Show') + ' details for ' + game.title);
      toggle.title = opening ? 'Hide details' : 'Show details';
      if (opening) expandedCards.add(expansionKey); else expandedCards.delete(expansionKey);
      if (!opening || detail.dataset.loaded || detailLoading) return;
      detailLoading = true;
      detail.replaceChildren(el('p', 'Loading complete game details…', 'ih-muted'));
      detail.setAttribute('aria-busy', 'true');
      try {
        const cached = detailCache.get(expansionKey);
        const payload = cached && Date.now() - cached.saved < 60000 ? cached.payload : await request('api/games/' + encodeURIComponent(game.id) + '/details?player=' + encodeURIComponent(player));
        detailCache.set(expansionKey, { payload, saved: Date.now() });
        showRefreshed(detailCache.get(expansionKey).saved);
        detail.replaceChildren(await detailsContent(payload, { player: player, key: expansionKey, onFetched: showRefreshed })); detail.dataset.loaded = 'true';
      } catch (e) {
        showError(detail, e.message);
      } finally { detailLoading = false; detail.setAttribute('aria-busy', 'false'); }
    }
    toggle.addEventListener('click', function () { return setExpanded(detail.hidden); });
    const summary = el('div', null, 'game-summary');
    summary.append(cover, main, refreshed, toggle);
    card.append(summary, detail);
    if (expandedCards.has(expansionKey)) setExpanded(true);
    return card;
  }
  async function selectPlayer(key) {
    const requestId = ++viewRequest;
    selected = key; renderTabs(); libraryEl.setAttribute('aria-busy', 'true');
    libraryEl.replaceChildren(el('p', 'Loading game library…', 'ih-muted'));
    try {
      const data = await request('api/library?player=' + encodeURIComponent(key));
      const wrap = el('div', null, 'library-grid ih-card-grid');
      if (requestId !== viewRequest) return;
      const games = data.games || [];
      const tracked = games.filter(g => Array.isArray(g.tools) && g.tools.length).length;
      const playtimeKnown = games.filter(g => g.playtime_min != null).length;
      const stats = el('div', null, 'library-stats');
      [[games.length, games.length === 1 ? 'game' : 'games'], [tracked, 'tracked'], [playtimeKnown, 'with playtime']].forEach(function (item) {
        const box = el('ih-metric', null, 'library-stat'); box.append(el('strong', item[0]), el('span', item[1])); stats.appendChild(box);
      });
      const preferences = libraryPreferences.get(key) || { query: '', platform: '', sort: 'recent' };
      libraryPreferences.set(key, preferences);
      const toolbar = el('div', null, 'library-toolbar ih-toolbar');
      const searchLabel = el('label', 'Search games', 'search-field');
      const search = el('input'); search.type = 'search'; search.value = preferences.query;
      search.placeholder = 'Game title'; searchLabel.appendChild(search);
      const platformLabel = el('label', 'Platform'); const platform = el('select');
      const platforms = [...new Set(games.map(game => game.platform || 'Other'))].sort();
      [['', 'All platforms'], ...platforms.map(value => [value, value])].forEach(([value, label]) => {
        const option = el('option', label); option.value = value; platform.appendChild(option);
      });
      platform.value = preferences.platform; platformLabel.appendChild(platform);
      const sortLabel = el('label', 'Sort by'); const sort = el('select');
      [['recent', 'Last played'], ['title', 'Title'], ['playtime', 'Playtime']].forEach(([value, label]) => {
        const option = el('option', label); option.value = value; sort.appendChild(option);
      });
      sort.value = preferences.sort; sortLabel.appendChild(sort);
      const clear = el('button', 'Clear filters', 'ih-button'); clear.type = 'button';
      toolbar.append(searchLabel, platformLabel, sortLabel, clear);
      const count = el('p', null, 'library-result-count'); count.setAttribute('role', 'status');
      function renderGames() {
        const visible = filteredGames(games, preferences);
        wrap.replaceChildren();
        count.textContent = visible.length + ' of ' + games.length + ' games';
        clear.disabled = !preferences.query && !preferences.platform;
        if (!games.length) wrap.appendChild(el('ih-empty', 'No games are listed for this player yet.', 'empty-state'));
        else if (!visible.length) wrap.appendChild(el('p', 'No games match these filters. Clear filters to see the full library.', 'empty-state'));
        else visible.forEach(game => wrap.appendChild(gameCard(game, key)));
      }
      search.addEventListener('input', function () { preferences.query = search.value; renderGames(); });
      platform.addEventListener('change', function () { preferences.platform = platform.value; renderGames(); });
      sort.addEventListener('change', function () { preferences.sort = sort.value; renderGames(); });
      clear.addEventListener('click', function () {
        preferences.query = ''; preferences.platform = ''; search.value = ''; platform.value = ''; renderGames(); search.focus();
      });
      renderGames();
      libraryEl.replaceChildren(el('h2', players.find(p => p.key === key).name + '’s library', 'visually-hidden'), stats, toolbar, count, wrap);
    } catch (e) { if (requestId === viewRequest) showError(libraryEl, e.message); }
    if (requestId === viewRequest) libraryEl.setAttribute('aria-busy', 'false');
  }
  async function selectResearch() {
    const requestId = ++viewRequest;
    selected = 'research'; renderTabs(); libraryEl.setAttribute('aria-busy', 'true');
    libraryEl.replaceChildren(el('p', 'Loading Games research…', 'ih-muted'));
    try {
      const data = await request('api/research');
      if (requestId !== viewRequest) return;
      const items = data.items || [];
      const section = el('ih-card', null, 'research-card');
      section.append(el('p', 'RESEARCH QUEUE', 'ih-kicker'), el('h2', 'Ideas to explore'));
      section.append(el('p', data.available ? items.length + ' ' + (items.length === 1 ? 'request' : 'requests') + ' listed for review' : 'Research status unavailable', 'research-count'));
      if (!data.available) section.append(el('p', 'The Home Assistant Games to-do list is temporarily unavailable.', 'notice notice-warning'));
      else if (!items.length) section.append(el('p', 'No game research is waiting. Tracker requests and game recommendations will appear here.', 'ih-muted'));
      else {
        const list = el('ul', null, 'research-list');
        items.forEach(function (item) {
          const info = researchItem(item);
          const li = el('li'); const content = el('div', null, 'research-item-main');
          content.append(el('small', info.kind, 'research-kind'), el('h3', info.title), el('p', info.question), el('p', 'Status: listed for review · investigation status not reported', 'research-status'), el('p', info.next));
          li.append(content, el('small', item.due ? 'Due ' + item.due : 'No due date')); list.appendChild(li);
        });
        section.appendChild(list);
      }
      section.append(el('p', 'These items are managed in the Claude to-do list. This read-only view does not report whether investigation has started.', 'ih-muted'));
      libraryEl.replaceChildren(section);
    } catch (e) { if (requestId === viewRequest) showError(libraryEl, e.message); }
    if (requestId === viewRequest) libraryEl.setAttribute('aria-busy', 'false');
  }
  async function start() {
    try {
      const [p, h] = await Promise.all([request('api/players'), request('api/status')]);
      players = p.players || [];
      artSearchAvailable = p.art_search_available === true;
      statusEl.textContent = h.healthy ? 'Service online' : 'Service unavailable';
      statusEl.classList.toggle('status-bad', !h.healthy);
      statusEl.setAttribute('status', h.healthy ? 'ok' : 'error');
      if (!players.length) throw new Error('No players are configured for Games yet.');
      const parts = location.pathname.split('/').filter(Boolean);
      const requested = requestedRoute || (parts.length >= 3 ? parts[parts.length - 1] : '');
      selected = requested === 'research' ? 'research' : players.some(p => p.key === requested) ? requested : players[0].key;
      renderTabs();
      if (selected === 'research') await selectResearch(); else await selectPlayer(selected);
    } catch (e) { statusEl.setAttribute('status', 'error'); statusEl.textContent = 'Could not connect to Games'; showError(libraryEl, e.message); libraryEl.setAttribute('aria-busy', 'false'); }
  }
  researchEl.addEventListener('click', selectResearch);
  start();
}());
