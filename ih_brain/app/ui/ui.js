(() => {
  const $ = (s) => document.querySelector(s);
  const results = $('#results'), reader = $('#note'), area = $('#area');
  let view = 'browse', notes = [], selected = '';
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function api(path) {
    const res = await fetch(new URL(`api/${path}`, location.href), {headers:{'Accept':'application/json'}, credentials:'same-origin'});
    if (!res.ok) throw new Error(res.status === 503 ? 'Brain service is unavailable.' : `Request failed (${res.status}).`);
    return res.json();
  }
  function renderRows(rows) {
    notes = rows;
    $('#list-title').textContent = view === 'recent' ? 'Recently changed' : ($('#query').value.trim() ? 'Search results' : 'Notes');
    if (!rows.length) { results.innerHTML = '<ih-empty>No matching notes in this view.</ih-empty>'; return; }
    results.innerHTML = rows.map((n) => `<button class="note-row" type="button" data-path="${esc(n.path)}" aria-current="${n.path === selected}"><strong>${esc(n.title)}</strong><small>${esc(n.path)} · ${esc(n.type || 'note')} · ${esc(n.updated || 'date unknown')}</small></button>`).join('');
  }
  function renderNote(n) {
    selected = n.path;
    reader.innerHTML = `<p class="path">${esc(n.path)}</p><h2>${esc(n.title)}</h2><pre>${esc(n.content)}</pre>`;
    results.querySelectorAll('.note-row').forEach((b) => b.setAttribute('aria-current', String(b.dataset.path === selected)));
  }
  async function loadNotes() {
    try {
      results.innerHTML = '<ih-loading>Loading notes…</ih-loading>';
      const suffix = view === 'recent' ? 'recent' : `notes?area=${encodeURIComponent(area.value)}`;
      const data = await api(suffix);
      renderRows(data.notes || []);
    } catch (e) { results.innerHTML = `<ih-error role="alert">${esc(e.message)}</ih-error>`; }
  }
  async function search() {
    const q = $('#query').value.trim();
    if (!q) { view = 'browse'; await loadNotes(); return; }
    try { view = 'search'; results.innerHTML = '<ih-loading>Searching every note…</ih-loading>'; const d = await api(`search?q=${encodeURIComponent(q)}`); renderRows(d.notes || []); }
    catch (e) { results.innerHTML = `<ih-error role="alert">${esc(e.message)}</ih-error>`; }
  }
  results.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-path]'); if (!b) return;
    try { const d = await api(`read?path=${encodeURIComponent(b.dataset.path)}`); renderNote(d); }
    catch (err) { reader.innerHTML = `<ih-error role="alert">${esc(err.message)}</ih-error>`; }
  });
  $('#search').addEventListener('click', search);
  $('#query').addEventListener('keydown', (e) => { if (e.key === 'Enter') search(); });
  area.addEventListener('change', () => { view = 'browse'; loadNotes(); });
  document.querySelector('ih-tabs').addEventListener('click', (e) => {
    const b = e.target.closest('[data-view]'); if (!b) return;
    view = b.dataset.view; document.querySelectorAll('[role="tab"]').forEach((t) => t.setAttribute('aria-selected', String(t === b)));
    if (view === 'graph') { renderGraph(); return; }
    $('#query').value = ''; loadNotes();
  });
  async function renderGraph() {
    $('#list-title').textContent = 'Knowledge connections';
    results.innerHTML = '<ih-loading>Mapping note connections…</ih-loading>';
    try {
      const d = await api('graph'), nodes = d.nodes || [], links = d.links || [];
      const grouped = new Map();
      for (const n of nodes) { const g=n.g||'General'; if(!grouped.has(g))grouped.set(g,[]); grouped.get(g).push(n); }
      const groups = [...grouped.entries()].sort((a,b)=>a[0].localeCompare(b[0]));
      const radius = Math.max(180, groups.length*20), maxLocal = Math.max(90, ...groups.map(([,ns])=>Math.sqrt(ns.length)*9));
      const extent = radius + maxLocal + 90, size = extent*2, center=size/2, positions=new Map();
      const svgNS='http://www.w3.org/2000/svg';
      results.innerHTML = `<div class="graph-tools"><span>${nodes.length} notes · ${links.length} links · drag to move, pinch or zoom to explore, select any dot to read</span><div><button type="button" data-zoom="in" aria-label="Zoom in">+</button><button type="button" data-zoom="out" aria-label="Zoom out">−</button><button type="button" data-zoom="reset">Fit</button></div></div><div class="graph-wrap"><svg id="brain-map" viewBox="0 0 ${size} ${size}" aria-label="Interactive map of every Brain note and link"><g id="map-layer"></g></svg></div>`;
      const svg=results.querySelector('#brain-map'), layer=results.querySelector('#map-layer');
      groups.forEach(([name,ns],gi)=>{
        const theta=groups.length ? (gi/groups.length)*Math.PI*2-Math.PI/2 : 0;
        const gx=center+Math.cos(theta)*radius, gy=center+Math.sin(theta)*radius;
        const group=document.createElementNS(svgNS,'g'); group.setAttribute('class','map-group');
        const label=document.createElementNS(svgNS,'text'); label.setAttribute('x',gx); label.setAttribute('y',gy-maxLocal-16); label.setAttribute('text-anchor','middle'); label.setAttribute('class','map-label'); label.textContent=`${name} · ${ns.length}`; group.append(label); layer.append(group);
        ns.forEach((n,i)=>{
          const a=i*2.399963, r=9+Math.sqrt(i)*8.5, x=gx+Math.cos(a)*r, y=gy+Math.sin(a)*r;
          positions.set(n.id,{x,y});
        });
      });
      for(const edge of links){const a=positions.get(edge.s),b=positions.get(edge.t);if(!a||!b)continue;const line=document.createElementNS(svgNS,'line');line.setAttribute('x1',a.x);line.setAttribute('y1',a.y);line.setAttribute('x2',b.x);line.setAttribute('y2',b.y);line.setAttribute('class','map-link');layer.append(line);}
      for(const n of nodes){const p=positions.get(n.id);if(!p)continue;const c=document.createElementNS(svgNS,'circle');c.setAttribute('cx',p.x);c.setAttribute('cy',p.y);c.setAttribute('r',String(Math.min(10,4+Math.sqrt((n.n||0)/800))));c.setAttribute('class','map-node');c.setAttribute('tabindex','0');c.setAttribute('role','button');c.setAttribute('aria-label',`${n.t}, ${n.id}`);c.dataset.path=n.id;const title=document.createElementNS(svgNS,'title');title.textContent=`${n.t} · ${n.id}`;c.append(title);layer.append(c);}
      let tx=0,ty=0,z=1,drag=null,moved=false,pinch=null; const pointers=new Map();
      const apply=()=>layer.setAttribute('transform',`translate(${tx} ${ty}) scale(${z})`);
      const zoomAt=(next,cx,cy)=>{next=Math.max(.5,Math.min(8,next));tx=cx-(cx-tx)*next/z;ty=cy-(cy-ty)*next/z;z=next;apply();};
      results.querySelectorAll('[data-zoom]').forEach((b)=>b.addEventListener('click',()=>{if(b.dataset.zoom==='reset'){tx=0;ty=0;z=1;}else zoomAt(z*(b.dataset.zoom==='in'?1.35:1/1.35),size/2,size/2);apply();}));
      const point=(e)=>({x:e.clientX,y:e.clientY});
      const distance=(a,b)=>Math.hypot(a.x-b.x,a.y-b.y);
      svg.addEventListener('pointerdown',(e)=>{pointers.set(e.pointerId,point(e));svg.setPointerCapture(e.pointerId);if(pointers.size===2){const ps=[...pointers.values()];pinch={distance:distance(ps[0],ps[1]),zoom:z};drag=null;moved=true;return;}drag={x:e.clientX,y:e.clientY,tx,ty,path:e.target.closest('[data-path]')?.dataset.path||''};moved=false;});
      svg.addEventListener('pointermove',(e)=>{if(!pointers.has(e.pointerId))return;pointers.set(e.pointerId,point(e));if(pointers.size>=2&&pinch){const ps=[...pointers.values()],rect=svg.getBoundingClientRect(),cx=((ps[0].x+ps[1].x)/2-rect.left)*size/rect.width,cy=((ps[0].y+ps[1].y)/2-rect.top)*size/rect.height;zoomAt(pinch.zoom*distance(ps[0],ps[1])/Math.max(pinch.distance,1),cx,cy);return;}if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>4)moved=true;if(moved){const rect=svg.getBoundingClientRect();tx=drag.tx+dx*size/rect.width/z;ty=drag.ty+dy*size/rect.height/z;apply();}});
      const release=(e)=>{if(pointers.size===1&&drag&&!moved){const path=drag.path||e.target.closest('[data-path]')?.dataset.path;if(path)openMapNote(path);}pointers.delete(e.pointerId);drag=null;if(pointers.size<2)pinch=null;};
      svg.addEventListener('pointerup',release);svg.addEventListener('pointercancel',release);
      svg.addEventListener('keydown',(e)=>{if((e.key==='Enter'||e.key===' ')&&e.target.dataset.path){e.preventDefault();openMapNote(e.target.dataset.path);}});
      svg.addEventListener('wheel',(e)=>{e.preventDefault();const rect=svg.getBoundingClientRect(),cx=(e.clientX-rect.left)*size/rect.width,cy=(e.clientY-rect.top)*size/rect.height;zoomAt(z*(e.deltaY<0?1.12:1/1.12),cx,cy);},{passive:false});
    } catch(e) { results.innerHTML=`<ih-error role="alert">${esc(e.message)}</ih-error>`; }
  }
  async function openMapNote(path) {
    try { const d=await api(`read?path=${encodeURIComponent(path)}`); renderNote(d); }
    catch(e) { reader.innerHTML=`<ih-error role="alert">${esc(e.message)}</ih-error>`; }
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
})();
