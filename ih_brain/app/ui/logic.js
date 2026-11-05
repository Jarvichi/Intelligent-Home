/* Pure helpers for the Brain page (no DOM). Loaded by the page and by Node tests. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.IHBrainLogic = api;
})(typeof self !== 'undefined' ? self : this, function () {
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const areaOf = (path) => (path.indexOf('/') > -1 ? path.split('/')[0] : '');
  const folderOf = (path) => path.split('/').slice(0, -1).join('/');

  /* Folder view: sub-folders (with note counts) and notes directly inside `prefix` ('' = whole brain). */
  function folderView(notes, prefix) {
    const base = prefix ? prefix + '/' : '';
    const folders = new Map(); const here = [];
    for (const n of notes) {
      if (!n.path.startsWith(base)) continue;
      const rest = n.path.slice(base.length);
      const i = rest.indexOf('/');
      if (i < 0) here.push(n);
      else { const name = rest.slice(0, i); folders.set(name, (folders.get(name) || 0) + 1); }
    }
    return {
      folders: [...folders].map(([name, count]) => ({ name, path: base + name, count }))
        .sort((a, b) => a.name.localeCompare(b.name)),
      notes: here.sort((a, b) => a.title.localeCompare(b.title)),
    };
  }

  /* Top-level areas with counts, notes at the root grouped as '' (shown as "top level"). */
  function areaCards(notes) {
    const counts = new Map();
    for (const n of notes) counts.set(areaOf(n.path), (counts.get(areaOf(n.path)) || 0) + 1);
    return [...counts].map(([path, count]) => ({ path, count, title: path || 'top level' }))
      .sort((a, b) => b.count - a.count || a.title.localeCompare(b.title));
  }

  const pad = (n) => String(n).padStart(2, '0');
  const dayKey = (d) => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  function formatDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
    return m ? Number(m[3]) + ' ' + MONTHS[Number(m[2]) - 1] + ' ' + m[1] : '';
  }
  function dayLabel(d, now) {
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const diff = Math.round((today - day) / 86400000);
    const short = day.getDate() + ' ' + MONTHS[day.getMonth()];
    if (diff === 0) return 'Today, ' + short;
    if (diff === 1) return 'Yesterday, ' + short;
    if (diff > 1 && diff < 7) return DAYS[day.getDay()] + ' ' + short;
    return short + ' ' + day.getFullYear();
  }
  /* Rows (mtime in epoch seconds) -> [{key, label, rows}] newest day first; `now` is injectable for tests. */
  function groupByDay(rows, now) {
    const groups = []; let last = null;
    for (const r of [...rows].sort((a, b) => b.mtime - a.mtime)) {
      const d = new Date(r.mtime * 1000), key = dayKey(d);
      if (!last || last.key !== key) { last = { key, label: dayLabel(d, now), rows: [] }; groups.push(last); }
      last.rows.push(r);
    }
    return groups;
  }

  const searchTerms = (q) => (String(q).match(/[\w-]+/g) || []).filter((t) => t.length > 1).map((t) => t.toLowerCase());
  /* Markdown body -> plain one-line text for snippets (headings and code blocks dropped, link brackets removed). */
  function plainText(text) {
    return String(text || '').replace(/```[\s\S]*?```/g, ' ').replace(/^\s{0,3}#{1,6}\s+.*$/gm, ' ')
      .replace(/\[\[([^\]|]+)\|?([^\]]*)\]\]/g, (m, a, b) => b || a).replace(/[*_`>|#\[\]]/g, '').replace(/\s+/g, ' ').trim();
  }
  /* A short excerpt around the first match: returns segments [{text, hit}] so the page can use textContent. */
  function snippetParts(text, terms, length) {
    const clean = plainText(text); length = length || 170;
    if (!clean) return [];
    const lower = clean.toLowerCase();
    let first = -1;
    for (const t of terms) { const i = lower.indexOf(t); if (i > -1 && (first < 0 || i < first)) first = i; }
    let start = first > 60 ? first - 60 : 0;
    if (start > 0) { const sp = clean.indexOf(' ', start); if (sp > -1 && sp < first) start = sp + 1; }
    let piece = clean.slice(start, start + length);
    const prefix = start > 0 ? '…' : '', suffix = start + length < clean.length ? '…' : '';
    piece = prefix + piece + suffix;
    const parts = []; const lp = piece.toLowerCase();
    let i = 0;
    while (i < piece.length) {
      let best = -1, bl = 0;
      for (const t of terms) { const k = lp.indexOf(t, i); if (k > -1 && (best < 0 || k < best)) { best = k; bl = t.length; } }
      if (best < 0) { parts.push({ text: piece.slice(i), hit: false }); break; }
      if (best > i) parts.push({ text: piece.slice(i, best), hit: false });
      parts.push({ text: piece.slice(best, best + bl), hit: true });
      i = best + bl;
    }
    return parts;
  }

  /* Degree table and ranking for the map. links: [{s,t}]. */
  function degrees(nodes, links) {
    const inn = new Map(), all = new Map();
    for (const n of nodes) { inn.set(n.id, 0); all.set(n.id, 0); }
    for (const l of links) {
      if (inn.has(l.t)) inn.set(l.t, inn.get(l.t) + 1);
      if (all.has(l.s)) all.set(l.s, all.get(l.s) + 1);
      if (all.has(l.t)) all.set(l.t, all.get(l.t) + 1);
    }
    return { inn, all };
  }
  function mostConnected(nodes, links, limit) {
    const d = degrees(nodes, links);
    return nodes.map((n) => ({ id: n.id, title: n.t || n.id, area: n.g || '', links: d.all.get(n.id), linkedFrom: d.inn.get(n.id) }))
      .sort((a, b) => b.links - a.links || a.title.localeCompare(b.title)).slice(0, limit || 30);
  }
  /* Which map labels to draw. `rel` = current zoom divided by the zoom when the map was fitted to the screen:
     only the few most connected notes at first, more as the user zooms in. */
  function showLabel(rank, rel) {
    if (rel >= 7) return true;
    if (rel >= 4) return rank < 150;
    if (rel >= 2.5) return rank < 45;
    if (rel >= 1.6) return rank < 25;
    return rank < 6;
  }
  function filterGraph(nodes, links, area) {
    if (!area) return { nodes, links };
    const ids = new Set(nodes.filter((n) => (n.g || '') === area).map((n) => n.id));
    return { nodes: nodes.filter((n) => ids.has(n.id)), links: links.filter((l) => ids.has(l.s) && ids.has(l.t)) };
  }
  return { areaOf, folderOf, folderView, areaCards, formatDate, dayLabel, groupByDay, searchTerms, plainText, snippetParts,
           degrees, mostConnected, showLabel, filterGraph };
});
