/* ih_tasks page: Kanban board + task detail. Talks to api/ on the ingress server, which always acts as the owner.
   No markup is built from text: every node is made with createElement and textContent. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var state = { board: null, meta: null, open: null };
  var WAIT_TEXT = { owner: 'Waiting for you', worker: 'With the worker', check: 'Being checked' };
  var KIND_TEXT = { created: 'Created', moved: 'Moved', closed: 'Closed', comment: 'Comment', asked: 'Question asked', answered: 'Answered',
    rejected: 'Plan rejected', link: 'Link added', plan: 'Plan', edited: 'Edited', imported: 'Carried over', question_withdrawn: 'Question withdrawn',
    duplicate_linked: 'Duplicate' };

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
    if (st && t.stage !== st) return false;
    if (w === 'input' && !t.needs_input) return false;
    if (w && w !== 'input' && t.waiting_on !== w) return false;
    if (q && t.title.toLowerCase().indexOf(q) < 0 && String(t.id) !== q) return false;
    return true;
  }
  function card(t) {
    var flags = [];
    if (t.needs_input) flags.push(el('span', { 'class': 't-flag', 'data-tone': 'warn', text: 'Needs your input' }));
    else if (t.waiting_on === 'owner') flags.push(el('span', { 'class': 't-flag', 'data-tone': 'warn', text: t.stage === 'planning' ? 'Plan ready for you' : 'Waiting for you' }));
    if (t.stage === 'planning' && t.revise) flags.push(el('span', { 'class': 't-flag', text: 'Plan being revised' }));
    var sub = [];
    if (t.due) sub.push('Due ' + t.due);
    if (t.stage === 'closed' && t.closed_reason) sub.push(closeLabel(t.closed_reason) + (t.duplicate_of ? ' of #' + t.duplicate_of : ''));
    return el('button', { 'class': 't-card', type: 'button', 'aria-label': 'Task ' + t.id + ': ' + t.title, on: { click: function () { openTask(t.id); } } },
      [el('b', { text: '#' + t.id }), t.title, sub.length ? el('small', { text: sub.join(' · ') }) : null, flags.length ? el('span', { 'class': 't-flags' }, flags) : null]);
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
  function loadBoard() {
    return api('GET', '/board').then(function (b) {
      state.board = b;
      var s = $('service-status');
      s.setAttribute('status', b.needs_input ? 'warn' : 'ok');
      s.textContent = b.needs_input ? b.needs_input + ' need your input' : 'Up to date';
      renderBoard();
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
  function renderDetail() {
    var t = state.open, d = $('detail'); clear(d); if (!t) return;
    var msg = msgLine();
    var head = [el('h2', { text: '#' + t.id + ' ' + t.title }),
      el('p', { 'class': 'ih-metadata', text: [t.stage_label, t.due ? 'Due ' + t.due : '', WAIT_TEXT[t.waiting_on] || '',
        t.stage === 'closed' ? closeLabel(t.closed_reason) + (t.duplicate_of ? ' of #' + t.duplicate_of : '') + (t.closed_detail ? ': ' + t.closed_detail : '') : ''].filter(Boolean).join(' · ') })];
    var root = el('div', { 'class': 't-detail' }, [el('div', {}, head)]);
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
    if (t.stage === 'verifying') {
      actions.appendChild(confirmButton('I have checked it: done', 'primary', function () { act(msg, api('POST', '/tasks/' + t.id + '/move', { to: 'done' })); }));
    }
    actions.appendChild(el('button', { 'class': 't-button', type: 'button', text: 'Save comment', on: { click: function () {
      if (!comment.value.trim()) { msg.setAttribute('data-tone', 'error'); msg.textContent = 'The comment is empty.'; return; }
      act(msg, api('POST', '/tasks/' + t.id + '/comment', { text: comment.value.trim() }));
    } } }));
    var card = el('ih-card', {}, [el('h3', { text: t.stage === 'planning' && !t.question ? 'Your decision' : 'Comment' }),
      t.stage === 'planning' && t.question ? el('p', { 'class': 'ih-metadata', text: 'Approve and Reject appear when no questions are open.' }) : null,
      field('Comment (a note on this task; it never makes a new task)', comment, 'd-comment'), actions, msg]);
    root.appendChild(card);

    if (t.plan) root.appendChild(el('ih-card', {}, [el('h3', { text: 'Plan' + (t.revise ? ' (being revised)' : '') }), el('p', { 'class': 't-text', text: t.plan })]));
    if (t.result) root.appendChild(el('ih-card', {}, [el('h3', { text: 'Result' }), el('p', { 'class': 't-text', text: t.result })]));
    if (t.description) root.appendChild(el('ih-card', {}, [el('h3', { text: 'Description' }), el('p', { 'class': 't-text', text: t.description })]));
    if (t.links.length) {
      root.appendChild(el('ih-card', {}, [el('h3', { text: 'Results and links' }), el('ul', { 'class': 't-history' }, t.links.map(function (l) {
        var safe = /^https?:\/\//.test(l.url);
        return el('li', {}, [el('time', { text: when(l.at) }), l.kind.toUpperCase() + ' ', safe ? el('a', { href: l.url, target: '_blank', rel: 'noopener noreferrer', text: l.url }) : null, l.text ? ' ' + l.text : '']);
      }))]));
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
      if (h.kind === 'closed' && h.data && h.data.reason) line += ' (' + closeLabel(h.data.reason) + (h.data.duplicate_of ? ' of #' + h.data.duplicate_of : '') + ')';
      var extra = h.text || '';
      if (h.kind === 'answered' && h.data && h.data.answers) extra = Object.keys(h.data.answers).map(function (k) { var v = h.data.answers[k]; return k + ': ' + (v === true ? 'Yes' : v === false ? 'No' : Array.isArray(v) ? v.join(', ') : v); }).join(' | ') + (extra ? ' | ' + extra : '');
      if (h.data && h.data.result) extra += (extra ? ' | ' : '') + 'Result: ' + h.data.result;
      return el('li', {}, [el('time', { datetime: h.at, text: when(h.at) }), el('b', { text: line }), extra ? el('p', { 'class': 't-text', text: extra }) : null]);
    }))]));
    if (t.legacy.length) root.appendChild(el('p', { 'class': 'ih-metadata', text: 'Old ids: ' + t.legacy.map(function (l) { return l.kind.replace('_', ' ') + ' ' + l.value; }).join(', ') }));
    d.appendChild(root);
  }
  function openTask(id) {
    api('GET', '/tasks/' + id).then(function (t) {
      state.open = t; $('board-view').hidden = true; $('detail-view').hidden = false; renderDetail(); window.scrollTo(0, 0); $('back').focus();
      trail([{ label: 'Tasks', onClick: closeTask }], 'Task ' + t.id);
    }).catch(function (e) { $('f-count').textContent = e.message; });
  }
  function closeTask() { state.open = null; $('detail-view').hidden = true; $('board-view').hidden = false; trail([], 'Tasks'); loadBoard(); }
  function trail(list, title) { var nav = document.querySelector('ih-nav'); if (nav && typeof nav.setTrail === 'function') nav.setTrail(list, title); }

  /* ---- start ---- */
  $('back').addEventListener('click', closeTask);
  ['f-search', 'f-stage', 'f-waiting'].forEach(function (id) { $(id).addEventListener('input', renderBoard); });
  $('f-clear').addEventListener('click', function () { $('f-search').value = ''; $('f-stage').value = ''; $('f-waiting').value = ''; renderBoard(); });
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
  fetch('ui.json', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (u) {
    if (u.purpose) $('purpose').textContent = u.purpose;
    if (u.access) $('access').textContent = u.access;
  }).catch(function () {});
  api('GET', '/meta').then(function (m) {
    state.meta = m;
    m.stages.forEach(function (s) { $('f-stage').appendChild(el('option', { value: s.id, text: s.label })); });
  }).catch(function () {}).then(loadBoard);
  setInterval(function () { if (!state.open && document.visibilityState === 'visible') loadBoard(); }, 60000);
})();
