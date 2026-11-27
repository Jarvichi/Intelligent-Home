/* Read-only page: only GET requests, relative URLs, text set with textContent. */
(() => {
  const $ = (id) => document.getElementById(id);
  const get = (path) => fetch(path, { headers: { Accept: 'application/json' }, cache: 'no-store' })
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
  const gbp = (p) => '£' + (p / 100).toFixed(2);
  const cell = (text, cls) => { const td = document.createElement('td'); td.textContent = text; if (cls) td.className = cls; return td; };
  const fill = (id, rows, cols, emptyText) => {
    const body = document.querySelector('#' + id + ' tbody');
    body.textContent = '';
    if (!rows.length) {
      const tr = document.createElement('tr'); const td = cell(emptyText, 'empty'); td.colSpan = cols; tr.appendChild(td); body.appendChild(tr); return;
    }
    for (const cells of rows) {
      const tr = document.createElement('tr');
      for (const c of cells) tr.appendChild(cell(c[0], c[1]));
      body.appendChild(tr);
    }
  };
  const pct = (v) => (v > 0 ? '+' : '') + v.toFixed(1) + '%';
  const dayWord = (n) => (n < 0 ? Math.abs(n) + ' d overdue' : n === 0 ? 'today' : 'in ' + n + ' d');

  function loadSummary() {
    return get('api/summary').then((s) => {
      const m = $('mode');
      m.textContent = s.mode === 'dry' ? 'Test mode' : 'Live';
      m.setAttribute('status', s.mode === 'dry' ? 'warn' : 'ok');
      $('note').textContent = s.mode === 'dry' ? 'Test mode: receipts are only checked, nothing is stored, so lists may be empty.' : '';
      $('m-items').firstChild.textContent = String(s.items);
      $('m-receipts').firstChild.textContent = String(s.receipts);
      $('m-prices').firstChild.textContent = String(s.price_changes);
    });
  }
  const loadReceipts = () => get('api/receipts').then((d) => fill('t-receipts', d.receipts.map((r) => [[r.bought_on], [r.shop], [gbp(r.total_p), 'num']]), 3, 'No receipts yet'));
  const loadPrices = () => get('api/prices').then((d) => fill('t-prices', d.changes.map((c) => [[c.name], [gbp(c.first_p), 'num'], [gbp(c.last_p), 'num'], [pct(c.pct), 'num ' + (c.pct > 0 ? 'up' : 'down')]]), 4, 'No price changes'));
  const loadDue = () => get('api/due').then((d) => fill('t-due', d.due.map((x) => [[x.name], [x.last], [x.due + ' (' + dayWord(x.in_days) + ')', x.in_days < 0 ? 'overdue' : '']]), 3, 'Nothing due soon'));
  const loadLibrary = () => get('api/library?q=' + encodeURIComponent($('q').value.trim())).then((d) => fill('t-library', d.items.map((i) => [[i.name], [String(i.times_bought), 'num'], [i.last_bought || '–']]), 3, 'No matching items'));

  let timer = null;
  $('q').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => loadLibrary().catch(() => {}), 250); });
  $('search').addEventListener('submit', (e) => { e.preventDefault(); loadLibrary().catch(() => {}); });
  Promise.all([loadSummary(), loadReceipts(), loadPrices(), loadDue(), loadLibrary()]).catch(() => {
    const m = $('mode'); m.textContent = 'Not reachable'; m.setAttribute('status', 'error');
  });
})();
