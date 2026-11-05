(() => {
  const $ = (s) => document.querySelector(s);
  const L = window.IHBrainLogic;
  const results = $('#results'), reader = $('#note'), area = $('#area'), workspace = $('.workspace');
  const PAGE = 30;
  let view = 'search', notes = [], selected = '', visibleRows = PAGE, allNotes = [], summary = null;
  let searchRows = null, recentRows = null, browsePrefix = '-', noteHistory = [];
  let listRequest = 0, noteRequest = 0, mapRequest = 0, listScroll = 0, pageScroll = 0;
  let reading = false, fromMap = false;
  let graphInstance = null, graphResizeObserver = null, graphData = null, graphArea = '', fitK = 1, labelBoxes = [];
  const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const mobile = () => window.matchMedia('(max-width: 760px)').matches;
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const h = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = text; return e; };
  async function api(path) {
    const res = await fetch(new URL(`api/${path}`, location.href), {headers:{'Accept':'application/json'}, credentials:'same-origin'});
    if (!res.ok) throw new Error(res.status === 503 ? 'Brain service is unavailable.' : `Request failed (${res.status}).`);
    return res.json();
  }
  const errorBox = (msg, retry) => {
    const box = h('div', 'state bad'); box.setAttribute('role', 'alert');
    box.append(h('b', '', msg));
    if (retry) { const b = h('button', 'btn primary', 'Try again'); b.type = 'button'; b.addEventListener('click', retry); box.append(b); }
    return box;
  };
  const placeholder = (text, bold) => { const s = h('div', 'state'); if (bold) s.append(h('b', '', bold)); s.append(document.createTextNode(text)); return s; };

  /* ---------- navigation bar (shared <ih-nav>) ---------- */
  function updateNav() {
    if (!window.IHNav) return;
    if (reading && (mobile() || fromMap)) {
      const title = reader.querySelector('h2.note-title')?.textContent || 'Note';
      IHNav.setTrail([{label: fromMap ? 'Map' : 'Brain', onClick: backToList}], title);
    } else IHNav.setTrail([], 'Brain');
  }
  function setReading(on, map) {
    reading = on; fromMap = on && !!map;
    $('#back-to-list').textContent = fromMap ? '‹ Back to the map' : '‹ Back to results';
    workspace.dataset.reading = String(on); document.body.dataset.reading = String(on);
    if (fromMap) workspace.dataset.from = 'map'; else delete workspace.dataset.from;
    updateNav();
  }
  function syncTabs() { document.querySelectorAll('[role=tab]').forEach(t => { const on = t.dataset.view === view; t.setAttribute('aria-selected', String(on)); t.tabIndex = on ? 0 : -1; }); }

  /* ---------- rows ---------- */
  const areaLabel = (path) => { const p = path.split('/').slice(0, -1); return p.length ? p.join(' / ') : 'top level'; };
  function noteButton(n, opts = {}) {
    const b = h('button', 'note-row res'); b.type = 'button'; b.dataset.path = n.path; b.setAttribute('aria-current', String(n.path === selected));
    b.append(h('strong', 't', n.title));
    if (opts.snippet) {
      const sn = h('span', 'sn');
      for (const part of L.snippetParts(opts.snippet, opts.terms || [])) sn.append(part.hit ? h('mark', '', part.text) : document.createTextNode(part.text));
      if (sn.textContent) b.append(sn);
    }
    const row = h('span', 'row2');
    if (!opts.hideArea) row.append(h('span', 'chip', areaLabel(n.path)));
    if (n.status && opts.status) row.append(h('span', 'chip', n.status));
    if (n.private) row.append(h('span', 'chip private', 'private'));
    const date = L.formatDate(n.date || n.updated); if (date) row.append(h('span', '', date));
    b.append(row);
    return b;
  }
  const inArea = (n) => !area.value || n.path.startsWith(area.value + '/');
  function moreButton(left) {
    const more = h('button', 'show-more', `Show ${Math.min(PAGE, left)} more · ${left} remaining`); more.type = 'button'; more.dataset.more = 'true'; return more;
  }
  function setCount(text) { $('#result-count').textContent = text; }

  /* ---------- views ---------- */
  function renderSummary() {
    const box = h('div');
    if (!summary) { box.append(placeholder('The counts could not be read. Search still works.')); return box; }
    const card = h('div', 'card');
    card.append(h('h2', '', "What's in the brain"), h('p', 'sub', 'Checked just now from the notes folder.'));
    const tiles = h('div', 'tiles');
    const tile = (n, label, warn) => { const t = h('div', 'tile' + (warn ? ' warn' : '')); t.append(h('b', '', String(n)), h('span', '', label)); tiles.append(t); };
    tile(summary.notes, 'Notes'); tile(summary.links, 'Links between notes'); tile(summary.areas, 'Areas');
    tile(summary.changed_7d, 'Changed in the last 7 days'); tile(summary.drafts, 'Drafts', true); tile(summary.orphans, 'Orphans: nothing links to them', true);
    card.append(tiles);
    card.append(h('p', 'meta', `${summary.private} notes are marked private (share: false). ${summary.shareable} are marked shareable. ${summary.unmarked} are not marked.`));
    card.append(h('p', 'meta', summary.orphan_rule || ''));
    box.append(card, h('p', 'meta', 'Type words above to search every note. Tap a note to read it.'));
    return box;
  }
  function renderRows(rows, opts = {}, append = false) {
    if (!append) { notes = rows; visibleRows = PAGE; }
    const filtered = rows.filter(inArea), shown = filtered.slice(0, visibleRows);
    results.replaceChildren();
    if (!filtered.length) { setCount(''); results.append(placeholder(opts.empty || 'Try fewer or different words, or another area.', opts.emptyTitle || 'No matching notes')); return; }
    setCount(opts.count ? opts.count(filtered.length) : `${filtered.length} notes`);
    if (opts.group) {
      let day = null, box = null;
      const groups = L.groupByDay(shown, new Date());
      for (const g of groups) { results.append(h('p', 'day', g.label)); box = h('div', 'list'); for (const n of g.rows) box.append(noteButton(n, {hideArea: false, snippet: n.excerpt, terms: []})); results.append(box); }
    } else {
      const list = h('div', 'list'); for (const n of shown) list.append(noteButton(n, {snippet: n.excerpt, terms: opts.terms})); results.append(list);
    }
    if (shown.length < filtered.length) results.append(moreButton(filtered.length - shown.length));
  }
  function renderSearch() {
    $('#filters').hidden = searchRows === null;
    if (searchRows === null) { setCount(''); results.replaceChildren(renderSummary()); return; }
    const q = $('#query').value.trim(), terms = L.searchTerms(q);
    renderRows(searchRows, {terms, emptyTitle: `Nothing matches "${q}"`, count: (n) => `${n} ${n === 1 ? 'note matches' : 'notes match'} "${q}"`});
  }
  function renderRecent() {
    $('#filters').hidden = false;
    if (recentRows === null) return;
    renderRows(recentRows, {group: true, emptyTitle: 'No notes in this area', empty: 'Pick another area.', count: (n) => `${n} notes, newest first.`});
  }
  function renderBrowse() {
    $('#filters').hidden = true; setCount('');
    results.replaceChildren();
    const cards = L.areaCards(allNotes), top = browsePrefix.split('/')[0];
    const grid = h('div', 'areas'); grid.setAttribute('aria-label', 'Areas');
    for (const a of cards) {
      const b = h('button', 'area'); b.type = 'button'; b.dataset.browse = a.path || '/';
      b.setAttribute('aria-pressed', String(browsePrefix !== '-' && a.path === top));
      b.append(h('b', '', a.title), h('span', '', `${a.count} ${a.count === 1 ? 'note' : 'notes'}`)); grid.append(b);
    }
    results.append(h('p', 'day', 'Areas'), grid);
    if (browsePrefix === '-') { if (!cards.length) results.append(placeholder('Notes appear here once the Brain has some.', 'No notes yet')); else results.append(h('p', 'meta', 'Pick an area to see its folders and notes.')); return; }
    // The 'top level' pseudo-area is the root note list.
    const prefix = browsePrefix === '/' ? '' : browsePrefix;
    const crumbs = h('p', 'crumbs'); const parts = prefix ? prefix.split('/') : [];
    const all = h('button', '', browsePrefix === '/' ? 'all areas › top level' : 'all areas'); all.type = 'button'; all.dataset.browse = '-'; crumbs.append(all);
    parts.forEach((p, i) => { crumbs.append(document.createTextNode(' › ')); if (i === parts.length - 1) crumbs.append(h('b', '', p)); else { const b = h('button', '', p); b.type = 'button'; b.dataset.browse = parts.slice(0, i + 1).join('/'); crumbs.append(b); } });
    results.append(crumbs);
    const fv = L.folderView(allNotes, prefix);
    if (browsePrefix === '/') fv.folders = [];
    if (fv.folders.length) {
      results.append(h('p', 'day', prefix ? `Folders in ${prefix.split('/').pop()}` : 'Folders'));
      const list = h('div', 'list');
      for (const f of fv.folders) { const b = h('button', 'drow'); b.type = 'button'; b.dataset.browse = f.path; const g = h('span', 'g'); g.append(h('b', '', f.name), h('small', '', `${f.count} ${f.count === 1 ? 'note' : 'notes'}`)); b.append(g, h('span', 'n', '›')); list.append(b); }
      results.append(list);
    }
    notes = fv.notes;
    if (fv.notes.length) {
      results.append(h('p', 'day', prefix ? `Notes in ${prefix.split('/').pop()}` : 'Notes at the top level'));
      const list = h('div', 'list'); for (const n of fv.notes.slice(0, visibleRows)) list.append(noteButton(n, {hideArea: true, status: true})); results.append(list);
      if (fv.notes.length > visibleRows) results.append(moreButton(fv.notes.length - visibleRows));
    } else if (!fv.folders.length) results.append(placeholder('Pick another area.', 'No notes in this area'));
  }
  function renderCurrent() {
    if (view === 'search') renderSearch(); else if (view === 'recent') renderRecent(); else if (view === 'browse') renderBrowse();
  }

  /* ---------- reader ---------- */
  function cancelNoteRequest() { ++noteRequest; reader.removeAttribute('aria-busy'); reader.querySelector('.note-pending')?.remove(); }
  function resolveNote(target, currentPath) {
    let text; try { text = decodeURIComponent(target).trim(); } catch (_) { return null; }
    const normalize = path => { const parts = []; for (const part of path.split('/')) { if (!part || part === '.') continue; if (part === '..') { if (!parts.length) return null; parts.pop(); } else parts.push(part); } return parts.join('/'); };
    const parent = currentPath.split('/').slice(0, -1).join('/');
    const candidates = [normalize(parent + '/' + text), normalize(text)];
    for (const value of candidates) {
      if (!value) continue;
      const path = /\.md$/i.test(value) ? value : value + '.md', row = allNotes.find(note => note.path.toLowerCase() === path.toLowerCase());
      if (row) return row.path;
    }
    const key = text.replace(/\.md$/i, '').toLowerCase();
    const matches = allNotes.filter(note => note.title.toLowerCase() === key || note.path.split('/').pop().replace(/\.md$/i, '').toLowerCase() === key);
    return matches.length === 1 ? matches[0].path : null;
  }
  function linkChips(title, rows) {
    const block = h('div', 'links-block'); block.append(h('p', 'day', title));
    const chips = h('div', 'chips');
    const add = (r) => { const a = h('a', 'chip', r.title); a.href = '#note'; a.dataset.notePath = r.path; return a; };
    const LIMIT = 12;
    rows.slice(0, LIMIT).forEach(r => chips.append(add(r)));
    if (rows.length > LIMIT) {
      const more = h('button', 'chip', `Show ${rows.length - LIMIT} more`); more.type = 'button';
      more.addEventListener('click', () => { more.remove(); rows.slice(LIMIT).forEach(r => chips.append(add(r))); });
      chips.append(more);
    }
    block.append(chips); return block;
  }
  async function loadLinks(path, requestId) {
    try {
      const d = await api(`links?path=${encodeURIComponent(path)}`);
      if (requestId !== noteRequest || selected !== path) return;
      reader.querySelector('.note-links')?.remove();
      const wrap = h('div', 'note-links');
      if (d.out && d.out.length) wrap.append(linkChips('Links in this note', d.out));
      if (d.in && d.in.length) wrap.append(linkChips('Linked from (backlinks)', d.in));
      const source = reader.querySelector('.note-source');
      if (wrap.childNodes.length) reader.insertBefore(wrap, source);
    } catch (_) { /* links are extra; the note itself is already shown */ }
  }
  function renderNote(n, requestId) {
    selected = n.path;
    reader.replaceChildren();
    reader.append(h('p', 'path', areaLabel(n.path).replace(/ \/ /g, ' › ')));
    reader.append(h('h2', 'note-title', n.title));
    const row = allNotes.find(x => x.path === n.path) || {};
    const chips = h('div', 'chips');
    const status = n.status || row.status; if (status) chips.append(h('span', 'chip', status));
    const date = L.formatDate(n.date || row.date || row.updated); if (date) chips.append(h('span', 'chip', 'updated ' + date));
    if (n.private || row.private) chips.append(h('span', 'chip private', 'private'));
    reader.append(chips);
    if (noteHistory.length) { const back = h('button', 'previous-note', '‹ Previous note'); back.type = 'button'; back.dataset.previousNote = 'true'; reader.append(back); }
    reader.append(window.IHBrainMarkdown.render(n.content, {title:n.title, resolveNote:target => resolveNote(target, n.path)}));
    const source = h('details', 'note-source'); source.append(h('summary', '', 'Exact source (Markdown)'));
    const pre = h('pre'); pre.textContent = n.content; source.append(pre); reader.append(source);
    results.querySelectorAll('.note-row').forEach((b) => b.setAttribute('aria-current', String(b.dataset.path === selected)));
    setReading(true, fromMap);
    if (mobile()) window.scrollTo({top:0, behavior:'auto'});
    loadLinks(n.path, requestId);
  }
  async function fetchNote(path, {remember = false, back = false, heading = ''} = {}) {
    const requestId = ++noteRequest;
    reader.querySelector('.note-pending')?.remove(); reader.setAttribute('aria-busy', 'true');
    const pending = h('p', 'note-pending', 'Opening note…'); pending.setAttribute('role', 'status'); reader.prepend(pending);
    try {
      const d = await api(`read?path=${encodeURIComponent(path)}`); if (requestId !== noteRequest) return;
      if (!back) { if (remember && selected && selected !== path) noteHistory.push(selected); else if (!remember) noteHistory = []; }
      renderNote(d, requestId);
      if (heading) reader.querySelector('[id="' + CSS.escape(heading) + '"]')?.scrollIntoView({block:'start', behavior:'auto'});
    } catch (e) {
      if (requestId !== noteRequest) return;
      reader.querySelector('.note-error')?.remove();
      const error = h('div', 'note-error', 'Could not open this note. '); error.setAttribute('role', 'alert');
      const retry = h('button', 'previous-note', 'Try again'); retry.type = 'button'; retry.onclick = () => fetchNote(path, {remember, back, heading}); error.append(retry); reader.prepend(error);
    } finally { if (requestId === noteRequest) { reader.removeAttribute('aria-busy'); reader.querySelector('.note-pending')?.remove(); } }
  }
  reader.addEventListener('click', e => {
    const previous = e.target.closest('[data-previous-note]');
    if (previous) { const path = noteHistory.pop(); if (path) fetchNote(path, {back:true}); return; }
    const link = e.target.closest('a[data-note-path]'); if (!link) return;
    e.preventDefault(); fetchNote(link.dataset.notePath, {remember:true, heading:link.dataset.noteHeading || ''});
  });
  function openNote(path, map) {
    listScroll = results.scrollTop; pageScroll = window.scrollY;
    setReading(true, map);
    if (map) { workspace.dataset.view = 'map'; hideMap(); }
    return fetchNote(path);
  }
  function backToList() {
    cancelNoteRequest();
    if (fromMap) { setReading(false); showMap(); return; }
    setReading(false);
    const row = [...results.querySelectorAll('[data-path]')].find(b => b.dataset.path === selected);
    if (row) row.focus({preventScroll:true});
    results.scrollTop = listScroll; window.scrollTo({top:pageScroll, behavior:'auto'});
  }
  $('#back-to-list').addEventListener('click', backToList);

  /* ---------- list loading and search ---------- */
  async function loadRecent() {
    const requestId = ++listRequest; cancelNoteRequest();
    results.replaceChildren(h('div', 'skel'), h('div', 'skel')); results.firstChild.className = 'skel';
    try {
      const data = await api('recent');
      if (requestId !== listRequest) return;
      recentRows = data.notes || []; renderRecent();
    } catch (e) { if (requestId !== listRequest) return; setCount(''); results.replaceChildren(errorBox(e.message, loadRecent)); }
  }
  async function search() {
    const q = $('#query').value.trim();
    $('#clear').hidden = !$('#query').value;
    if (view !== 'search') setView('search', {render:false});
    if (!q) { ++listRequest; searchRows = null; renderSearch(); return; }
    const requestId = ++listRequest; cancelNoteRequest(); setReading(false);
    results.replaceChildren(h('ih-loading', '', 'Searching every note…')); setCount('');
    try {
      const d = await api(`search?q=${encodeURIComponent(q)}`);
      if (requestId !== listRequest) return;
      searchRows = d.notes || []; renderSearch();
    } catch (e) { if (requestId !== listRequest) return; results.replaceChildren(errorBox(e.message, search)); }
  }
  results.addEventListener('click', (e) => {
    if (e.target.closest('[data-more]')) { visibleRows += PAGE; if (view === 'browse') renderBrowse(); else renderRows(notes, view === 'recent' ? {group: true, count: (n) => `${n} notes, newest first.`} : {terms: L.searchTerms($('#query').value)}, true); return; }
    const nav = e.target.closest('[data-browse]');
    if (nav) { browsePrefix = nav.dataset.browse; visibleRows = PAGE; renderBrowse(); return; }
    const b = e.target.closest('[data-path]'); if (!b) return;
    openNote(b.dataset.path, false);
  });
  $('#search').addEventListener('click', search);
  $('#query').addEventListener('keydown', (e) => { if (e.key === 'Enter') search(); });
  $('#query').addEventListener('input', () => { $('#clear').hidden = !$('#query').value; });
  $('#clear').addEventListener('click', () => { $('#query').value = ''; $('#clear').hidden = true; search(); $('#query').focus(); });
  area.addEventListener('change', () => { $('#map-area').value = area.value; visibleRows = PAGE; renderCurrent(); });

  /* ---------- views and tabs ---------- */
  function setView(next, {render = true} = {}) {
    ++listRequest; cancelNoteRequest(); setReading(false);
    view = next; document.body.dataset.view = next; workspace.dataset.view = next; syncTabs();
    $('#searchbar').hidden = !(next === 'search' || next === 'browse');
    $('#filters').hidden = next === 'browse' || (next === 'search' && searchRows === null);
    if (next === 'map') { showMap(); return; }
    hideMap();
    if (!render) return;
    visibleRows = PAGE;
    if (next === 'browse' && $('#query').value) { $('#query').value = ''; $('#clear').hidden = true; searchRows = null; }
    if (next === 'recent') { if (recentRows === null) loadRecent(); else renderRecent(); }
    else renderCurrent();
  }
  document.querySelector('ih-tabs').addEventListener('click', (e) => {
    const b = e.target.closest('[data-view]'); if (!b) return;
    setView(b.dataset.view);
  });
  document.querySelector('ih-tabs').addEventListener('keydown', e => {
    const tabs = [...document.querySelectorAll('[role=tab]')], i = tabs.indexOf(e.target);
    if (i < 0 || !['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) return;
    e.preventDefault(); const next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length;
    tabs[next].focus(); tabs[next].click();
  });

  /* ---------- map ---------- */
  const COLORS = ['#7ea2ff','#f5a524','#30a46c','#e5484d','#b07cff','#3aa6d8','#e87bd0','#9bd34a','#ffd166','#8d99ae','#06d6a0','#ff8c42'];
  function destroyGraph() {
    if (graphResizeObserver) { graphResizeObserver.disconnect(); graphResizeObserver = null; }
    if (graphInstance) { if (graphInstance._destructor) graphInstance._destructor(); else if (graphInstance.pauseAnimation) graphInstance.pauseAnimation(); graphInstance = null; }
    $('#brain-map').replaceChildren();
  }
  function hideMap() { $('#mapview').hidden = true; if (graphInstance && graphInstance.pauseAnimation) graphInstance.pauseAnimation(); }
  function showMap() {
    $('#mapview').hidden = false; document.body.dataset.view = 'map'; view = 'map'; workspace.dataset.view = 'map'; syncTabs();
    if (graphInstance) { graphInstance.resumeAnimation && graphInstance.resumeAnimation(); sizeGraph(); return; }
    loadMap();
  }
  function sizeGraph() { const box = $('#brain-map'); if (graphInstance && box.clientWidth && box.clientHeight) graphInstance.width(box.clientWidth).height(box.clientHeight); }
  function mapMessage(text, retry) {
    const m = $('#map-msg'); m.replaceChildren();
    if (text) { m.append(document.createTextNode(text)); if (retry) { const b = h('button', 'btn primary', 'Try again'); b.type = 'button'; b.addEventListener('click', retry); m.append(document.createElement('br'), b); } }
  }
  function fillSheet() {
    const d = L.filterGraph(graphData.nodes, graphData.links, graphArea);
    const top = L.mostConnected(d.nodes, d.links, 30), list = $('#sheet-list'); list.replaceChildren();
    $('#sheet-sub').textContent = `${d.links.length} links between ${d.nodes.length} notes. Tap one to read it.`;
    for (const t of top) {
      const b = h('button', 'drow'); b.type = 'button'; b.dataset.mapPath = t.id;
      const g = h('span', 'g'); g.append(h('b', '', t.title), h('small', '', t.area || 'top level')); b.append(g, h('span', 'n', String(t.links))); list.append(b);
    }
    if (!top.length) list.append(placeholder('There are no links to list yet.', 'Nothing here'));
  }
  async function loadMap() {
    const requestId = ++mapRequest; mapMessage('Mapping note connections…'); destroyGraph();
    try {
      if (!graphData) { const d = await api('graph'); if (requestId !== mapRequest) return; graphData = {nodes: d.nodes || [], links: d.links || []}; }
      const groups = [...new Set(graphData.nodes.map(n => n.g || ''))].sort(), sel = $('#map-area');
      if (sel.options.length <= 1) for (const g of groups) if (g) { const o = h('option', '', g); o.value = g; sel.append(o); }
      fillSheet();
      if (!graphData.nodes.length) { mapMessage('The Brain has no notes to map yet.'); return; }
      buildGraph(requestId);
    } catch (e) { if (requestId !== mapRequest) return; mapMessage(e.message || 'The map could not be drawn.', loadMap); }
  }
  function buildGraph(requestId) {
    destroyGraph();
    if (!window.ForceGraph) { mapMessage('The map picture could not load. The list of most connected notes still works.'); openSheet(true); return; }
    const d = L.filterGraph(graphData.nodes, graphData.links, graphArea), deg = L.degrees(d.nodes, d.links).all;
    const names = [...new Set(graphData.nodes.map(n => n.g || ''))].sort(), palette = new Map(names.map((n, i) => [n, COLORS[i % COLORS.length]]));
    const key = $('#map-keypanel'); key.replaceChildren();
    for (const n of names) { const s = h('span'), i = document.createElement('i'); i.style.backgroundColor = palette.get(n); s.append(i, document.createTextNode(n || 'top level')); key.append(s); }
    const nodes = d.nodes.map(n => ({...n, name: String(n.t || n.id), group: n.g || '', degree: deg.get(n.id) || 0})).sort((a, b) => b.degree - a.degree || a.id.localeCompare(b.id));
    nodes.forEach((n, i) => { n.rank = i; });
    $('#map-stat').textContent = `${nodes.length} notes · ${d.links.length} links`;
    if (!nodes.length) { mapMessage('No notes in this area.'); return; }
    mapMessage('');
    const theme = getComputedStyle(document.documentElement), bg = theme.getPropertyValue('--ih-bg').trim(), line = theme.getPropertyValue('--ih-line').trim(), text = theme.getPropertyValue('--ih-text').trim();
    const box = $('#brain-map');
    graphInstance = window.ForceGraph()(box)
      .width(box.clientWidth || window.innerWidth).height(box.clientHeight || window.innerHeight)
      .backgroundColor(bg).linkColor(() => line).linkWidth(0.6)
      .enableNodeDrag(false).warmupTicks(220).cooldownTicks(0)
      .nodeLabel(() => '').onRenderFramePre(() => { labelBoxes = []; })
      .nodeCanvasObjectMode(() => 'replace')
      .nodeCanvasObject((n, ctx, scale) => {
        const r = Math.max(Math.min(2 + Math.sqrt(n.degree) * 0.75, 8), 3 / scale);
        ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fillStyle = palette.get(n.group); ctx.fill();
        if (!L.showLabel(n.rank, scale / fitK)) return;
        const label = n.name.length > 26 ? n.name.slice(0, 25) + '…' : n.name, size = 12 / scale;
        ctx.font = `600 ${size}px system-ui, sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        const half = ctx.measureText(label).width / 2, top = n.y + r, box = [n.x - half, top, n.x + half, top + size * 1.2];
        if (labelBoxes.some(o => box[0] < o[2] && box[2] > o[0] && box[1] < o[3] && box[3] > o[1])) return;
        labelBoxes.push(box);
        ctx.lineWidth = 3 / scale; ctx.strokeStyle = bg; ctx.strokeText(label, n.x, n.y + r + 1 / scale);
        ctx.fillStyle = text; ctx.fillText(label, n.x, n.y + r + 1 / scale);
      })
      .nodePointerAreaPaint((n, color, ctx, scale) => { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, Math.max(2 + Math.sqrt(n.degree) * 0.75, 14 / scale), 0, Math.PI * 2); ctx.fill(); })
      .onNodeClick((n) => openNote(n.id, true))
      .graphData({nodes, links: d.links.map(l => ({source: l.s, target: l.t}))});
    graphInstance.d3Force('charge').strength(-18);
    graphInstance.d3Force('link').distance(22);
    let tries = 0;
    const firstFit = () => {
      if (requestId !== mapRequest || !graphInstance) return;
      const placed = graphInstance.graphData().nodes.some(n => isFinite(n.x));
      if (placed || tries > 60) fitGraph(0); else { tries++; requestAnimationFrame(firstFit); }
    };
    requestAnimationFrame(firstFit);
    if (window.ResizeObserver) { graphResizeObserver = new ResizeObserver(sizeGraph); graphResizeObserver.observe(box); }
  }
  /* Fit to the middle 98% of nodes (isolated strays stay reachable by panning), inside the free space between the floating controls. */
  function fitGraph(ms) {
    if (!graphInstance) return;
    const box = $('#brain-map'), w = box.clientWidth, hgt = box.clientHeight;
    const nodes = graphInstance.graphData().nodes.filter(n => isFinite(n.x) && isFinite(n.y));
    if (!nodes.length || !w || !hgt) return;
    const span = (key) => { const v = nodes.map(n => n[key]).sort((a, b) => a - b); return [v[Math.floor(v.length * 0.01)], v[Math.ceil(v.length * 0.99) - 1]]; };
    const [x0, x1] = span('x'), [y0, y1] = span('y');
    const top = Math.min($('#map-top').getBoundingClientRect().bottom + 10, hgt / 2), bottom = 70, side = 20;
    const k = Math.max(0.04, Math.min(8, Math.min((w - 2 * side) / Math.max(x1 - x0, 1), (hgt - top - bottom) / Math.max(y1 - y0, 1))));
    graphInstance.centerAt((x0 + x1) / 2, (y0 + y1) / 2 - ((top - bottom) / 2) / k, ms);
    graphInstance.zoom(k, ms); fitK = k;
  }
  function openSheet(on) { $('#map-sheet').hidden = !on; $('#map-list').setAttribute('aria-expanded', String(on)); $('#map-list').textContent = on ? 'Hide list' : 'Top notes'; if (on) { $('#map-keypanel').hidden = true; $('#map-key').setAttribute('aria-expanded', 'false'); } }
  $('#map-list').addEventListener('click', () => openSheet($('#map-sheet').hidden));
  $('#map-key').addEventListener('click', () => { const on = $('#map-keypanel').hidden; $('#map-keypanel').hidden = !on; $('#map-key').setAttribute('aria-expanded', String(on)); });
  $('#sheet-list').addEventListener('click', (e) => { const b = e.target.closest('[data-map-path]'); if (b) { openSheet(false); openNote(b.dataset.mapPath, true); } });
  $('#map-area').addEventListener('change', () => { graphArea = $('#map-area').value; area.value = graphArea; if (graphData) { fillSheet(); buildGraph(mapRequest); } });
  $('#map-ctl').querySelectorAll('[data-zoom]').forEach((button) => button.addEventListener('click', () => {
    if (!graphInstance) return;
    if (button.dataset.zoom === 'fit') fitGraph(350);
    else graphInstance.zoom(Math.max(.1, Math.min(14, graphInstance.zoom() * (button.dataset.zoom === 'in' ? 1.5 : 1 / 1.5))), 250);
  }));

  /* ---------- start ---------- */
  (async () => {
    const health = $('#health'), healthText = $('#health-text');
    try {
      const [d, s] = await Promise.all([api('index'), api('summary').catch(() => null)]);
      allNotes = d.notes; summary = s;
      health.dataset.ok = 'true'; healthText.textContent = 'Connected';
      $('#count').textContent = `${d.count} notes · read-only through this page`;
      $('#query').placeholder = `Search ${d.count} notes`;
      for (const a of d.areas) if (a.path) { const o = h('option', '', `${a.title} (${a.count})`); o.value = a.path; area.append(o); }
      renderCurrent();
    } catch (e) {
      health.dataset.ok = 'false'; healthText.textContent = 'Not reachable'; $('#count').textContent = e.message;
      results.replaceChildren(errorBox(e.message, () => location.reload()));
    }
  })();
  syncTabs(); updateNav();
  const media = window.matchMedia('(max-width: 760px)');
  if (media.addEventListener) media.addEventListener('change', updateNav); else if (media.addListener) media.addListener(updateNav);
})();
