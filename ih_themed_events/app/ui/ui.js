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
      d.appendChild(el('button', { class: 'e-button', type: 'button', text: 'Edit event', on: { click: function () { editEvent(ev); } } }));
      d.appendChild(el('div', { class: 'e-row' }, [el('span', { class: 'e-pill', text: STATUS_TEXT[ev.status] || ev.status }), modePill(ev), el('span', { text: nextText(ev.next) })]));
      if (ev.mode_state) {
        d.appendChild(el('div', { class: 'e-row' }, [
          el('button', { class: 'e-button', type: 'button', text: 'Switch mode ' + (ev.mode_state === 'on' ? 'off' : 'on'), on: { click: function () { setMode(slug, ev.mode_state !== 'on'); } } })]));
      }
      var facts = [['Lights', String((ev.light_group ? [ev.light_group] : []).concat(ev.lights).length)], ['Speakers', String(ev.speakers.length)],
        ['Automations to pause', String(ev.pause_automations.length)], ['Auto-off', ev.auto_off || 'never'],
        ['Effects', ev.effects.map(function (e) { return e.name + (e.at ? ' at ' + e.at : ''); }).join(', ') || 'none']];
      d.appendChild(section('The event', facts.map(function (f) { return el('div', {}, [el('b', { text: f[0] + ': ' }), f[1]]); })
        .concat(ev.notes ? [el('p', { class: 'ih-metadata', text: ev.notes })] : [])));
      d.appendChild(checklistView(ev));
      d.appendChild(reviewView(ev));
      d.appendChild(el('p', { class: 'ih-metadata', text: 'Saving an event records your plan. It does not install or update automations in Home Assistant.' }));
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

  function editEvent(ev) {
    api('GET', '/entities').then(function (data) { renderEditor(ev, data.entities); }).catch(fail);
  }

  function renderEditor(ev, entities) {
    ev = ev || {};
    $('list-view').hidden = true; $('detail-view').hidden = false;
    var root = $('detail'); clear(root); say('');
    root.appendChild(el('h2', { text: ev.slug ? 'Edit event' : 'Create event' }));
    var form = el('form', { class: 'e-form' }); root.appendChild(form);
    function field(label, node) { form.appendChild(el('label', {}, [label, node])); return node; }
    function input(type, value, attrs) {
      attrs = attrs || {}; attrs.type = type; var n = el('input', attrs); n.value = value == null ? '' : value; return n;
    }
    function select(options, value) {
      var n = el('select'); options.forEach(function (o) { n.appendChild(el('option', { value: o[0], text: o[1] })); }); n.value = value; return n;
    }
    function heading(text) { form.appendChild(el('h3', { text: text })); }
    function picker(label, domain, selected, single) {
      var chosen = (selected || []).slice(), box = el('div', { class: 'e-picker' });
      var choices = entities.filter(function (e) { return e.id.indexOf(domain + '.') === 0; });
      chosen.forEach(function (id) { if (!choices.some(function (e) { return e.id === id; })) choices.push({ id: id, name: id + ' (unavailable)' }); });
      choices.sort(function (a, b) { return a.name.localeCompare(b.name); });
      var pickerNode = select([['', 'Choose ' + label.toLowerCase()]].concat(choices.map(function (e) { return [e.id, e.name]; })), '');
      var list = el('div');
      function draw() {
        clear(list);
        chosen.forEach(function (id) {
          var item = choices.find(function (e) { return e.id === id; });
          list.appendChild(el('div', { class: 'e-row' }, [el('span', { text: item ? item.name : id }), el('button', { type: 'button', class: 'e-button', text: 'Remove', on: { click: function () { chosen = chosen.filter(function (x) { return x !== id; }); draw(); } } })]));
        });
      }
      pickerNode.addEventListener('change', function () { if (pickerNode.value && chosen.indexOf(pickerNode.value) < 0) { if (single) chosen = []; chosen.push(pickerNode.value); draw(); } pickerNode.value = ''; });
      box.appendChild(pickerNode); box.appendChild(list); form.appendChild(el('div', {}, [el('strong', { text: label }), box])); draw();
      return function () { return chosen.slice(); };
    }
    var name = field('Event name', input('text', ev.name || '', { required: true, maxlength: '80' }));
    var status = field('Progress', select([['planned', 'Planning'], ['built', 'Built'], ['live', 'Live']], ev.status || 'planned'));
    heading('When');
    var when = ev.when || {}, annual = when.annual || {};
    var schedule = field('Schedule', select([['none', 'No date yet'], ['annual', 'Every year'], ['dates', 'Specific dates']], when.annual ? 'annual' : when.dates ? 'dates' : 'none'));
    var annualBox = el('div', { class: 'e-form' }); form.appendChild(annualBox);
    var month = input('number', annual.month || 1, { min: '1', max: '12' }), day = input('number', annual.day || 1, { min: '1', max: '31' }), duration = input('number', annual.days || 1, { min: '1', max: '31' });
    [[ 'Month (1–12)', month ], [ 'Day of month', day ], [ 'Number of days', duration ]].forEach(function (p) { annualBox.appendChild(el('label', {}, [p[0], p[1]])); });
    var dateBox = el('div', { class: 'e-form' }), dateRows = el('div', { class: 'e-form' }); form.appendChild(dateBox); dateBox.appendChild(dateRows);
    function addDate(row) {
      var start = input('date', row.start || '', { required: true }), end = input('date', row.end || '');
      var line = el('div', { class: 'e-subform' }, [el('label', {}, ['Start date', start]), el('label', {}, ['End date (optional)', end])]);
      line.appendChild(el('button', { type: 'button', class: 'e-button', text: 'Remove dates', on: { click: function () { line.remove(); } } }));
      line._dateValues = function () { return { start: start.value, end: end.value || start.value }; }; dateRows.appendChild(line);
    }
    (when.dates || []).forEach(addDate);
    dateBox.appendChild(el('button', { type: 'button', class: 'e-button', text: 'Add dates', on: { click: function () { addDate({}); } } }));
    function scheduleChanged() { annualBox.hidden = schedule.value !== 'annual'; dateBox.hidden = schedule.value !== 'dates'; Array.prototype.forEach.call(dateRows.querySelectorAll('input'), function (n) { n.disabled = schedule.value !== 'dates'; }); }
    schedule.addEventListener('change', scheduleChanged); scheduleChanged();
    heading('Devices');
    var group = picker('Light group', 'light', ev.light_group ? [ev.light_group] : [], true);
    var lights = picker('Lights', 'light', ev.lights), switches = picker('Switches', 'switch', ev.extra_switches), speakers = picker('Speakers', 'media_player', ev.speakers), pauses = picker('Automations to pause', 'automation', ev.pause_automations);
    heading('Lighting');
    var look = ev.look || {}, hasColour = field('Set a lighting colour', input('checkbox', '', {})); hasColour.checked = !!look.rgb;
    var colour = field('Colour', input('color', look.rgb ? '#' + look.rgb.map(function (c) { return c.toString(16).padStart(2, '0'); }).join('') : '#ffffff'));
    var brightness = field('Brightness (1–255; leave blank for default)', input('number', look.brightness, { min: '1', max: '255' }));
    var transition = field('Fade time in seconds (leave blank for default)', input('number', look.transition, { min: '0', max: '30' }));
    var off = field('Turn off at (optional)', input('time', ev.auto_off == null ? '02:30' : ev.auto_off));
    heading('Effects');
    var folder = ev.sounds_folder || '';
    var effectsBox = el('div', { class: 'e-form' }); form.appendChild(effectsBox);
    function addEffect(effect) {
      var line = el('div', { class: 'e-subform' }); effectsBox.appendChild(line);
      function ef(label, node) { line.appendChild(el('label', {}, [label, node])); return node; }
      var ename = ef('Effect name', input('text', effect.name || '', { required: true, maxlength: '80' }));
      var kind = ef('What happens', select([['sound_and_lights', 'Sound and lights'], ['sound', 'Sound'], ['lights', 'Lights']], effect.kind || 'sound_and_lights'));
      var soundValue = effect.sound || '', soundLabel = el('span', { text: soundValue ? soundValue.split('/').pop() : 'No sound selected' });
      var browser = el('div', { class: 'e-picker', hidden: true }), choices = el('div', { class: 'e-form' });
      var backStack = [];
      function browse(source) {
        clear(choices); choices.appendChild(el('span', { text: 'Loading sounds…' }));
        api('POST', '/media', { source: source }).then(function (data) {
          clear(choices);
          if (backStack.length) choices.appendChild(el('button', { type: 'button', class: 'e-button', text: 'Back', on: { click: function () { browse(backStack.pop()); } } }));
          data.items.forEach(function (item) {
            if (!item.folder && !item.playable) return;
            choices.appendChild(el('button', { type: 'button', class: 'e-button', text: (item.folder ? 'Open folder: ' : 'Choose: ') + item.name, on: { click: function () {
              if (item.folder) { backStack.push(source); browse(item.id); }
              else { soundValue = item.id; soundLabel.textContent = item.name; browser.hidden = true; }
            } } }));
          });
          if (!data.items.length) choices.appendChild(el('span', { text: 'This folder is empty.' }));
        }).catch(function (e) { clear(choices); choices.appendChild(el('span', { text: e.message })); });
      }
      line.appendChild(el('div', {}, [el('strong', { text: 'Sound: ' }), soundLabel]));
      line.appendChild(el('button', { type: 'button', class: 'e-button', text: 'Choose sound', on: { click: function () { browser.hidden = false; backStack = []; browse('media-source://media_source/local'); } } }));
      browser.appendChild(choices); line.appendChild(browser);
      var min = ef('Minimum minutes between effects', input('number', effect.min_gap_minutes == null ? 10 : effect.min_gap_minutes, { min: '1', max: '240', required: true }));
      var max = ef('Maximum minutes between effects', input('number', effect.max_gap_minutes == null ? 15 : effect.max_gap_minutes, { min: '1', max: '240', required: true }));
      var from = ef('Start time (optional)', input('time', effect.from || '')), to = ef('End time (optional)', input('time', effect.to || '')), at = ef('Exact time (optional)', input('time', effect.at || ''));
      line.appendChild(el('button', { type: 'button', class: 'e-button', text: 'Remove effect', on: { click: function () { line.remove(); } } }));
      line._effectValues = function (i) { return { id: effect.id || ('effect_' + i), name: ename.value, kind: kind.value, sound: soundValue, min_gap_minutes: Number(min.value), max_gap_minutes: Number(max.value), from: from.value, to: to.value, at: at.value }; };
    }
    (ev.effects || []).forEach(addEffect);
    form.appendChild(el('button', { type: 'button', class: 'e-button', text: 'Add effect', on: { click: function () { if (effectsBox.children.length < 12) addEffect({}); else say('You can add up to 12 effects.'); } } }));
    var notes = field('Notes', el('textarea', { rows: '3', maxlength: '2000' })); notes.value = ev.notes || '';
    form.appendChild(el('p', { class: 'ih-metadata', text: 'Saving records your plan; it does not install or update Home Assistant automations.' }));
    form.appendChild(el('button', { type: 'submit', class: 'e-button', text: 'Save event' }));
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var result = { name: name.value, status: status.value, when: {}, light_group: group()[0] || '', lights: lights(), extra_switches: switches(), speakers: speakers(), pause_automations: pauses(), sounds_folder: folder, effects: [], look: {}, auto_off: off.value, notes: notes.value };
      if (ev.slug) result.slug = ev.slug;
      if (ev.icon) result.icon = ev.icon;
      if (schedule.value === 'annual') result.when = { annual: { month: Number(month.value), day: Number(day.value), days: Number(duration.value) } };
      if (schedule.value === 'dates') result.when = { dates: Array.prototype.map.call(dateRows.children, function (row) { return row._dateValues(); }) };
      if (hasColour.checked) result.look.rgb = [1, 3, 5].map(function (i) { return parseInt(colour.value.slice(i, i + 2), 16); });
      if (brightness.value !== '') result.look.brightness = Number(brightness.value);
      if (transition.value !== '') result.look.transition = Number(transition.value);
      var used = (ev.effects || []).map(function (e) { return e.id; }), next = 1;
      result.effects = Array.prototype.map.call(effectsBox.children, function (row) { while (used.indexOf('effect_' + next) !== -1) next++; var effect = row._effectValues(next++); used.push(effect.id); return effect; });
      api('POST', ev.slug ? '/events/' + ev.slug : '/events', result).then(function (saved) { say('Event saved.'); showEvent(saved.slug); }).catch(fail);
    });
  }

  function setMode(slug, on) {
    api('POST', '/events/' + slug + '/mode', { on: on }).then(function () { say('Mode switched ' + (on ? 'on' : 'off') + '.'); return showEvent(slug); }).catch(fail);
  }

  $('back').addEventListener('click', function () { say(''); showList(); });
  $('new-event').addEventListener('click', function () { editEvent(null); });
  var status = $('service-status');
  fetch('api/events', { cache: 'no-store' }).then(function (r) { status.setAttribute('status', r.ok ? 'ok' : 'error'); status.textContent = r.ok ? 'Running' : 'Problem'; }).catch(function () { status.setAttribute('status', 'error'); status.textContent = 'Not reachable'; });
  showList();
}());
