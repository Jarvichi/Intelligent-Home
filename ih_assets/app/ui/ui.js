/* ih_assets page: totals, low/high bar chart, item table, add form, barcode box. Talks to api/ on the ingress server.
   No markup is built from text: nodes use createElement and textContent. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var items = [], sortKey = 'name', asc = true, pricing = null, sellAsk = null, sellTimer = null, loaded = false;

  function el(tag, attrs, kids) {
    var n = document.createElement(tag), k;
    for (k in (attrs || {})) {
      if (k === 'text') n.textContent = attrs[k];
      else if (k === 'on') { for (var ev in attrs.on) n.addEventListener(ev, attrs.on[ev]); }
      else if (attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) n.setAttribute(k, attrs[k] === true ? '' : attrs[k]);
    }
    (kids || []).forEach(function (c) { if (c) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function api(method, path, body) {
    return fetch('api' + path, { method: method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined, cache: 'no-store' })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) throw new Error(d.error || ('Error ' + r.status)); return d; }); });
  }
  function say(t) { $('msg').textContent = t || ''; }
  function fail(e) { say(e.message || 'Something went wrong'); }
  function gbp(v) { return v === null || v === undefined ? '-' : '£' + Number(v).toFixed(2); }
  function priced(i) { return i.low !== null && i.high !== null; }
  function status(state, text) { var s = $('service-status'); s.setAttribute('status', state); s.textContent = text; }

  function tiles(t, mode) {
    var box = $('tiles'); clear(box);
    [['Low value', gbp(t.low)], ['High value', gbp(t.high)], ['Items', String(t.count)], ['Not priced', String(t.unpriced)]].forEach(function (x) {
      box.appendChild(el('div', { class: 'a-tile' }, [el('b', { text: x[1] }), el('span', { text: x[0] })]));
    });
    if (t.unpriced) box.appendChild(el('p', { class: 'ih-metadata', text: t.unpriced + ' items have incomplete prices and are excluded from both totals (' + t.missing_low + ' missing low, ' + t.missing_high + ' missing high).' }));
  }

  function chart(h) {
    var s = $('chart'), NS = 'http://www.w3.org/2000/svg';
    clear(s);
    if (!h.length) { $('chart-sum').textContent = 'No history yet. A point is added each day, and whenever you add, price or sell an item.'; return; }
    var mx = Math.max.apply(null, h.map(function (x) { return x.high; }).concat([1])), n = h.length, w = 600 / n, bw = Math.max(1, (w - Math.min(4, w / 4)) / 2);
    h.forEach(function (x, i) {
      [['lo', x.low, i * w], ['hi', x.high, i * w + bw]].forEach(function (b) {
        var hh = b[1] / mx * 180, r = document.createElementNS(NS, 'rect');
        r.setAttribute('class', b[0]); r.setAttribute('x', b[2]); r.setAttribute('y', 200 - hh); r.setAttribute('width', bw); r.setAttribute('height', hh);
        var t = document.createElementNS(NS, 'title'); t.textContent = x.date + ': ' + (b[0] === 'lo' ? 'low ' : 'high ') + gbp(b[1]); r.appendChild(t); s.appendChild(r);
      });
    });
    [['4', '14', 'start', 'Top ' + gbp(mx)], ['4', '216', 'start', h[0].date], ['596', '216', 'end', h[n - 1].date]].forEach(function (a) {
      var t = document.createElementNS(NS, 'text'); t.setAttribute('x', a[0]); t.setAttribute('y', a[1]); t.setAttribute('text-anchor', a[2]); t.textContent = a[3]; s.appendChild(t);
    });
    var last = h[n - 1];
    $('chart-sum').textContent = n + ' day' + (n === 1 ? '' : 's') + ' of history, ' + h[0].date + ' to ' + last.date + '. Latest: low ' + gbp(last.low) + ', high ' + gbp(last.high) + '.' + (n < 3 ? ' A trend shows after a few days.' : '');
  }

  function visible() {
    var q = $('q').value.trim().toLowerCase(), c = $('cat').value, s = $('st').value;
    var L = items.filter(function (i) {
      return (!q || (i.name + ' ' + (i.platform || '')).toLowerCase().indexOf(q) >= 0) && (!c || i.category === c) && (!s || (s === 'p') === priced(i));
    });
    L.sort(function (a, b) {
      var x = a[sortKey], y = b[sortKey];
      if (x === null || x === undefined) x = -1; if (y === null || y === undefined) y = -1;
      if (typeof x === 'string') { x = x.toLowerCase(); y = String(y).toLowerCase(); }
      return (x < y ? -1 : x > y ? 1 : 0) * (asc ? 1 : -1);
    });
    return L;
  }

  function priceRow(i) {
    var lo = el('input', { type: 'number', min: '0', step: '0.01', value: i.low === null ? '' : i.low, id: 'p-lo' });
    var hi = el('input', { type: 'number', min: '0', step: '0.01', value: i.high === null ? '' : i.high, id: 'p-hi' });
    var form = el('form', { class: 'a-price' }, [
      el('label', {}, ['Low (GBP each)', lo]), el('label', {}, ['High (GBP each)', hi]),
      el('button', { class: 'a-button a-primary', type: 'submit', text: 'Save price' }),
      el('button', { class: 'a-button', type: 'button', text: 'Cancel', on: { click: function () { pricing = null; draw(); } } })]);
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      api('PATCH', '/items/' + i.id, { low: lo.value, high: hi.value, checked: new Date().toISOString().slice(0, 10) })
        .then(function () { pricing = null; say('Price saved.'); return load(); }).catch(fail);
    });
    return el('tr', {}, [el('td', { colspan: '6' }, [form])]);
  }

  function draw() {
    var L = visible(), body = $('rows'); clear(body);
    Array.prototype.forEach.call(document.querySelectorAll('.a-sort'), function (b) {
      if (b.getAttribute('data-k') === sortKey) b.parentNode.setAttribute('aria-sort', asc ? 'ascending' : 'descending'); else b.parentNode.removeAttribute('aria-sort');
    });
    L.forEach(function (i) {
      var ask = sellAsk === i.id;
      body.appendChild(el('tr', {}, [
        el('td', {}, [i.name, el('span', { class: 'a-sub', text: i.platform || '' })]),
        el('td', { text: i.category }),
        el('td', { class: 'a-n', text: String(i.qty) }),
        el('td', { class: 'a-n' + (i.low === null ? ' a-un' : ''), text: i.low === null ? 'Not priced' : gbp(i.low) }),
        el('td', { class: 'a-n', text: i.high === null ? 'Not priced' : gbp(i.high) }),
        el('td', {}, [el('div', { class: 'a-actions' }, [
          el('button', { class: 'a-button', type: 'button', 'aria-label': 'Set price for ' + i.name, text: 'Set price', on: { click: function () { pricing = i.id; draw(); var f = $('p-lo'); if (f) f.focus(); } } }),
          el('button', { class: 'a-button', type: 'button', 'aria-label': (ask ? 'Confirm sold: ' : 'Mark sold: ') + i.name, text: ask ? 'Confirm sold' : 'Mark sold', on: { click: function () { sell(i); } } })])])]));
      if (pricing === i.id) body.appendChild(priceRow(i));
    });
    var none = !L.length;
    $('empty').hidden = !none;
    $('empty').textContent = none ? (items.length ? 'No items match these filters.' : 'No items yet. Add one below, or put inventory.json in the add-on import folder (see the add-on documentation).') : '';
    $('count').textContent = loaded ? 'Showing ' + L.length + ' of ' + items.length + ' items.' : '';
  }

  function sell(i) {
    if (sellAsk !== i.id) {
      sellAsk = i.id; draw(); clearTimeout(sellTimer);
      sellTimer = setTimeout(function () { sellAsk = null; draw(); }, 6000);
      say('Press Confirm sold to remove ' + i.name + ' from the totals.');
      return;
    }
    sellAsk = null; clearTimeout(sellTimer);
    api('POST', '/items/' + i.id + '/sell', {}).then(function () { say('Marked sold.'); return load(); }).catch(fail);
  }

  function fillCats(cats) {
    if ($('addcat').options.length) return;
    cats.forEach(function (c) {
      $('addcat').appendChild(el('option', { value: c, text: c }));
      $('bcat').appendChild(el('option', { value: c, text: c }));
      $('cat').appendChild(el('option', { value: c, text: c }));
    });
  }

  function load() {
    return Promise.all([api('GET', '/summary'), api('GET', '/items'), api('GET', '/history')]).then(function (r) {
      items = r[1].items; loaded = true;
      fillCats(r[0].categories); tiles(r[0].totals, r[0].mode); chart(r[2].history); draw();
      status('ok', r[0].mode === 'live' ? 'Publishing sensors' : 'Dry run: no sensors published');
    }).catch(function (e) { status('error', 'Unavailable'); $('count').textContent = ''; fail(e); });
  }

  $('q').addEventListener('input', draw);
  $('cat').addEventListener('change', draw);
  $('st').addEventListener('change', draw);
  $('clear').addEventListener('click', function () { $('q').value = ''; $('cat').value = ''; $('st').value = ''; draw(); });
  Array.prototype.forEach.call(document.querySelectorAll('.a-sort'), function (b) {
    b.addEventListener('click', function () { var k = b.getAttribute('data-k'); asc = sortKey === k ? !asc : true; sortKey = k; draw(); });
  });
  $('add').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var d = {}; new FormData($('add')).forEach(function (v, k) { d[k] = v; });
    var btn = ev.target.querySelector('button'); btn.disabled = true;
    api('POST', '/items', d).then(function () { $('add').reset(); say('Added.'); return load(); }).catch(fail).then(function () { btn.disabled = false; });
  });
  function barcode(add) {
    api('POST', '/barcode', { code: $('bc').value, add: add, category: $('bcat').value }).then(function (r) {
      say(r.name ? (add ? 'Added: ' : 'Found: ') + r.name : r.lookup_failed ? 'Barcode lookup unavailable. Try again or add the item manually below.' : 'No match for that barcode. You can add it manually below.');
      if (add && r.item) { $('bc').value = ''; return load(); }
    }).catch(fail);
  }
  $('bc-find').addEventListener('click', function () { barcode(false); });
  $('bc-add').addEventListener('click', function () { barcode(true); });
  load();
}());
