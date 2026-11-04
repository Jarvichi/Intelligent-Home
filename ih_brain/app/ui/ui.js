(() => {
  const $ = (s) => document.querySelector(s);
  const results = $('#results'), reader = $('#note'), area = $('#area');
  let view = 'browse', notes = [], selected = '', visibleRows = 30;
  let listRequest = 0, noteRequest = 0, listScroll = 0, pageScroll = 0;
  const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function syncTabs() { document.querySelectorAll('[role=tab]').forEach(t => { const active = t.dataset.view === (view === 'search' ? 'browse' : view); t.setAttribute('aria-selected', String(active)); t.tabIndex = active ? 0 : -1; }); }
  let graphInstance = null, graphResizeObserver = null, graphResizeHandler = null;
  const rowPageSize = 30;
  const mobileList = () => window.matchMedia('(max-width: 760px)').matches;
  document.querySelector('.workspace').dataset.view = view;
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function api(path) {
    const res = await fetch(new URL(`api/${path}`, location.href), {headers:{'Accept':'application/json'}, credentials:'same-origin'});
    if (!res.ok) throw new Error(res.status === 503 ? 'Brain service is unavailable.' : `Request failed (${res.status}).`);
    return res.json();
  }
  function destroyGraph() {
    if (graphResizeObserver) { graphResizeObserver.disconnect(); graphResizeObserver = null; }
    if (graphResizeHandler) { window.removeEventListener('resize', graphResizeHandler); graphResizeHandler = null; }
    if (graphInstance) {
      if (graphInstance._destructor) graphInstance._destructor();
      else if (graphInstance.pauseAnimation) graphInstance.pauseAnimation();
      graphInstance = null;
    }
  }
  function renderRows(rows, append = false) {
    destroyGraph();
    if (!append) { notes = rows; visibleRows = rowPageSize; }
    $('#list-title').textContent = view === 'recent' ? 'Recently changed' : ($('#query').value.trim() ? 'Search results' : 'Notes');
    const filtered = rows.filter(n => !area.value || n.path.startsWith(area.value + '/'));
    const shown = mobileList() ? filtered.slice(0, visibleRows) : filtered;
    $('#result-count').textContent = filtered.length + ' results · ' + shown.length + ' shown';
    if (!filtered.length) { results.innerHTML = '<ih-empty>No matching notes in this view.</ih-empty>'; return; }
    results.innerHTML = shown.map((n) => `<button class="note-row" type="button" data-path="${esc(n.path)}" aria-current="${n.path === selected}"><strong>${esc(n.title)}</strong><small>${esc(n.path)} · ${esc(n.type || 'note')} · ${esc(n.updated || 'date unknown')}</small></button>`).join('');
    if (mobileList() && shown.length < filtered.length) {
      const more = document.createElement('button'); more.type = 'button'; more.className = 'show-more'; more.dataset.more = 'true';
      more.textContent = `Show ${Math.min(rowPageSize, filtered.length - shown.length)} more · ${filtered.length - shown.length} remaining`;
      results.append(more);
    }
  }
  function renderNote(n) {
    selected = n.path;
    reader.innerHTML = `<p class="path">${esc(n.path)}</p><h2>${esc(n.title)}</h2><pre>${esc(n.content)}</pre>`;
    results.querySelectorAll('.note-row').forEach((b) => b.setAttribute('aria-current', String(b.dataset.path === selected)));
    document.querySelector('.workspace').dataset.reading = 'true';
    if (mobileList()) document.querySelector('.reader').scrollIntoView({block:'start', behavior:reducedMotion()?'auto':'smooth'});
  }
  async function loadNotes() {
    const requestId = ++listRequest; ++noteRequest; syncTabs();
    try {
      destroyGraph(); document.querySelector('.workspace').dataset.view = view; document.querySelector('.workspace').dataset.reading = 'false'; results.innerHTML = '<ih-loading>Loading notes…</ih-loading>';
      const suffix = view === 'recent' ? 'recent' : `notes?area=${encodeURIComponent(area.value)}`;
      const data = await api(suffix);
      if (requestId !== listRequest) return;
      renderRows(data.notes || []);
    } catch (e) { if (requestId !== listRequest) return; results.innerHTML = `<ih-error role="alert">${esc(e.message)}</ih-error>`; }
  }
  async function search() {
    const q = $('#query').value.trim();
    if (!q) { view = 'browse'; await loadNotes(); return; }
    const requestId = ++listRequest; ++noteRequest;
    try { view = 'search'; syncTabs(); destroyGraph(); document.querySelector('.workspace').dataset.view = view; document.querySelector('.workspace').dataset.reading = 'false'; results.innerHTML = '<ih-loading>Searching every note…</ih-loading>'; const d = await api(`search?q=${encodeURIComponent(q)}`); if (requestId === listRequest) renderRows(d.notes || []); }
    catch (e) { if (requestId !== listRequest) return; results.innerHTML = `<ih-error role="alert">${esc(e.message)}</ih-error>`; }
  }
  results.addEventListener('click', async (e) => {
    if (e.target.closest('[data-more]')) { visibleRows += rowPageSize; renderRows(notes, true); return; }
    const b = e.target.closest('[data-path]'); if (!b) return;
    listScroll = results.scrollTop; pageScroll = window.scrollY; const requestId = ++noteRequest;
    try { const d = await api(`read?path=${encodeURIComponent(b.dataset.path)}`); if(requestId === noteRequest) renderNote(d); }
    catch (err) { if(requestId !== noteRequest) return; reader.innerHTML = `<ih-error role="alert">${esc(err.message)}</ih-error>`; }
  });
  $('#search').addEventListener('click', search);
  $('#back-to-list').addEventListener('click', () => {
    document.querySelector('.workspace').dataset.reading = 'false';
    ++noteRequest;
    const row = [...results.querySelectorAll('[data-path]')].find(b => b.dataset.path === selected);
    if(row) row.focus({preventScroll:true});
    results.scrollTop = listScroll; window.scrollTo({top:pageScroll, behavior:'auto'});
  });
  $('#query').addEventListener('keydown', (e) => { if (e.key === 'Enter') search(); });
  area.addEventListener('change', () => { if(view === 'graph') { view='browse'; $('#query').value=''; loadNotes(); } else { renderRows(notes); } });
  document.querySelector('ih-tabs').addEventListener('click', (e) => {
    const b = e.target.closest('[data-view]'); if (!b) return;
    ++listRequest; ++noteRequest; view = b.dataset.view; syncTabs(); document.querySelector('.workspace').dataset.view = view; document.querySelector('.workspace').dataset.reading = 'false';
    document.querySelectorAll('[role="tab"]').forEach((t) => t.setAttribute('aria-selected', String(t === b)));
    if (view === 'graph') { renderGraph(); return; }
    $('#query').value = ''; loadNotes();
  });
  async function renderGraph() {
    const requestId = ++listRequest;
    $('#result-count').textContent = '';
    destroyGraph();
    $('#list-title').textContent = 'Knowledge connections';
    results.innerHTML = '<ih-loading>Mapping note connections…</ih-loading>';
    try {
      const d = await api('graph'), nodes = d.nodes || [], links = d.links || [];
      if(requestId !== listRequest) return;
      if (!window.ForceGraph) throw new Error('The local graph renderer could not be loaded.');
      const groupNames = [...new Set(nodes.map((n) => n.g || 'General'))].sort();
      const colors = ['#7ea2ff','#f5a524','#30a46c','#e5484d','#b07cff','#3aa6d8','#e87bd0','#9bd34a','#ffd166','#8d99ae','#06d6a0','#ff8c42'];
      const degree = new Map();
      for (const edge of links) { degree.set(edge.s, (degree.get(edge.s) || 0) + 1); degree.set(edge.t, (degree.get(edge.t) || 0) + 1); }
      const graphNodes = nodes.map((n) => ({...n, name:String(n.t || n.id), group:n.g || 'General', degree:degree.get(n.id) || 0, hub:/(^|\/)index\.md$/i.test(n.id)})).sort((a,b)=>b.degree-a.degree);
      let hubCount=0; for(const node of graphNodes) if(node.hub) node.showLabel=hubCount++<10;
      const graphLinks = links.map((edge) => ({source:edge.s, target:edge.t}));
      results.innerHTML = `<div class="graph-tools"><span>${nodes.length} notes · ${links.length} links · Drag to pan; pinch to zoom; tap a note to read it.</span><div><button type="button" data-zoom="in" aria-label="Zoom in">+</button><button type="button" data-zoom="out" aria-label="Zoom out">−</button><button type="button" data-zoom="fit">Fit</button></div></div><div class="graph-legend" aria-label="Note areas"></div><div class="graph-wrap" id="brain-map" role="application" aria-label="Interactive map of Brain note connections"></div>`;
      const box = results.querySelector('#brain-map');
      const palette = new Map(groupNames.map((name, i) => [name, colors[i % colors.length]]));
      const legend = results.querySelector('.graph-legend');
      for (const name of groupNames) {
        const item = document.createElement('span'), swatch = document.createElement('i');
        swatch.style.backgroundColor = palette.get(name); item.append(swatch, document.createTextNode(name || 'Top level')); legend.append(item);
      }
      const theme = getComputedStyle(document.documentElement);
      const bg = theme.getPropertyValue('--ih-bg').trim();
      const line = theme.getPropertyValue('--ih-line').trim();
      const labelBg = theme.getPropertyValue('--ih-surface').trim();
      const labelText = theme.getPropertyValue('--ih-text').trim();
      let fitted = false;
      graphInstance = window.ForceGraph()(box)
        .graphData({nodes:graphNodes, links:graphLinks})
        .backgroundColor(bg)
        .linkColor(() => line)
        .linkWidth(1)
        .nodeVal((n) => 1 + Math.sqrt(n.degree) * 2)
        .nodeColor((n) => palette.get(n.group))
        .nodeLabel((n) => `${esc(n.name)} · ${esc(n.id)} · ${n.degree} links`)
        .nodeCanvasObjectMode(() => 'replace')
        .nodeCanvasObject((n, ctx, scale) => {
          const radius = Math.min(2.2 + Math.sqrt(n.degree) * 1.1, 16 / scale);
          ctx.beginPath(); ctx.arc(n.x, n.y, radius, 0, Math.PI * 2); ctx.fillStyle = palette.get(n.group); ctx.fill();
          if (!n.showLabel) return;
          const label = n.name.length > 28 ? `${n.name.slice(0, 27)}…` : n.name;
          const size = 11 / scale; ctx.font = `${size}px system-ui, sans-serif`;
          const width = ctx.measureText(label).width, y = n.y + radius + size;
          ctx.fillStyle = labelBg; ctx.fillRect(n.x-width/2-3/scale, y-size, width+6/scale, size*1.35);
          ctx.fillStyle = labelText; ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.fillText(label,n.x,y-size*.88);
        })
        .nodePointerAreaPaint((n, color, ctx, scale) => {
          ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x,n.y,Math.max(2.2+Math.sqrt(n.degree)*1.1,6/scale),0,Math.PI*2); ctx.fill();
        })
        .onNodeClick((n) => openMapNote(n.id))
        .cooldownTicks(200)
        .cooldownTime(6000)
        .onEngineStop(() => { if (!fitted) { fitted = true; graphInstance.zoomToFit(400, 32); } });
      graphInstance.d3Force('charge').strength(-45);
      graphInstance.d3Force('link').distance(28);
      graphInstance.width(box.clientWidth).height(box.clientHeight);
      const resizeGraph = () => {
        if (graphInstance && box.clientWidth && box.clientHeight) graphInstance.width(box.clientWidth).height(box.clientHeight);
      };
      if (window.ResizeObserver) { graphResizeObserver = new ResizeObserver(resizeGraph); graphResizeObserver.observe(box); }
      else { graphResizeHandler = resizeGraph; window.addEventListener('resize', graphResizeHandler); }
      results.querySelectorAll('[data-zoom]').forEach((button) => button.addEventListener('click', () => {
        if (!graphInstance) return;
        if (button.dataset.zoom === 'fit') graphInstance.zoomToFit(350, 32);
        else graphInstance.zoom(Math.max(.2,Math.min(12,graphInstance.zoom()*(button.dataset.zoom==='in'?1.35:1/1.35))),250);
      }));
    } catch(e) { if(requestId !== listRequest) return; destroyGraph(); results.innerHTML=`<ih-error role="alert">${esc(e.message)}</ih-error>`; }
  }
  async function openMapNote(path) {
    const requestId = ++noteRequest;
    try { const d=await api(`read?path=${encodeURIComponent(path)}`); if(requestId === noteRequest) renderNote(d); }
    catch(e) { if(requestId !== noteRequest) return; reader.innerHTML=`<ih-error role="alert">${esc(e.message)}</ih-error>`; }
  }
  (async () => {
    try {
      const d = await api('index');
      $('#health').textContent = 'Connected'; $('#health').setAttribute('data-ok','true');
      $('#count').textContent = `${d.count} notes · read-only through this page`;
      for (const a of d.areas) { const o=document.createElement('option'); o.value=a.path; o.textContent=`${a.title} (${a.count})`; area.append(o); }
      renderRows(d.notes); 
    } catch(e) { $('#health').textContent='Unavailable'; $('#count').textContent=e.message; results.innerHTML=`<ih-error role="alert">${esc(e.message)}</ih-error>`; }
  })();
  syncTabs();
  document.querySelector('ih-tabs').addEventListener('keydown', e => {
    const tabs = [...document.querySelectorAll('[role=tab]')], i = tabs.indexOf(e.target);
    if(i < 0 || !['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) return;
    e.preventDefault(); const next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length-1 : (i+(e.key==='ArrowRight'?1:tabs.length-1))%tabs.length;
    tabs[next].focus(); tabs[next].click();
  });
  const media = window.matchMedia('(max-width: 760px)');
  const rerenderForWidth = () => { if (view !== 'graph' && notes.length) renderRows(notes); };
  if (media.addEventListener) media.addEventListener('change', rerenderForWidth);
  else if (media.addListener) media.addListener(rerenderForWidth);
})();
