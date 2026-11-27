/* ih_tasks page: Kanban board + task detail. Talks to api/ on the ingress server, which always acts as the owner.
   No markup is built from text: every node is made with createElement and textContent. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var state = { labels: [], flash: '', flashTone: '', draft: '', board: null, meta: null, open: null, merging: false, picked: {}, keep: null, dups: null };
  var detailView = 'new';
  var detailDirty = false, switchTimer = null;
  var REL_TEXT = { blocked_by: 'Blocked by', depends_on: 'Depends on', relates_to: 'Relates to', blocks: 'Blocks', needed_by: 'Needed by' };
  var WAIT_TEXT = { owner: 'Waiting for you', worker: 'With the worker', check: 'Being checked', children: 'Waiting for its child tasks' };
  var KIND_TEXT = { created: 'Created', moved: 'Moved', closed: 'Closed', comment: 'Comment', asked: 'Question asked', answered: 'Answered',
    rejected: 'Plan rejected', link: 'Link added', plan: 'Plan', edited: 'Edited', imported: 'Carried over', question_withdrawn: 'Question withdrawn',
    reminder_set: 'Reminder set', reminder_fired: 'Reminder fired', child_added: 'Child task', children_finished: 'Child tasks finished', duplicate_linked: 'Duplicate', merged: 'Merged', merged_into: 'Merged', not_duplicate: 'Not a duplicate', related: 'Link added', unrelated: 'Link removed', run_now: 'Work this now' };

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
  function when(iso) {
    var d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }
  function stageLabel(id) {
    var s = (state.meta ? state.meta.stages : []).filter(function (x) { return x.id === id; })[0];
    return s ? s.label : id;
  }

  /* ---- board ---- */
  function matches(t) {
    var q = $('f-search').value.trim().toLowerCase().replace(/^#/, ''), st = $('f-stage').value, w = $('f-waiting').value;
    var lb = $('f-label').value;
    if (lb && (t.labels || []).indexOf(lb) < 0) return false;
    if (st && t.stage !== st) return false;
    if (w === 'input' && !t.needs_input) return false;
    if (w === 'blocked' && !t.blocked) return false;
    if (w && w !== 'input' && w !== 'blocked' && t.waiting_on !== w) return false;
    if (q && t.title.toLowerCase().indexOf(q) < 0 && String(t.id) !== q) return false;
    return true;
  }
  function card(t) {
    var flags = [];
    if (t.needs_input) flags.push(el('span', { 'class': 't-flag', 'data-tone': 'warn', text: 'Needs your input' }));
    else if (t.waiting_on === 'owner') flags.push(el('span', { 'class': 't-flag', 'data-tone': 'warn', text: t.stage === 'planning' ? 'Plan ready for you' : 'Waiting for you' }));
    if (t.run_now) flags.push(el('span', { 'class': 't-flag', 'data-tone': 'ok', text: 'Work now' }));
    if (t.blocked && t.blocked_by) flags.push(el('span', { 'class': 't-flag', 'data-tone': 'warn', text: 'Blocked by #' + t.blocked_by }));
    if (t.stage === 'planning' && t.revise) flags.push(el('span', { 'class': 't-flag', text: 'Plan being revised' }));
    var sub = [];
    if (t.priority && t.priority !== 'normal') sub.push(t.priority === 'high' ? 'High priority' : 'Low priority');
    if (t.due) sub.push('Due ' + t.due);
    if (t.raised_by) sub.push('Raised by ' + t.raised_by);
    if (t.children_total) sub.push((t.children_total - t.children_open) + ' of ' + t.children_total + ' child tasks done');
    if (t.parent_id) sub.push('Part of #' + t.parent_id);
    if (t.remind_at) sub.push('Reminder on ' + t.remind_at);
    if (t.stage === 'closed' && t.closed_reason) sub.push(closeLabel(t.closed_reason) + (t.duplicate_of ? ' of #' + t.duplicate_of : ''));
    var body = [el('b', { text: '#' + t.id }), t.title, sub.length ? el('small', { text: sub.join(' · ') }) : null, flags.length ? el('span', { 'class': 't-flags' }, flags) : null, labelChips(t)];
    if (state.merging && isOpen(t)) {
      /* merge mode: the whole card is the tap target of its checkbox */
      var box = el('input', { type: 'checkbox', 'aria-label': 'Select task ' + t.id + ': ' + t.title, checked: !!state.picked[t.id], on: { change: function () {
        if (box.checked) state.picked[t.id] = true; else delete state.picked[t.id];
        lab.setAttribute('data-picked', box.checked ? 'true' : 'false');
        renderMergeBar();
      } } });
      var lab = el('label', { 'class': 't-card t-pick', 'data-picked': state.picked[t.id] ? 'true' : 'false' }, [box, el('span', {}, body)]);
      return lab;
    }
    return el('button', { 'class': 't-card', type: 'button', 'aria-label': 'Task ' + t.id + ': ' + t.title, on: { click: function () { openTask(t.id); } } }, body);
  }
  function labelChips(t) {
    return (t.labels || []).length ? el('span', { 'class': 't-labels' }, t.labels.map(function (x) { return el('span', { 'class': 't-label', text: x }); })) : null;
  }
  /* Label filter + suggestions come from the labels in use (GET /labels). The filter keeps its pick across a reload. */
  function loadLabels() {
    return api('GET', '/labels').then(function (d) {
      state.labels = d.labels.map(function (x) { return x.label; });
      var sel = $('f-label'), keep = sel.value;
      clear(sel); sel.appendChild(el('option', { value: '', text: 'All' }));
      state.labels.forEach(function (x) { sel.appendChild(el('option', { value: x, text: x, selected: x === keep })); });
      $('f-label-row').hidden = !state.labels.length;
    }).catch(function () {});
  }
  function closeLabel(id) {
    var r = (state.meta ? state.meta.close_reasons : []).filter(function (x) { return x.id === id; })[0];
    return r ? r.label : id;
  }
  function renderBoard() {
    var b = state.board; if (!b) return;
    var board = $('board'), shown = 0, total = 0, only = $('f-stage').value;
    clear(board);
    b.columns.forEach(function (c) {
      total += c.tasks.length;
      var tasks = c.tasks.filter(matches);
      shown += tasks.length;
      if (c.stage === 'closed') {
        $('closed-title').textContent = 'Closed (' + tasks.length + ')';
        clear($('closed-list'));
        tasks.forEach(function (t) { $('closed-list').appendChild(card(t)); });
        $('closed-box').hidden = !!only && only !== 'closed';
        if (only === 'closed') $('closed-box').open = true;
        return;
      }
      if (only && only !== c.stage) return;
      board.appendChild(el('section', { 'class': 't-col', 'aria-label': c.label }, [
        el('h2', {}, [c.label, el('span', { text: String(tasks.length) })]),
        el('div', { 'class': 't-col-list' }, tasks.length ? tasks.map(card) : [el('p', { 'class': 't-empty', text: 'Nothing here' })])
      ]));
    });
    board.hidden = only === 'closed';
    $('f-count').textContent = shown + ' of ' + total + ' tasks';
    $('board-empty').hidden = shown > 0;
  }
  /* ---- merge ---- */
  function rank(t) { var ids = (state.meta ? state.meta.stages : []).map(function (x) { return x.id; }); return ids.indexOf(t.stage); }
  function isOpen(t) { return t.stage !== 'done' && t.stage !== 'closed'; }
  /* Same rules as the server: keep the most advanced stage (then the lowest number); an Approved or later task is never merged into an earlier one. */
  function defaultKeep(tasks) {
    return tasks.slice().sort(function (a, b) { return rank(b) - rank(a) || a.id - b.id; })[0].id;
  }
  function canKeep(t, tasks) {
    var approved = rank({ stage: 'approved' });
    return tasks.every(function (o) { return o.id === t.id || rank(o) < approved || rank(o) <= rank(t); });
  }
  function short(s, n) { return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  /* Keeper picker + two-tap confirm for a set of tasks. keep: {id} is shared with the caller so a pick survives a redraw. */
  function mergeControls(tasks, keep, label, done, msg) {
    if (!tasks.some(function (t) { return t.id === keep.id && canKeep(t, tasks); })) keep.id = defaultKeep(tasks);
    var wrap = el('div', { 'class': 't-merge-controls' });
    var draw = function () {
      clear(wrap);
      var sel = el('select', { 'aria-label': 'Task to keep' }, tasks.map(function (t) {
        return el('option', { value: String(t.id), selected: t.id === keep.id, disabled: !canKeep(t, tasks), text: 'Keep #' + t.id + ' · ' + stageLabel(t.stage) + ' · ' + short(t.title, 60) });
      }));
      sel.addEventListener('change', function () { keep.id = parseInt(sel.value, 10); draw(); });
      var others = tasks.filter(function (t) { return t.id !== keep.id; }).map(function (t) { return t.id; });
      var go = confirmButton(label(tasks.length, keep.id), 'primary', function () {
        go.disabled = true; msg.removeAttribute('data-tone'); msg.textContent = 'Merging…';
        api('POST', '/tasks/merge', { keep: keep.id, merge: others }).then(function () {
          done('Merged ' + others.map(function (i) { return '#' + i; }).join(', ') + ' into #' + keep.id + '. To undo, open a merged task under Closed and reopen it.');
        }).catch(function (e) { go.disabled = false; msg.setAttribute('data-tone', 'error'); msg.textContent = e.message; });
      });
      wrap.appendChild(sel); wrap.appendChild(go);
    };
    draw();
    return wrap;
  }
  var barKeep = { id: null };
  function pickedTasks() {
    var out = [];
    if (state.board) state.board.columns.forEach(function (c) { c.tasks.forEach(function (t) { if (state.picked[t.id] && isOpen(t)) out.push(t); }); });
    return out;
  }
  function renderMergeBar() {
    var bar = $('merge-bar'); clear(bar);
    bar.hidden = !state.merging || !!state.open;
    document.body.classList.toggle('t-merging', !bar.hidden);
    if (bar.hidden) return;
    var tasks = pickedTasks(), msg = msgLine();
    var cancel = el('button', { 'class': 't-button', type: 'button', text: 'Cancel', on: { click: function () { setMerging(false); } } });
    if (tasks.length < 2) {
      bar.appendChild(el('div', { 'class': 't-mergebar-row' }, [el('p', { 'class': 't-mergebar-text', text: tasks.length ? '1 task selected. Select 1 more to merge.' : 'Select 2 or more tasks to merge.' }), cancel]));
      return;
    }
    bar.appendChild(el('p', { 'class': 't-mergebar-text', text: 'Merge ' + tasks.length + ' tasks into one. The others close as duplicates of the task you keep.' }));
    bar.appendChild(el('div', { 'class': 't-mergebar-row' }, [mergeControls(tasks, barKeep, function (n, k) { return 'Merge ' + n + ' tasks into #' + k; }, function (text) {
      setMerging(false); $('tools-msg').textContent = text; loadBoard(); if (state.dups) loadDups();
    }, msg), cancel]));
    bar.appendChild(msg);
  }
  function setMerging(on) {
    state.merging = on; state.picked = {}; barKeep.id = null;
    $('merge-toggle').setAttribute('aria-pressed', on ? 'true' : 'false');
    $('merge-toggle').textContent = on ? 'Stop merging' : 'Merge';
    if (on) $('tools-msg').textContent = '';
    renderBoard(); renderMergeBar();
  }

  /* ---- likely duplicates ---- */
  function renderDups() {
    var box = $('dup-view'); clear(box);
    box.hidden = !state.dups;
    $('dup-toggle').setAttribute('aria-expanded', state.dups ? 'true' : 'false');
    $('dup-toggle').textContent = state.dups ? 'Hide duplicates' : 'Find duplicates';
    if (!state.dups) return;
    box.appendChild(el('h2', { text: 'Likely duplicates (' + state.dups.length + ')' }));
    if (!state.dups.length) { box.appendChild(el('p', { 'class': 'ih-empty-inline', text: 'No open tasks look the same.' })); return; }
    state.dups.forEach(function (g) {
      var msg = msgLine(), keep = { id: g.keep };
      var after = function (text) { $('tools-msg').textContent = text; loadBoard(); loadDups(); };
      var no = el('button', { 'class': 't-button', type: 'button', text: 'Not duplicates', on: { click: function () {
        no.disabled = true; msg.removeAttribute('data-tone'); msg.textContent = 'Saving…';
        api('POST', '/tasks/duplicates/dismiss', { ids: g.ids }).then(function () { after('Noted: ' + g.ids.map(function (i) { return '#' + i; }).join(', ') + ' are not duplicates.'); })
          .catch(function (e) { no.disabled = false; msg.setAttribute('data-tone', 'error'); msg.textContent = e.message; });
      } } });
      box.appendChild(el('ih-card', { 'class': 't-dup' }, [
        el('ul', { 'class': 't-dup-list' }, g.tasks.map(function (t) {
          return el('li', {}, [el('button', { 'class': 't-card', type: 'button', 'aria-label': 'Open task ' + t.id + ': ' + t.title, on: { click: function () { openTask(t.id); } } },
            [el('b', { text: '#' + t.id }), t.title, el('small', { text: [stageLabel(t.stage), t.due ? 'Due ' + t.due : '', t.needs_input ? 'Needs your input' : ''].filter(Boolean).join(' · ') })])]);
        })),
        el('div', { 'class': 't-dup-actions' }, [mergeControls(g.tasks, keep, function (n, k) { return 'Merge group into #' + k; }, after, msg), no]),
        msg
      ]));
    });
  }
  function loadDups() {
    return api('GET', '/tasks/duplicates').then(function (d) { state.dups = d.groups; renderDups(); })
      .catch(function (e) { $('tools-msg').textContent = e.message; });
  }

  function loadBoard() {
    api('GET', '/status').then(function (info) {
      var b = info.backup || {}, pending = (info.reminder_delivery || {}).pending || [];
      $('backup-info').textContent = 'Last successful backup: ' + (b.last_success || 'None recorded') + (b.error ? ' · Last attempt failed (' + b.error + '); retrying.' : '');
      var box = $('reminder-info'); clear(box);
      box.appendChild(el('p', { text: pending.length ? pending.length + ' reminder notification(s) waiting: ' + (info.reminder_delivery.configured ? 'the inbox could not be reached; retrying.' : 'inbox is not configured.') : 'No reminder notifications waiting.' }));
      pending.forEach(function (r) { box.appendChild(el('p', { text: '#' + r.id + ' · ' + r.title + ' · due ' + r.date })); });
    }).catch(function () { $('backup-info').textContent = 'Backup and reminder status unavailable.'; });
    return api('GET', '/board').then(function (b) {
      state.board = b;
      var s = $('service-status');
      s.setAttribute('status', b.needs_input ? 'warn' : 'ok');
      s.textContent = b.needs_input ? b.needs_input + ' need your input' : 'Up to date';
      renderBoard(); renderMergeBar(); loadLabels();
    }).catch(function (e) {
      var s = $('service-status'); s.setAttribute('status', 'error'); s.textContent = 'Not reachable';
      $('f-count').textContent = e.message;
    });
  }

  /* ---- detail ---- */
  function msgLine() { return el('p', { 'class': 't-msg', role: 'status', 'aria-live': 'polite' }); }
  function act(msg, promise) {
    msg.removeAttribute('data-tone'); msg.textContent = 'Saving…';
    return promise.then(function (t) { state.open = t; renderDetail(); loadBoard(); })
      .catch(function (e) { msg.setAttribute('data-tone', 'error'); msg.textContent = e.message; });
  }
  /* Two taps instead of a confirm dialog (dialogs can crash the phone app). */
  function confirmButton(label, variant, run) {
    var b = el('button', { 'class': 't-button', type: 'button', 'data-variant': variant, text: label });
    var timer = null;
    b.addEventListener('click', function () {
      if (b.getAttribute('data-armed') === 'true') { clearTimeout(timer); b.removeAttribute('data-armed'); b.textContent = label; run(); return; }
      b.setAttribute('data-armed', 'true'); b.textContent = 'Tap again: ' + label;
      timer = setTimeout(function () { b.removeAttribute('data-armed'); b.textContent = label; }, 5000);
    });
    return b;
  }
  function field(label, control, id) {
    control.id = id;
    return el('div', { 'class': 't-field' }, [el('label', { 'for': id, text: label }), control]);
  }
  /* ---- the shared field list: the New task panel and the task page are BOTH built from state.meta.fields (model.TASK_FIELDS, served on /api/meta).
     To add a field to a task, add it to that list; do not add markup for it here. Each control is {f, id, node, get(), dirty()}; get() is undefined for a read-only field. ---- */
  function fieldsFor(key) { return ((state.meta && state.meta.fields) || []).filter(function (f) { return f[key]; }); }
  function allTasks() { var out = []; if (state.board) state.board.columns.forEach(function (c) { out = out.concat(c.tasks); }); return out; }
  function parseLabels(text) { var out = []; text.split(',').forEach(function (x) { x = x.trim().toLowerCase(); if (x && out.indexOf(x) < 0) out.push(x); }); return out; }
  function labelsControl(f, init, id, changed) {
    var inp = el('input', { type: 'text', maxlength: '250', autocomplete: 'off', placeholder: 'for example ih, kindle' }), sug = el('div', { 'class': 't-suggest', role: 'group', 'aria-label': 'Labels already in use' });
    inp.value = (init || []).join(', ');
    var draw = function () {
      var parts = inp.value.split(','), cur = parts[parts.length - 1].trim().toLowerCase(), have = parseLabels(inp.value);
      clear(sug);
      state.labels.filter(function (x) { return !cur || x.indexOf(cur) >= 0 || have.indexOf(x) >= 0; }).slice(0, 10).forEach(function (x) {
        var on = have.indexOf(x) >= 0;
        sug.appendChild(el('button', { 'class': 't-button', type: 'button', 'data-picked': on ? 'true' : 'false', 'aria-pressed': on ? 'true' : 'false', text: x, on: { click: function () {
          var list = inp.value.split(',').map(function (p) { return p.trim(); }).filter(Boolean), last = list.length ? list[list.length - 1].toLowerCase() : '';
          if (on) list = list.filter(function (p) { return p.toLowerCase() !== x; });
          else if (last && last !== x && have.indexOf(last) < 0 && x.indexOf(last) >= 0) list[list.length - 1] = x;     /* finish the word being typed */
          else list.push(x);
          inp.value = list.join(', '); draw(); changed();
        } } }));
      });
    };
    inp.addEventListener('input', function () { draw(); changed(); });
    inp.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') ev.preventDefault(); });     /* Enter must not save the New task form half way through */
    draw();
    return { control: [inp, sug], focus: inp, get: function () { return parseLabels(inp.value); } };
  }
  function tasksControl(f, init, id, changed, selfId) {
    var picked = (init || []).slice(), list = el('ul', { 'class': 't-picked' }), box = el('input', { type: 'text', autocomplete: 'off', placeholder: 'Search by number or title' }), sug = el('div', { 'class': 't-suggest', role: 'group', 'aria-label': 'Matching tasks' });
    var titleOf = function (n) { var t = allTasks().filter(function (x) { return x.id === n; })[0]; return t ? t.title : ''; };
    var matches = function () {
      var q = box.value.trim().toLowerCase().replace(/^#/, '');
      if (!q) return [];
      return allTasks().filter(function (t) { return t.id !== selfId && picked.indexOf(t.id) < 0 && (String(t.id) === q || t.title.toLowerCase().indexOf(q) >= 0); }).slice(0, 6);
    };
    var add = function (n) { picked.push(n); box.value = ''; draw(); changed(); };
    var draw = function () {
      clear(list); clear(sug);
      picked.forEach(function (n) {
        list.appendChild(el('li', {}, [el('span', { text: '#' + n + ' ' + titleOf(n) }), el('button', { 'class': 't-button', type: 'button', text: 'Remove', 'aria-label': 'Remove link to #' + n, on: { click: function () { picked.splice(picked.indexOf(n), 1); draw(); changed(); } } })]));
      });
      matches().forEach(function (t) { sug.appendChild(el('button', { 'class': 't-button', type: 'button', text: '#' + t.id + ' ' + short(t.title, 50), on: { click: function () { add(t.id); box.focus(); } } })); });
    };
    box.addEventListener('input', draw);
    box.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Enter') return;
      ev.preventDefault();      /* Enter picks the best match; it must not save the form */
      var m = matches(), exact = m.filter(function (t) { return String(t.id) === box.value.trim().replace(/^#/, ''); })[0];
      if (exact || m.length) add((exact || m[0]).id);
    });
    draw();
    box.id = id;
    return { control: [list, box, sug], focus: box, get: function () { return picked.slice(); } };
  }
  /* one control for one field of the list; `init` is the starting value, `changed` is called on every edit */
  function buildField(f, init, prefix, changed, selfId) {
    var id = prefix + f.id, c = null, get, focus = null, parts;
    if (f.type === 'labels') c = labelsControl(f, init, id, changed);
    else if (f.type === 'tasks') c = tasksControl(f, init, id, changed, selfId);
    if (c) { get = c.get; focus = c.focus; parts = c.control; if (f.type === 'labels') c.focus.id = id; }
    else if (f.type === 'readonly') { get = function () { return undefined; }; parts = [el('p', { 'class': 't-readonly', id: id, text: init || '' })]; }
    else {
      var ctl;
      if (f.type === 'textarea') ctl = el('textarea', { rows: '5' });
      else if (f.type === 'select') ctl = el('select', {}, (f.options || []).map(function (o) { return el('option', { value: o.id, text: o.label }); }));
      else ctl = el('input', { type: f.type === 'date' ? 'date' : 'text', maxlength: f.max ? String(f.max) : null, required: f.required ? true : null });
      ctl.id = id;
      ctl.value = init === undefined || init === null ? '' : init;
      ctl.addEventListener('input', changed); ctl.addEventListener('change', changed);
      get = function () { return ctl.value.trim(); }; focus = ctl; parts = [ctl];
    }
    var kids = [el('label', { 'for': id, text: f.label + (f.required ? ' (required)' : '') })].concat(parts);
    if (f.help) kids.push(el('p', { 'class': 't-help', text: f.help }));
    var start = JSON.stringify(get() === undefined ? null : get());
    return { f: f, id: f.id, node: el('div', { 'class': 't-field' }, kids), get: get, focus: focus, dirty: function () { var v = get(); return v !== undefined && JSON.stringify(v) !== start; } };
  }
  function taskValue(t, f) {
    if (f.id === 'priority') return t.priority || 'normal';
    if (f.type === 'labels') return t.labels || [];
    return t[f.id] === undefined || t[f.id] === null ? '' : t[f.id];
  }
  function canRunNow(t) { return !t.needs_input && (t.stage === 'todo' || t.stage === 'approved' || (t.stage === 'planning' && !!t.revise)); }

  /* Links to other tasks: list with a two-tap Remove (blocked by / depends on / relates to; the inverse rows are read-only here: remove them from the other task) and an Add form. */
  function linksCard(t) {
    var msg = msgLine(), rels = t.relations || [];
    var rows = rels.map(function (r) {
      var own = r.kind === 'blocked_by' || r.kind === 'depends_on' || r.kind === 'relates_to';
      var open = el('button', { 'class': 't-card', type: 'button', 'aria-label': 'Open task ' + r.task + ': ' + r.title, on: { click: function () { openTask(r.task); } } },
        [el('b', { text: '#' + r.task }), r.title, el('small', { text: stageLabel(r.stage) + (r.waiting ? ' · still open, so this task is blocked' : '') })]);
      var rm = own ? confirmButton('Remove', '', function () { act(msg, api('POST', '/tasks/' + t.id + '/unrelate', { kind: r.kind, task: r.task })); }) : null;
      if (rm) rm.setAttribute('aria-label', 'Remove link: ' + REL_TEXT[r.kind] + ' #' + r.task);
      return el('li', { 'class': 't-rel' }, [el('span', { 'class': 't-rel-kind', text: REL_TEXT[r.kind] || r.kind }), open, rm]);
    });
    var kind = el('select', {}, ['blocked_by', 'depends_on', 'relates_to'].map(function (k) { return el('option', { value: k, text: REL_TEXT[k] }); }));
    var num = el('input', { type: 'number', min: '1', step: '1', inputmode: 'numeric', placeholder: 'task number' });
    var add = el('button', { 'class': 't-button', type: 'button', text: 'Add link', on: { click: function () {
      var n = parseInt(num.value, 10);
      if (!n || n < 1) { msg.setAttribute('data-tone', 'error'); msg.textContent = 'Give the number of the other task.'; num.focus(); return; }
      act(msg, api('POST', '/tasks/' + t.id + '/relate', { kind: kind.value, task: n }));
    } } });
    return el('ih-card', { id: 'links-card' }, [el('h3', { text: 'Links to other tasks' + (t.blocked ? ' (blocked)' : '') }),
      rows.length ? el('ul', { 'class': 't-history t-rels' }, rows) : el('p', { 'class': 't-empty', text: 'No links yet.' }),
      el('div', { 'class': 't-rel-add' }, [field('Kind', kind, 'd-rel-kind'), field('Other task number', num, 'd-rel-task'), add]), msg]);
  }
  function questionForm(t, msg) {
    var q = t.question, inputs = {};
    var box = el('ih-card', {}, [el('h3', { text: 'Needs your input' })]);
    q.questions.forEach(function (qq, i) {
      var name = 'q-' + qq.id, wrap;
      if (qq.type === 'text') {
        var ta = el('textarea', { rows: '3' });
        inputs[qq.id] = function () { return ta.value.trim(); };
        wrap = field((i + 1) + '. ' + qq.text, ta, name);
      } else {
        var opts = qq.type === 'yesno' ? ['Yes', 'No'] : qq.options, boxes = [];
        var fs = el('fieldset', {}, [el('legend', { 'class': 't-legend', text: (i + 1) + '. ' + qq.text })]);
        opts.forEach(function (o, j) {
          var inp = el('input', { type: qq.type === 'multi' ? 'checkbox' : 'radio', name: name, id: name + '-' + j, value: o });
          boxes.push(inp);
          fs.appendChild(el('label', { 'class': 't-option', 'for': name + '-' + j }, [inp, o]));
        });
        inputs[qq.id] = function () {
          var picked = boxes.filter(function (x) { return x.checked; }).map(function (x) { return x.value; });
          if (qq.type === 'multi') return picked;
          if (!picked.length) return undefined;
          return qq.type === 'yesno' ? picked[0] === 'Yes' : picked[0];
        };
        wrap = el('div', { 'class': 't-field' }, [fs]);
      }
      box.appendChild(wrap);
    });
    var note = el('textarea', { rows: '2' });
    box.appendChild(field('Anything else (optional)', note, 'q-note'));
    box.appendChild(el('p', { 'class': 'ih-metadata', text: 'After you answer: ' + q.after }));
    box.appendChild(el('div', { 'class': 't-actions' }, [el('button', { 'class': 't-button', 'data-variant': 'primary', type: 'button', text: 'Submit answers', on: { click: function () {
      var answers = {}, missing = false;
      q.questions.forEach(function (qq) { var v = inputs[qq.id](); if (v === undefined || v === '') missing = true; answers[qq.id] = v; });
      if (missing) { msg.setAttribute('data-tone', 'error'); msg.textContent = 'Answer every question first.'; return; }
      act(msg, api('POST', '/tasks/' + t.id + '/answer', { answers: answers, note: note.value.trim() }));
    } } })]));
    return box;
  }
  function renderOldDetail() {
    var t = state.open, d = $('detail'); clear(d); if (!t) return;
    var msg = msgLine();
    var head = [el('h2', { text: '#' + t.id + ' ' + t.title }),
      el('p', { 'class': 'ih-metadata', text: [t.stage_label, 'Priority ' + (t.priority || 'normal'), t.due ? 'Due ' + t.due : '', t.raised_by ? 'Raised by ' + t.raised_by : '', WAIT_TEXT[t.waiting_on] || '', t.run_now ? 'Work now requested' : '',
        t.remind_at ? 'Reminder on ' + t.remind_at : '', t.stage === 'closed' ? closeLabel(t.closed_reason) + (t.duplicate_of ? ' of #' + t.duplicate_of : '') + (t.closed_detail ? ': ' + t.closed_detail : '') : ''].filter(Boolean).join(' · ') })];
    var root = el('div', { 'class': 't-detail' }, [el('div', {}, head.concat([labelChips(t)]))]);
    if (t.question) root.appendChild(questionForm(t, msg));

    var actions = el('div', { 'class': 't-actions' });
    var comment = el('textarea', { rows: '3' });
    if (t.stage === 'planning' && !t.question) {
      actions.appendChild(confirmButton('Approve', 'primary', function () { act(msg, api('POST', '/tasks/' + t.id + '/move', { to: 'approved' })); }));
      actions.appendChild(el('button', { 'class': 't-button', type: 'button', text: 'Reject (send back with the comment)', on: { click: function () {
        if (!comment.value.trim()) { msg.setAttribute('data-tone', 'error'); msg.textContent = 'Write in the comment box what should change.'; comment.focus(); return; }
        act(msg, api('POST', '/tasks/' + t.id + '/reject', { comment: comment.value.trim() }));
      } } }));
    }
    if (t.stage === 'approved' || t.stage === 'implementing' || t.stage === 'verifying') {
      /* Replan: back to Planning, flagged for the worker, with the comment (if any) as the reason */
      actions.appendChild(confirmButton('Replan (back to Planning)', '', function () {
        var why = 'Replan requested by the owner: check the CURRENT state of the house and the code first (what is already built, merged, installed or changed since the plan was written), then write a new plan for what is still needed.' + (comment.value.trim() ? ' Owner note: ' + comment.value.trim() : '');
        act(msg, api('POST', '/tasks/' + t.id + '/move', { to: 'planning', reason: why }));
      }));
    }
    if (t.stage === 'verifying') {
      actions.appendChild(confirmButton('I have checked it: done', 'primary', function () { act(msg, api('POST', '/tasks/' + t.id + '/move', { to: 'done' })); }));
    }
    /* ONE Save: sends only the fields that changed (one request) and the comment text (if any); disabled until something changes */
    var editCtl = [], save = el('button', { 'class': 't-button', 'data-variant': 'primary', type: 'button', text: 'Save', disabled: true });
    var sync = function () { save.disabled = !(comment.value.trim() || editCtl.some(function (c) { return c.dirty(); })); };
    comment.addEventListener('input', sync);
    if (isOpen(t)) fieldsFor('edit').forEach(function (f) { editCtl.push(buildField(f, taskValue(t, f), 'd-', sync, t.id)); });
    if (state.draft) { comment.value = state.draft; state.draft = ''; sync(); }
    save.addEventListener('click', function () {
      var body = {}, names = [], text = comment.value.trim();
      editCtl.forEach(function (c) { if (c.dirty()) { body[c.id] = c.get(); names.push(c.f.label.replace(/ \(.*\)$/, '').toLowerCase()); } });
      if (!names.length && !text) return;
      if (body.title === '') { msg.setAttribute('data-tone', 'error'); msg.textContent = 'A title is needed.'; return; }
      if (text) names.push('comment');
      save.disabled = true; msg.removeAttribute('data-tone'); msg.textContent = 'Saving…';
      var first = Object.keys(body).length ? api('POST', '/tasks/' + t.id + '/edit', body) : Promise.resolve(t);
      first.then(function (nt) {
        state.open = nt;
        if (!text) return nt;
        return api('POST', '/tasks/' + t.id + '/comment', { text: text }).then(function (n2) { state.open = n2; return n2; }).catch(function (e) {
          var er = new Error('The other changes were saved, but the comment was not: ' + e.message); er.partial = true; throw er;
        });
      }).then(function () { state.flash = 'Saved: ' + names.join(', ') + '.'; renderDetail(); loadBoard(); })
        .catch(function (e) {
          if (e.partial) { state.flash = e.message; state.flashTone = 'error'; state.draft = text; renderDetail(); loadBoard(); return; }
          msg.setAttribute('data-tone', 'error'); msg.textContent = e.message; sync();
        });
    });
    actions.appendChild(save);
    var details = editCtl.length ? el('ih-card', {}, [el('h3', { text: 'Details' })].concat(editCtl.map(function (c) { return c.node; }))) : null;
    if (details) root.appendChild(details);
    var card = el('ih-card', {}, [el('h3', { text: t.stage === 'planning' && !t.question ? 'Your decision' : 'Comment' }),
      t.stage === 'planning' && t.question ? el('p', { 'class': 'ih-metadata', text: 'Approve and Reject appear when no questions are open.' }) : null,
      field('Comment (a note on this task; it never makes a new task)', comment, 'd-comment'), actions, msg]);
    root.appendChild(card);
    if (state.flash) { msg.textContent = state.flash; if (state.flashTone) msg.setAttribute('data-tone', state.flashTone); state.flash = ''; state.flashTone = ''; }
    if (isOpen(t) && (t.run_now || canRunNow(t))) {
      /* Work this now: asks a worker to take this task next, ignoring pace and caps (the worker side runs on the host); two taps, no dialog */
      var mw = msgLine();
      root.appendChild(el('ih-card', {}, [el('h3', { text: 'Work this now' }),
        el('p', { 'class': 'ih-metadata', text: t.run_now ? 'A worker has been asked to take this task next. It starts when the worker next looks (the STOP switch still applies).' : 'Asks a worker to take this task next, ignoring the usual pace and daily limits. It plans a To do task and builds an Approved one; nothing is built before you approve.' }),
        el('div', { 'class': 't-actions' }, [t.run_now
          ? confirmButton('Cancel work now', '', function () { act(mw, api('POST', '/tasks/' + t.id + '/run_now', { on: false })); })
          : confirmButton('Work this now', 'primary', function () { act(mw, api('POST', '/tasks/' + t.id + '/run_now', { on: true })); })]), mw]));
    }

    if (t.parent) root.appendChild(el('ih-card', {}, [el('h3', { text: 'Part of' }), el('button', { 'class': 't-card', type: 'button', 'aria-label': 'Open task ' + t.parent.id + ': ' + t.parent.title, on: { click: function () { openTask(t.parent.id); } } },
      [el('b', { text: '#' + t.parent.id }), t.parent.title, el('small', { text: t.parent.stage_label + ' · approved together with this task' })])]));
    if ((t.children || []).length) root.appendChild(el('ih-card', {}, [el('h3', { text: 'Child tasks (' + (t.children_total - t.children_open) + ' of ' + t.children_total + ' finished)' }), el('ul', { 'class': 't-history' }, t.children.map(function (c) {
      return el('li', {}, [el('button', { 'class': 't-card', type: 'button', 'aria-label': 'Open task ' + c.id + ': ' + c.title, on: { click: function () { openTask(c.id); } } }, [el('b', { text: '#' + c.id }), c.title, el('small', { text: c.stage_label })])]);
    }))]));
    root.appendChild(linksCard(t));
    if (t.plan) root.appendChild(el('ih-card', {}, [el('h3', { text: 'Plan' + (t.revise ? ' (being revised)' : '') }), window.IHTasksMarkdown.render(t.plan)]));
    if (t.result) root.appendChild(el('ih-card', {}, [el('h3', { text: 'Result' }), el('p', { 'class': 't-text', text: t.result })]));
    if (t.description && !isOpen(t)) root.appendChild(el('ih-card', {}, [el('h3', { text: 'Description' }), el('p', { 'class': 't-text', text: t.description })]));
    if (t.links.length) {
      root.appendChild(el('ih-card', {}, [el('h3', { text: 'Results and links' }), el('ul', { 'class': 't-history' }, t.links.map(function (l) {
        var safe = /^https?:\/\//.test(l.url);
        return el('li', {}, [el('time', { text: when(l.at) }), l.kind.toUpperCase() + ' ', safe ? el('a', { href: l.url, target: '_blank', rel: 'noopener noreferrer', text: l.url }) : null, l.text ? ' ' + l.text : '']);
      }))]));
    }

    if (isOpen(t)) {
      /* Remind me: a future date; the task is closed as "Reminder set" and comes back to To do (high priority) that day, with a message from Claude */
      var rd = el('input', { type: 'date' }), rn = el('input', { type: 'text', maxlength: '500', placeholder: 'what to remember (optional)' }), mr = msgLine();
      var tomorrow = new Date(Date.now() + 86400000); rd.min = tomorrow.getFullYear() + '-' + ('0' + (tomorrow.getMonth() + 1)).slice(-2) + '-' + ('0' + tomorrow.getDate()).slice(-2);
      root.appendChild(el('ih-card', {}, [el('h3', { text: 'Remind me later' }),
        el('p', { 'class': 'ih-metadata', text: 'Closes this task as "Reminder set". On that day it comes back to To do with high priority and Claude messages you.' }),
        field('Remind me on', rd, 'd-remind-date'), field('Note (optional)', rn, 'd-remind-note'),
        el('div', { 'class': 't-actions' }, [confirmButton('Set reminder and close', 'primary', function () {
          if (!rd.value) { mr.setAttribute('data-tone', 'error'); mr.textContent = 'Pick a date.'; rd.focus(); return; }
          act(mr, api('POST', '/tasks/' + t.id + '/remind', { date: rd.value, note: rn.value.trim() }));
        })]), mr]));
    }
    /* close or reopen */
    if (t.stage === 'closed' || t.stage === 'done') {
      var why = el('input', { type: 'text', maxlength: '500' }), m2 = msgLine();
      root.appendChild(el('ih-card', {}, [el('h3', { text: 'Reopen' }), field('Why reopen it', why, 'd-reopen'),
        el('div', { 'class': 't-actions' }, [confirmButton('Reopen as To do', '', function () { act(m2, api('POST', '/tasks/' + t.id + '/move', { to: 'todo', reason: why.value.trim() })); })]), m2]));
    } else {
      var sel = el('select', {}, (state.meta ? state.meta.close_reasons : []).map(function (r) { return el('option', { value: r.id, text: r.label }); }));
      var dup = el('input', { type: 'number', min: '1', step: '1', inputmode: 'numeric' }), det = el('input', { type: 'text', maxlength: '500' }), m3 = msgLine();
      var dupField = field('Duplicate of task number', dup, 'd-dup');
      var sync = function () { dupField.hidden = sel.value !== 'duplicate'; };
      sel.addEventListener('change', sync);
      root.appendChild(el('ih-card', {}, [el('h3', { text: 'Close without doing it' }), field('Reason (needed)', sel, 'd-reason'), dupField, field('Detail (optional)', det, 'd-detail'),
        el('div', { 'class': 't-actions' }, [confirmButton('Close task', 'danger', function () {
          var body = { reason: sel.value, detail: det.value.trim() };
          if (sel.value === 'duplicate') body.duplicate_of = parseInt(dup.value, 10) || 0;
          act(m3, api('POST', '/tasks/' + t.id + '/close', body));
        })]), m3]));
      sync();
    }

    root.appendChild(el('ih-card', {}, [el('h3', { text: 'History' }), el('ul', { 'class': 't-history' }, t.history.map(function (h) {
      var line = (KIND_TEXT[h.kind] || h.kind) + ' by ' + h.actor;
      if (h.kind === 'moved') line = stageLabel(h.from) + ' → ' + stageLabel(h.to) + ' by ' + h.actor;
      if ((h.kind === 'merged' || h.kind === 'merged_into') && h.data && h.data.task) line = (h.kind === 'merged' ? 'Merged in #' : 'Merged into #') + h.data.task + ' by ' + h.actor;
      if (h.kind === 'closed' && h.data && h.data.reason) line += ' (' + closeLabel(h.data.reason) + (h.data.duplicate_of ? ' of #' + h.data.duplicate_of : '') + ')';
      var extra = h.text || '';
      if (h.kind === 'answered' && h.data && h.data.answers) extra = Object.keys(h.data.answers).map(function (k) { var v = h.data.answers[k]; return k + ': ' + (v === true ? 'Yes' : v === false ? 'No' : Array.isArray(v) ? v.join(', ') : v); }).join(' | ') + (extra ? ' | ' + extra : '');
      if (h.data && h.data.result) extra += (extra ? ' | ' : '') + 'Result: ' + h.data.result;
      return el('li', {}, [el('time', { datetime: h.at, text: when(h.at) }), el('b', { text: line }), extra ? el('p', { 'class': 't-text', text: extra }) : null]);
    }))]));
    if (t.legacy.length) root.appendChild(el('p', { 'class': 'ih-metadata', text: 'Old ids: ' + t.legacy.map(function (l) { return l.kind.replace('_', ' ') + ' ' + l.value; }).join(', ') }));
    d.appendChild(root);
  }
  function renderDetail() {
    detailDirty = false;
    renderOldDetail();
    $('view-new').setAttribute('aria-pressed', detailView === 'new' ? 'true' : 'false');
    $('view-old').setAttribute('aria-pressed', detailView === 'old' ? 'true' : 'false');
    if (detailView === 'new' && state.open) arrangeNewDetail();
  }

  function arrangeNewDetail() {
    var t = state.open, container = $('detail'), old = container.firstChild;
    var children = Array.prototype.slice.call(old.children), sections = {};
    children.slice(1).forEach(function (node) {
      var heading = node.querySelector('h3');
      if (heading) sections[heading.textContent] = node;
    });
    var root = el('div', { 'class': 't-detail t-detail-new' });
    var summary = el('section', { 'class': 't-overview' }, [
      el('div', { 'class': 't-overview-top' }, [el('span', { 'class': 't-eyebrow', text: 'TASK #' + t.id }), el('span', { 'class': 't-stage', text: t.stage_label })]),
      el('h2', { text: t.title }),
      el('p', { 'class': 't-overview-meta', text: [WAIT_TEXT[t.waiting_on] || '', (t.priority || 'normal') + ' priority', t.due ? 'Due ' + t.due : '', t.raised_by ? 'Raised by ' + t.raised_by : '', t.run_now ? 'Work now requested' : '', t.remind_at ? 'Reminder ' + t.remind_at : '', t.stage === 'closed' ? closeLabel(t.closed_reason) + (t.duplicate_of ? ' of #' + t.duplicate_of : '') + (t.closed_detail ? ': ' + t.closed_detail : '') : ''].filter(Boolean).join(' · ') }),
      labelChips(t), t.description ? el('p', { 'class': 't-text t-overview-description', text: t.description }) : el('p', { 'class': 't-empty', text: 'No description yet.' })]);
    root.appendChild(summary);
    var message = msgLine();
    if (sections['Needs your input']) {
      var question = sections['Needs your input'];
      var oldMessage = old.querySelector('.t-msg');
      if (oldMessage) question.appendChild(oldMessage);
      root.appendChild(question);
    }
    var details = el('details', { 'class': 't-disclosure' }, [el('summary', { text: 'Details' })]);
    root.appendChild(details);
    if (sections['Plan'] || sections['Plan (being revised)'] || (t.stage === 'planning' && !t.question)) {
      var plan = sections['Plan'] || sections['Plan (being revised)'] || el('ih-card', {}, [el('h3', { text: 'Plan' }), el('p', { 'class': 't-empty', text: 'No plan yet.' })]);
      if (t.stage === 'planning' && !t.question) {
        var decisionMsg = msgLine(), reason = el('textarea', { rows: '3', placeholder: 'What would you like changed?' });
        var request = el('details', { 'class': 't-inline-action' }, [el('summary', { text: 'Request changes' }), field('What should change?', reason, 'new-reject-reason'),
          el('button', { 'class': 't-button', type: 'button', text: 'Send back for changes', on: { click: function () {
            if (!reason.value.trim()) { decisionMsg.setAttribute('data-tone', 'error'); decisionMsg.textContent = 'Describe what should change.'; reason.focus(); return; }
            act(decisionMsg, api('POST', '/tasks/' + t.id + '/reject', { comment: reason.value.trim() }));
          } } })]);
        plan.appendChild(el('div', { 'class': 't-plan-actions' }, [confirmButton('Approve plan', 'primary', function () { act(decisionMsg, api('POST', '/tasks/' + t.id + '/move', { to: 'approved' })); }), request, decisionMsg]));
      }
      root.appendChild(plan);
    }
    if (sections['Result']) root.appendChild(sections['Result']);
    if (t.stage === 'verifying') root.appendChild(el('ih-card', {}, [el('h3', { text: 'Ready to check' }), confirmButton('I have checked it: done', 'primary', function () { act(message, api('POST', '/tasks/' + t.id + '/move', { to: 'done' })); }), message]));

    var comments = t.comments || [], commentBox = el('textarea', { rows: '3', placeholder: 'Write a comment…', maxlength: '4000' }), commentMsg = msgLine();
    var commentButton = el('button', { 'class': 't-button', 'data-variant': 'primary', type: 'button', text: 'Add comment' });
    commentButton.addEventListener('click', function () {
      var text = commentBox.value.trim();
      if (!text) { commentMsg.setAttribute('data-tone', 'error'); commentMsg.textContent = 'Write a comment first.'; commentBox.focus(); return; }
      commentButton.disabled = true; commentMsg.removeAttribute('data-tone'); commentMsg.textContent = 'Adding comment…';
      api('POST', '/tasks/' + t.id + '/comment', { text: text }).then(function (updated) {
        state.open = updated; state.flash = 'Comment added.'; renderDetail(); loadBoard();
      }).catch(function (e) { commentButton.disabled = false; commentMsg.setAttribute('data-tone', 'error'); commentMsg.textContent = e.message; });
    });
    var commentList = comments.length ? el('ol', { 'class': 't-comments' }, comments.map(function (c) {
      return el('li', {}, [el('div', { 'class': 't-comment-meta' }, [el('strong', { text: c.actor }), el('time', { datetime: c.at, text: when(c.at) })]), el('p', { 'class': 't-text', text: c.text })]);
    })) : el('p', { 'class': 't-empty', text: 'No comments yet.' });
    root.appendChild(el('ih-card', { 'class': 't-comments-card' }, [el('div', { 'class': 't-section-heading' }, [el('h3', { text: 'Comments' }), el('span', { 'class': 't-count', text: String(comments.length) })]), commentList,
      el('div', { 'class': 't-comment-compose' }, [field('Add a comment', commentBox, 'new-comment'), commentButton, commentMsg])]));

    var related = el('details', { 'class': 't-disclosure' }, [el('summary', { text: 'Advanced details' })]);
    children.forEach(function (node) {
      var h = node.querySelector('h3'), title = h ? h.textContent : '';
      if (title === 'Part of' || title.indexOf('Child tasks (') === 0 || title.indexOf('Links to other tasks') === 0 || title === 'Results and links') related.appendChild(node);
    });
    if (isOpen(t)) {
      var edit = el('div', { 'class': 't-edit-details' }, [el('h3', { text: 'Edit details' })]), controls = [], editMsg = msgLine();
      var save = el('button', { 'class': 't-button', 'data-variant': 'primary', type: 'button', text: 'Save details', disabled: true });
      var sync = function () { save.disabled = !controls.some(function (c) { return c.dirty(); }); };
      fieldsFor('edit').forEach(function (f) { var c = buildField(f, taskValue(t, f), 'new-', sync, t.id); controls.push(c); edit.appendChild(c.node); });
      save.addEventListener('click', function () {
        var body = {}; controls.forEach(function (c) { if (c.dirty()) body[c.id] = c.get(); });
        if (body.title === '') { editMsg.setAttribute('data-tone', 'error'); editMsg.textContent = 'A title is needed.'; return; }
        save.disabled = true;
        api('POST', '/tasks/' + t.id + '/edit', body).then(function (updated) { state.open = updated; state.flash = 'Details saved.'; renderDetail(); loadBoard(); })
          .catch(function (e) { editMsg.setAttribute('data-tone', 'error'); editMsg.textContent = e.message; sync(); });
      });
      edit.appendChild(save); edit.appendChild(editMsg); details.appendChild(edit);
    }
    details.appendChild(related);
    var actions = el('details', { 'class': 't-disclosure' }, [el('summary', { text: 'Actions' })]);
    var actionItems = [];
    [['Work this now', 'Work now'], ['Remind me later', 'Remind me'], ['Reopen', 'Reopen'], ['Close without doing it', 'Close task']].forEach(function (entry) {
      if (sections[entry[0]]) actionItems.push({ label: entry[1], card: sections[entry[0]] });
    });
    if (t.stage === 'approved' || t.stage === 'implementing' || t.stage === 'verifying') {
      var replanNote = el('textarea', { rows: '3', placeholder: 'Optional note for the worker' }), replanMsg = msgLine();
      actionItems.push({ label: 'Replan', card: el('ih-card', {}, [el('h3', { text: 'Replan' }), field('What should the new plan consider?', replanNote, 'new-replan'),
        confirmButton('Send back to planning', '', function () {
          var why = 'Replan requested by the owner: check the CURRENT state of the house and the code first (what is already built, merged, installed or changed since the plan was written), then write a new plan for what is still needed.' + (replanNote.value.trim() ? ' Owner note: ' + replanNote.value.trim() : '');
          act(replanMsg, api('POST', '/tasks/' + t.id + '/move', { to: 'planning', reason: why }));
        }), replanMsg]) });
    }
    if (actionItems.length) {
      var tabs = el('div', { 'class': 't-action-tabs', role: 'tablist', 'aria-label': 'Task actions' }), panels = [];
      function selectAction(index, focus) {
        actionItems.forEach(function (item, i) {
          item.tab.setAttribute('aria-selected', i === index ? 'true' : 'false');
          item.tab.tabIndex = i === index ? 0 : -1;
          panels[i].hidden = i !== index;
        });
        if (focus) actionItems[index].tab.focus();
      }
      actionItems.forEach(function (item, i) {
        var tabId = 'action-tab-' + i, panelId = 'action-panel-' + i;
        item.tab = el('button', { 'class': 't-action-tab', id: tabId, type: 'button', role: 'tab', 'aria-controls': panelId, text: item.label });
        item.tab.addEventListener('click', function () { selectAction(i, false); });
        item.tab.addEventListener('keydown', function (event) {
          var next = event.key === 'ArrowRight' ? (i + 1) % actionItems.length : event.key === 'ArrowLeft' ? (i + actionItems.length - 1) % actionItems.length : event.key === 'Home' ? 0 : event.key === 'End' ? actionItems.length - 1 : -1;
          if (next >= 0) { event.preventDefault(); selectAction(next, true); }
        });
        tabs.appendChild(item.tab);
        panels.push(el('div', { 'class': 't-action-panel', id: panelId, role: 'tabpanel', 'aria-labelledby': tabId }, [item.card]));
      });
      actions.appendChild(tabs);
      panels.forEach(function (panel) { actions.appendChild(panel); });
      selectAction(0, false);
      root.insertBefore(actions, details.nextSibling);
    }
    if (sections['History']) root.appendChild(el('details', { 'class': 't-disclosure' }, [el('summary', { text: 'Activity' }), sections['History']]));
    var legacy = children[children.length - 1];
    if (legacy && legacy.tagName === 'P') root.appendChild(legacy);
    var previousMsg = old.querySelector('.t-msg');
    if (previousMsg && previousMsg.textContent && !sections['Needs your input']) root.appendChild(el('p', { 'class': 'ih-metadata', role: 'status', text: previousMsg.textContent }));
    clear(container); container.appendChild(root);
  }

  function openTask(id) {
    api('GET', '/tasks/' + id).then(function (t) {
      state.open = t; $('board-view').hidden = true; $('detail-view').hidden = false; renderDetail(); renderMergeBar(); window.scrollTo(0, 0); $('back').focus();
      trail([{ label: 'Tasks', onClick: closeTask }], 'Task ' + t.id);
    }).catch(function (e) { $('f-count').textContent = e.message; });
  }
  function closeTask() { state.open = null; $('detail-view').hidden = true; $('board-view').hidden = false; trail([], 'Tasks'); loadBoard(); }
  function trail(list, title) { var nav = document.querySelector('ih-nav'); if (nav && typeof nav.setTrail === 'function') nav.setTrail(list, title); }

  /* ---- start ---- */
  ['input', 'change'].forEach(function (event) { $('detail').addEventListener(event, function (e) {
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) detailDirty = true;
  }); });
  ['new', 'old'].forEach(function (view) { var button = $('view-' + view), label = button.textContent;
    button.addEventListener('click', function () {
      if (detailView === view) return;
      if (detailDirty && button.getAttribute('data-armed') !== 'true') {
        button.setAttribute('data-armed', 'true'); button.textContent = 'Tap again to discard edits';
        clearTimeout(switchTimer); switchTimer = setTimeout(function () { button.removeAttribute('data-armed'); button.textContent = label; }, 5000); return;
      }
      clearTimeout(switchTimer); button.removeAttribute('data-armed'); button.textContent = label; detailView = view; renderDetail();
    });
  });
  $('back').addEventListener('click', closeTask);
  $('merge-toggle').addEventListener('click', function () { setMerging(!state.merging); });
  $('dup-toggle').addEventListener('click', function () { if (state.dups) { state.dups = null; renderDups(); } else { $('tools-msg').textContent = ''; loadDups(); } });
  ['f-search', 'f-stage', 'f-waiting', 'f-label'].forEach(function (id) { $(id).addEventListener('input', renderBoard); });
  $('f-clear').addEventListener('click', function () { $('f-search').value = ''; $('f-stage').value = ''; $('f-waiting').value = ''; $('f-label').value = ''; renderBoard(); });
  $('add-form').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var title = $('add-title').value.trim(), m = $('add-msg');
    if (!title) return;
    m.textContent = 'Saving…';
    api('POST', '/tasks', { title: title }).then(function (r) {
      $('add-title').value = '';
      m.textContent = r.duplicate_of ? 'Task ' + r.task.id + ' looked the same as task ' + r.duplicate_of + ', so it was closed as a duplicate. Open it and reopen it if that is wrong.' : 'Added task ' + r.task.id + '.';
      loadBoard();
    }).catch(function (e) { m.textContent = e.message; });
  });
  /* New-task panel: a hidden element, not a browser dialog (dialogs can crash the phone app). Its fields come from the shared field list. */
  var newCtl = [];
  function newBuild() {
    var box = $('new-fields'); clear(box); newCtl = [];
    fieldsFor('create').forEach(function (f) {
      var init = f.type === 'labels' || f.type === 'tasks' ? [] : f.type === 'readonly' ? (state.meta.user || 'You (filled in when it is saved)') : (f.default || '');
      var c = buildField(f, init, 'new-', function () {}, null);
      newCtl.push(c); box.appendChild(c.node);
    });
  }
  function newOpen() { newBuild(); $('new-msg').textContent = ''; $('new-msg').removeAttribute('data-tone'); $('new-modal').hidden = false; if (newCtl[0] && newCtl[0].focus) newCtl[0].focus.focus(); }
  function newClose() { $('new-modal').hidden = true; $('new-open').focus(); }
  $('new-open').addEventListener('click', newOpen);
  $('new-cancel').addEventListener('click', newClose);
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape' && !$('new-modal').hidden) newClose(); });
  $('new-form').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var m = $('new-msg'), go = $('new-save'), body = {};
    newCtl.forEach(function (c) {
      var v = c.get();
      if (v === undefined || v === '' || (Array.isArray(v) && !v.length)) return;
      body[c.id] = v;
    });
    if (!body.title) { m.setAttribute('data-tone', 'error'); m.textContent = 'A title is needed.'; return; }
    go.disabled = true; m.removeAttribute('data-tone'); m.textContent = 'Saving…';
    api('POST', '/tasks', body).then(function (r) {
      go.disabled = false;
      newClose();
      $('add-msg').textContent = r.duplicate_of ? 'Task ' + r.task.id + ' looked the same as task ' + r.duplicate_of + ', so it was closed as a duplicate. Open it and reopen it if that is wrong.' : 'Added task ' + r.task.id + '.';
      loadBoard();
    }).catch(function (e) { go.disabled = false; m.setAttribute('data-tone', 'error'); m.textContent = e.message; });
  });
  fetch('ui.json', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (u) {
    if (u.purpose) $('purpose').textContent = u.purpose;
    if (u.access) $('access').textContent = u.access;
  }).catch(function () {});
  api('GET', '/meta').then(function (m) {
    state.meta = m;
    m.stages.forEach(function (s) { $('f-stage').appendChild(el('option', { value: s.id, text: s.label })); });
  }).catch(function () {}).then(loadBoard);
  setInterval(function () { if (!state.open && !state.merging && document.visibilityState === 'visible') loadBoard(); }, 60000);
})();
