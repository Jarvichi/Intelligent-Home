/* ih_themed_events page: list of events, one event with its checklist, review and generated files.
   Talks to api/ on the ingress server, which always acts as the owner. No markup is built from text: nodes use createElement and textContent. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var STATUS_TEXT = { planned: 'Planned', built: 'Built, not installed', live: 'Live' };
  var current = null;

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
  function say(text) { $('msg').textContent = text || ''; }
  function fail(e) { say(e.message || 'Something went wrong'); }
  function niceDate(iso) {
    var d = new Date(iso + 'T12:00:00');
    return isNaN(d) ? iso : d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  }
  function nextText(n) {
    if (!n) return 'No date set';
    if (n.running) return 'Running now (until ' + niceDate(n.end) + ')';
    if (n.days_to_start === 0) return 'Today';
    return niceDate(n.start) + ' (in ' + n.days_to_start + ' day' + (n.days_to_start === 1 ? '' : 's') + ')';
  }
  function modePill(ev) {
    if (!ev.mode_state) return el('span', { class: 'e-pill', text: 'No mode switch yet' });
    return el('span', { class: 'e-pill', 'data-on': ev.mode_state, text: 'Mode ' + ev.mode_state });
  }

  function showList() {
    current = null;
    $('detail-view').hidden = true;
    $('list-view').hidden = false;
    return api('GET', '/events').then(function (d) {
      var box = $('events');
      clear(box);
      $('events-empty').hidden = d.events.length > 0;
      d.events.forEach(function (ev) {
        box.appendChild(el('div', { class: 'e-card' }, [
          el('h2', { text: ev.name }),
          el('div', { class: 'e-row' }, [el('span', { class: 'e-pill', text: STATUS_TEXT[ev.status] || ev.status }), modePill(ev)]),
          el('div', { text: nextText(ev.next) }),
          el('div', { class: 'ih-metadata', text: ev.open_items + ' checklist item' + (ev.open_items === 1 ? '' : 's') + ' open' }),
          el('div', { class: 'e-row' }, [el('button', { class: 'e-button', type: 'button', text: 'Open', on: { click: function () { showEvent(ev.slug); } } })])
        ]));
      });
    }).catch(fail);
  }

  function section(title, kids) { return el('div', { class: 'e-sect' }, [el('h3', { text: title })].concat(kids)); }

  function showEvent(slug) {
    return api('GET', '/events/' + slug).then(function (ev) {
      current = slug;
      $('list-view').hidden = true;
      $('detail-view').hidden = false;
      var d = $('detail');
      clear(d);
      d.appendChild(el('h2', { text: ev.name }));
      d.appendChild(el('div', { class: 'e-row' }, [el('span', { class: 'e-pill', text: STATUS_TEXT[ev.status] || ev.status }), modePill(ev), el('span', { text: nextText(ev.next) })]));
      if (ev.mode_state) {
        d.appendChild(el('div', { class: 'e-row' }, [
          el('button', { class: 'e-button', type: 'button', text: 'Switch mode ' + (ev.mode_state === 'on' ? 'off' : 'on'), on: { click: function () { setMode(slug, ev.mode_state !== 'on'); } } })]));
      }
      var facts = [['Lights', (ev.light_group ? [ev.light_group] : []).concat(ev.lights).length + ' (' + (ev.light_group || 'no group') + ')'], ['Speakers', ev.speakers.join(', ') || 'none'],
        ['Sounds folder', ev.sounds_folder || 'none'], ['Pauses', ev.pause_automations.join(', ') || 'nothing'], ['Auto-off', ev.auto_off || 'never'],
        ['Effects', ev.effects.map(function (e) { return e.name + (e.at ? ' at ' + e.at : ''); }).join(', ') || 'none']];
      d.appendChild(section('The event', facts.map(function (f) { return el('div', {}, [el('b', { text: f[0] + ': ' }), f[1]]); })
        .concat(ev.notes ? [el('p', { class: 'ih-metadata', text: ev.notes })] : [])));
      d.appendChild(checklistView(ev));
      d.appendChild(reviewView(ev));
      var gen = el('div', { id: 'gen' });
      d.appendChild(section('Home Assistant files (text only)', [el('button', { class: 'e-button', type: 'button', text: 'Show the generated files', on: { click: function () { showFiles(slug, gen); } } }), gen]));
    }).catch(fail);
  }

  function checklistView(ev) {
    var rows = ev.checklist.map(function (it) {
      var box = el('input', { type: 'checkbox', id: 'i' + it.id, 'aria-label': it.text });
      box.checked = it.done;
      box.addEventListener('change', function () {
        api('POST', '/events/' + ev.slug + '/items/' + it.id, { done: box.checked }).then(function () { showEvent(ev.slug); }).catch(fail);
      });
      return el('label', { class: 'e-check', 'data-done': it.done ? '1' : '0' }, [box, el('span', { text: it.text + (it.due ? ' (by ' + niceDate(it.due) + ')' : '') })]);
    });
    var text = el('input', { type: 'text', maxlength: '300', 'aria-label': 'New checklist item', placeholder: 'New checklist item' });
    var days = el('input', { type: 'number', min: '-30', max: '365', value: '0', 'aria-label': 'Days before the event' });
    var add = el('button', { class: 'e-button', type: 'button', text: 'Add item', on: { click: function () {
      if (!text.value.trim()) return say('Write the item first.');
      api('POST', '/events/' + ev.slug + '/items', { text: text.value, days_before: parseInt(days.value || '0', 10) }).then(function () { showEvent(ev.slug); }).catch(fail);
    } } });
    return section('Checklist', rows.concat([el('div', { class: 'e-form' }, [text, el('label', {}, ['Days before the event (negative = after): ', days]), add])]));
  }

  function reviewView(ev) {
    var r = ev.review;
    if (!r.occurrence) return section('Review', [el('p', { class: 'ih-metadata', text: 'No date set, so no review yet.' })]);
    var worked = el('textarea', { rows: '3', maxlength: '2000', 'aria-label': 'What worked' });
    var change = el('textarea', { rows: '3', maxlength: '2000', 'aria-label': 'What to change' });
    worked.value = r.worked; change.value = r.change;
    var save = el('button', { class: 'e-button', type: 'button', text: 'Save review', on: { click: function () {
      api('POST', '/events/' + ev.slug + '/review', { occurrence: r.occurrence, worked: worked.value, change: change.value }).then(function () { say('Review saved.'); }).catch(fail);
    } } });
    return section('Review of the run on ' + niceDate(r.occurrence), [el('div', { class: 'e-form' }, [el('label', {}, ['What worked', worked]), el('label', {}, ['What to change next time', change]), save])]);
  }

  function showFiles(slug, box) {
    api('GET', '/events/' + slug + '/generate').then(function (g) {
      clear(box);
      g.warnings.forEach(function (w) { box.appendChild(el('p', { class: 'e-warn', text: w })); });
      Object.keys(g.files).forEach(function (name) { box.appendChild(el('h4', { text: name })); box.appendChild(el('pre', { class: 'e-code', text: g.files[name] })); });
    }).catch(fail);
  }

  function setMode(slug, on) {
    api('POST', '/events/' + slug + '/mode', { on: on }).then(function () { say('Mode switched ' + (on ? 'on' : 'off') + '.'); return showEvent(slug); }).catch(fail);
  }

  $('back').addEventListener('click', function () { say(''); showList(); });
  var status = $('service-status');
  fetch('api/events', { cache: 'no-store' }).then(function (r) { status.setAttribute('status', r.ok ? 'ok' : 'error'); status.textContent = r.ok ? 'Running' : 'Problem'; }).catch(function () { status.setAttribute('status', 'error'); status.textContent = 'Not reachable'; });
  showList();
}());
