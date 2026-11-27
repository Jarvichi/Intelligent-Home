/* ih_usage_trace page: chart of session % and week % with event lanes. Talks to api/ on the ingress server (read only).
   No markup is built from text: every node is made with createElement(NS) and textContent. */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var $ = function (id) { return document.getElementById(id); };
  var KNOWN = { 'assistant': 'var(--u-src-assistant)', 'worker': 'var(--u-src-worker)', 'task-channel': 'var(--u-src-task-channel)', 'inbox-sort': 'var(--u-src-inbox-sort)' };
  var SPARE = ['var(--u-src-other)', 'var(--u-src-other2)', 'var(--ih-muted)'];
  var st = { series: [], jumps: [], events: [], sources: [], threshold: 2, mode: '' };

  function el(tag, attrs, kids) {
    var n = document.createElement(tag), k;
    for (k in (attrs || {})) { if (k === 'text') n.textContent = attrs[k]; else if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k]); }
    (kids || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }
  function svg(tag, attrs, text) {
    var n = document.createElementNS(NS, tag), k;
    for (k in (attrs || {})) n.setAttribute(k, attrs[k]);
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function api(path) {
    return fetch('api' + path, { cache: 'no-store' }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) throw new Error(d.error || ('Error ' + r.status)); return d; });
    });
  }
  function fmt(ts) { return new Date(ts * 1000).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }); }
  function len(s) {
    if (s < 90) return Math.round(s) + ' s';
    if (s < 5400) return Math.round(s / 60) + ' min';
    return (s / 3600).toFixed(1) + ' h';
  }
  function colour(source) {
    if (KNOWN[source]) return KNOWN[source];
    var others = st.sources.filter(function (s) { return !KNOWN[s]; });
    return SPARE[Math.min(others.indexOf(source), SPARE.length - 1)] || SPARE[0];
  }
  function period() { return parseInt($('f-period').value, 10) || 0; }
  function matches(e) {
    var q = $('f-search').value.trim().toLowerCase(), s = $('f-source').value;
    if (s && e.source !== s) return false;
    if (q && (e.label + ' ' + e.ref).toLowerCase().indexOf(q) < 0) return false;
    return true;
  }

  function showTip(html, x, y) {
    var tip = $('tip'), box = $('plot').getBoundingClientRect(), host = tip.offsetParent ? tip.offsetParent.getBoundingClientRect() : box;
    clear(tip);
    html.forEach(function (line) { tip.appendChild(el('div', { text: line })); });
    tip.hidden = false;
    var left = Math.min(Math.max(4, x - host.left + 12), Math.max(4, host.width - tip.offsetWidth - 4));
    tip.style.left = left + 'px';
    tip.style.top = Math.max(4, y - host.top + 12) + 'px';
  }
  function hideTip() { $('tip').hidden = true; }

  function legend(lanes) {
    var ul = $('legend');
    clear(ul);
    ul.appendChild(el('li', {}, [el('span', { 'class': 'u-swatch', 'data-k': 'session' }), el('span', { text: 'Session %' })]));
    ul.appendChild(el('li', {}, [el('span', { 'class': 'u-swatch', 'data-k': 'week' }), el('span', { text: 'Week %' })]));
    lanes.forEach(function (s) {
      var sw = el('span', { 'class': 'u-swatch' });
      sw.style.background = colour(s);
      ul.appendChild(el('li', {}, [sw, el('span', { text: s })]));
    });
    ul.appendChild(el('li', {}, [el('span', { 'class': 'u-swatch', 'data-k': 'jump' }), el('span', { text: 'Unattributed jump' })]));
  }

  function draw() {
    var host = $('plot'), now = Math.floor(Date.now() / 1000);
    var evs = st.events.filter(matches);
    var lanes = st.sources.filter(function (s) { return !$('f-source').value || s === $('f-source').value; });
    var from = period() ? now - period() : (st.series.length ? st.series[0].ts : now - 86400);
    var to = now;
    var W = Math.max(320, host.clientWidth || 640), small = W < 520;
    var m = { l: 36, r: 10, t: 10, b: 22 }, ph = small ? 170 : 220, lane = 22, H = m.t + ph + m.b + 8 + lanes.length * lane;
    var pw = W - m.l - m.r;
    var maxv = 10;
    st.series.forEach(function (p) { maxv = Math.max(maxv, p.session || 0, p.week || 0); });
    maxv = Math.min(100, Math.ceil(maxv / 10) * 10);
    var X = function (t) { return m.l + (t - from) / Math.max(1, to - from) * pw; };
    var Y = function (v) { return m.t + ph - v / maxv * ph; };
    var s = svg('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': 'Claude session and week usage percent over time with event bands. The same data is in the tables below.' });
    var g;
    for (g = 0; g <= 4; g++) {
      var v = maxv / 4 * g;
      s.appendChild(svg('line', { 'class': 'u-grid', x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
      s.appendChild(svg('text', { x: m.l - 6, y: Y(v) + 4, 'text-anchor': 'end' }, Math.round(v) + '%'));
    }
    for (g = 0; g <= (small ? 3 : 5); g++) {
      var t = from + (to - from) / (small ? 3 : 5) * g;
      s.appendChild(svg('text', { x: Math.min(W - m.r - 30, Math.max(m.l, X(t) - 30)), y: m.t + ph + 15 }, new Date(t * 1000).toLocaleString(undefined, period() && period() <= 259200 ? { hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'short' } : { day: 'numeric', month: 'short' })));
    }
    // faint full-height shading so a band can be matched to the line; identity comes from the lane and the label
    evs.forEach(function (e) {
      var x1 = Math.max(m.l, X(e.start)), x2 = Math.min(W - m.r, X(e.shown_end));
      if (x2 < m.l || x1 > W - m.r) return;
      var r = svg('rect', { 'class': 'u-shade', x: x1, y: m.t, width: Math.max(2, x2 - x1), height: ph });
      r.style.fill = colour(e.source);
      s.appendChild(r);
    });
    ['week', 'session'].forEach(function (k) {
      var d = '', pen = false;
      st.series.forEach(function (p) {
        if (p[k] === null || p.ts < from) return;
        d += (pen ? 'L' : 'M') + X(p.ts).toFixed(1) + ' ' + Y(p[k]).toFixed(1);
        pen = true;
      });
      if (d) s.appendChild(svg('path', { 'class': 'u-line', 'data-k': k, d: d }));
    });
    st.jumps.forEach(function (j) {
      if (j.ts < from) return;
      var yv = (st.series.filter(function (p) { return p.ts === j.ts; })[0] || {})[j.metric];
      var c = svg('circle', { 'class': 'u-jump', cx: X(j.ts), cy: Y(yv === undefined || yv === null ? 0 : yv), r: 6 });
      c.appendChild(svg('title', {}, 'Unattributed ' + j.metric + ' rise of ' + j.rise + ' points at ' + fmt(j.ts)));
      s.appendChild(c);
    });
    // event lanes
    lanes.forEach(function (src, i) {
      var y = m.t + ph + m.b + 8 + i * lane - 14;
      evs.filter(function (e) { return e.source === src; }).forEach(function (e) {
        var x1 = Math.max(m.l, X(e.start)), x2 = Math.min(W - m.r, X(e.shown_end));
        if (x2 < m.l || x1 > W - m.r) return;
        var r = svg('rect', { 'class': 'u-band', x: x1, y: y, width: Math.max(6, x2 - x1), height: lane - 4, rx: 3, tabindex: 0,
          'aria-label': e.source + ': ' + e.label + ', ' + fmt(e.start) + ', ' + (e.end === null ? 'still open' : len(e.end - e.start)) });
        r.style.fill = colour(src);
        var lines = [e.source + ': ' + e.label, fmt(e.start) + ' for ' + (e.end === null ? 'unknown time (no end received)' : len(e.end - e.start))].concat(e.ref ? ['ref ' + e.ref] : []);
        r.addEventListener('mousemove', function (ev) { ev.stopPropagation(); showTip(lines, ev.clientX, ev.clientY); });
        r.addEventListener('mouseleave', hideTip);
        r.addEventListener('focus', function () { var b = r.getBoundingClientRect(); showTip(lines, b.left, b.bottom); });
        r.addEventListener('blur', hideTip);
        s.appendChild(r);
      });
    });
    // crosshair
    var cross = svg('line', { 'class': 'u-cross', y1: m.t, y2: m.t + ph, visibility: 'hidden' });
    s.appendChild(cross);
    var hit = svg('rect', { x: m.l, y: m.t, width: pw, height: ph, fill: 'transparent' });
    hit.addEventListener('mousemove', function (ev) {
      var box = s.getBoundingClientRect(), px = (ev.clientX - box.left) / box.width * W, t = from + (px - m.l) / pw * (to - from);
      var near = null;
      st.series.forEach(function (p) { if (p.ts >= from && (!near || Math.abs(p.ts - t) < Math.abs(near.ts - t))) near = p; });
      if (!near) return;
      cross.setAttribute('x1', X(near.ts)); cross.setAttribute('x2', X(near.ts)); cross.setAttribute('visibility', 'visible');
      showTip([fmt(near.ts), 'Session ' + (near.session === null ? 'n/a' : near.session + '%'), 'Week ' + (near.week === null ? 'n/a' : near.week + '%')], ev.clientX, ev.clientY);
    });
    hit.addEventListener('mouseleave', function () { cross.setAttribute('visibility', 'hidden'); hideTip(); });
    s.insertBefore(hit, cross);
    clear(host);
    host.appendChild(s);
    legend(lanes);
    $('chart-note').textContent = st.series.length ? 'Usage is sampled about every 5 minutes, so an event is matched to a rise by time window. Hover or focus a band for its label.' : 'No usage samples yet. The first one arrives within 5 minutes of the add-on starting.';
  }

  function fillTables() {
    var from = period() ? Math.floor(Date.now() / 1000) - period() : 0;
    var evs = st.events.filter(matches), tb = $('events').tBodies[0];
    clear(tb);
    evs.forEach(function (e) {
      tb.appendChild(el('tr', {}, [el('td', { text: fmt(e.start) }), el('td', { text: e.source }), el('td', { text: e.label }), el('td', { text: e.ref }),
        el('td', { text: e.end === null ? 'open' : len(e.end - e.start) })]));
    });
    $('events').hidden = !evs.length;
    $('events-empty').hidden = !!evs.length;
    $('f-count').textContent = evs.length + (evs.length === 1 ? ' event' : ' events');
    var js = st.jumps.filter(function (j) { return j.ts >= from; }).reverse(), jb = $('jumps').tBodies[0];
    clear(jb);
    js.forEach(function (j) {
      jb.appendChild(el('tr', {}, [el('td', { text: fmt(j.ts) }), el('td', { text: j.metric === 'session' ? 'Session' : 'Week' }), el('td', { text: '+' + j.rise })]));
    });
    $('jumps').hidden = !js.length;
    $('jumps-empty').hidden = !!js.length;
  }

  function render() { fillTables(); draw(); }

  // Countdown tile. The server sends an absolute target time; the browser ticks it every second and corrects for a clock that is off
  // (offset = server time - browser time at the moment of the reply). Re-synced by load().
  var cd = null, offset = 0;
  function dhms(sec) {
    var d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60), s = Math.floor(sec % 60);
    return (d ? d + 'd ' : '') + (d || h ? h + 'h ' : '') + m + 'm ' + s + 's';
  }
  function tick() {
    var box = $('clock-text'), tile = $('clock').parentNode, state = 'no_data', text = 'No data', note;
    if (cd && cd.state !== 'no_data') {
      var left = cd.target_ts - (Date.now() / 1000 + offset);
      if (cd.state === 'under' || left <= 0) { state = 'under'; text = 'Under pace now'; }
      else { state = 'counting'; text = dhms(left); }
      note = 'Pace ' + cd.pace + ' points over an even spread; sample from ' + fmt(cd.sample_ts) + '. Counts down to ' + fmt(cd.target_ts) + ' if no more Claude is used; re-synced when new data arrives.';
    } else {
      note = cd && cd.sample_ts ? 'The newest sample is from ' + fmt(cd.sample_ts) + ' (older than 15 minutes) or has no pace.' : 'No usage samples yet.';
    }
    tile.setAttribute('data-state', state);
    if (box.textContent !== text) { box.textContent = text; $('clock').setAttribute('aria-label', text); }
    $('clock-note').textContent = note;
  }
  function loadClock() {
    return api('/countdown').then(function (d) { cd = d; offset = d.server_ts - Date.now() / 1000; tick(); }).catch(function () { cd = null; tick(); });
  }

  function load() {
    var since = period() ? Math.floor(Date.now() / 1000) - period() : 0;
    return Promise.all([api('/series?since=' + since), api('/events?since=' + since), api('/status')]).then(function (r) {
      st.series = r[0].series; st.jumps = r[0].jumps; st.threshold = r[0].jump_threshold; st.events = r[1].events; st.mode = r[2].mode;
      var set = {};
      st.events.forEach(function (e) { set[e.source] = 1; });
      st.sources = Object.keys(KNOWN).filter(function (k) { return set[k]; }).concat(Object.keys(set).filter(function (k) { return !KNOWN[k]; }).sort());
      var sel = $('f-source'), cur = sel.value;
      clear(sel);
      sel.appendChild(el('option', { value: '', text: 'All' }));
      st.sources.forEach(function (x) { sel.appendChild(el('option', { value: x, text: x })); });
      sel.value = st.sources.indexOf(cur) >= 0 ? cur : '';
      var s = $('service-status');
      s.setAttribute('status', 'ok');
      s.textContent = st.mode === 'live' ? 'Live' : 'Shadow: events are not stored yet';
      $('access').textContent = 'A rise of ' + st.threshold + ' points or more in one step with no event is flagged. Version ' + r[2].version + '.';
      render();
    }).catch(function (e) {
      var s = $('service-status');
      s.setAttribute('status', 'error');
      s.textContent = 'Could not load: ' + e.message;
    });
  }

  function init() {
    fetch('ui.json', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (i) { $('purpose').textContent = i.purpose || ''; }).catch(function () {});
    $('f-search').addEventListener('input', render);
    $('f-source').addEventListener('change', render);
    $('f-period').addEventListener('change', load);
    $('f-clear').addEventListener('click', function () { $('f-search').value = ''; $('f-source').value = ''; $('f-period').value = '86400'; load(); });
    var tm;
    window.addEventListener('resize', function () { clearTimeout(tm); tm = setTimeout(draw, 120); });
    load();
    setInterval(load, 120000);
    loadClock();
    setInterval(loadClock, 60000);
    setInterval(tick, 1000);
  }
  init();
})();
