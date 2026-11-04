(function () {
  'use strict';
  const h = window.IHGameComponents = window.IHGameComponents || {};

  function render(data, api) {
    const wrap = api.el('div', null, 'details-content arc-raiders-details');
    const canEdit = typeof api.command === 'function' && typeof api.reload === 'function';
    let toastTimer = null;
    let doneOpen = false;

    function plainError(error) {
      const text = error && error.message ? error.message : '';
      return text ? 'Could not save: ' + text : 'Could not save. Check the connection and try again.';
    }

    // Send one change, re-fetch details, repaint. Returns true when saved.
    async function save(cmd, args, busy) {
      if (busy) busy.setAttribute('aria-busy', 'true');
      try {
        const result = await api.command(cmd, args);
        if (!/^(Marked|Saved)/.test(result.text || '')) throw new Error(result.text || 'The change was refused.');
        data = await api.reload();
        paint();
        return true;
      } catch (error) {
        if (busy) busy.removeAttribute('aria-busy');
        notice(plainError(error), true);
        return false;
      }
    }

    function notice(message, isError, undo) {
      const old = wrap.querySelector('.arc-toast');
      if (old) old.remove();
      clearTimeout(toastTimer);
      const bar = api.el('div', null, 'arc-toast' + (isError ? ' notice-error' : ''));
      bar.setAttribute('role', 'status');
      bar.appendChild(api.el('span', message));
      if (undo) {
        const b = api.el('button', 'Undo', 'ih-button'); b.type = 'button';
        b.addEventListener('click', async function () { b.disabled = true; clearTimeout(toastTimer); await undo(); });
        bar.appendChild(b);
      }
      wrap.appendChild(bar);
      toastTimer = setTimeout(function () { bar.remove(); }, isError ? 8000 : 20000);
    }

    function questRows() {
      const list = api.el('ul', null, 'arc-list');
      data.open_quests.forEach(function (q) {
        const li = api.el('li', null, 'arc-row');
        const box = api.el('input'); box.type = 'checkbox'; box.className = 'arc-check';
        box.setAttribute('aria-label', 'Mark done: ' + q.name);
        box.disabled = !canEdit || !q.id;
        box.addEventListener('change', async function () {
          box.disabled = true;
          const ok = await save('done', [q.id], li);
          if (ok) notice('Marked done: ' + q.name, false, function () { return save('undone', [q.id]).then(function (done) { if (done) notice('Undone: ' + q.name, false); }); });
          else box.checked = false;
        });
        const title = api.el('div', null, 'arc-row-title');
        title.append(q.name, api.el('small', (q.maps || []).join(', ') || 'Any map'));
        li.append(box, title);
        list.appendChild(li);
      });
      return list;
    }

    function doneList() {
      const box = api.el('div', null, 'arc-done');
      const count = (data.done_quests || []).length;
      const toggle = api.el('button', 'Done quests (' + count + ')', 'ih-button'); toggle.type = 'button';
      toggle.setAttribute('aria-expanded', String(doneOpen));
      box.appendChild(toggle);
      const list = api.el('ul', null, 'arc-list'); list.hidden = !doneOpen;
      (data.done_quests || []).forEach(function (q) {
        const li = api.el('li', null, 'arc-row');
        const cb = api.el('input'); cb.type = 'checkbox'; cb.className = 'arc-check'; cb.checked = true; cb.disabled = !canEdit;
        cb.setAttribute('aria-label', 'Untick to mark not done: ' + q.name);
        cb.addEventListener('change', async function () {
          cb.disabled = true;
          const ok = await save('undone', [q.id], li);
          if (ok) notice('Marked not done: ' + q.name, false); else cb.checked = true;
        });
        li.append(cb, api.el('div', q.name, 'arc-row-title'));
        list.appendChild(li);
      });
      if (!count) list.appendChild(api.el('li', 'No done quests yet.', 'ih-muted'));
      toggle.addEventListener('click', function () { doneOpen = !doneOpen; list.hidden = !doneOpen; toggle.setAttribute('aria-expanded', String(doneOpen)); });
      box.appendChild(list);
      return box;
    }

    function stepper(label, value, min, max, unit, onSet) {
      const box = api.el('div', null, 'arc-stepper');
      const minus = api.el('button', '−', 'ih-button'); minus.type = 'button'; minus.setAttribute('aria-label', 'Lower ' + label);
      const plus = api.el('button', '+', 'ih-button'); plus.type = 'button'; plus.setAttribute('aria-label', 'Raise ' + label);
      const out = api.el('output', unit + ' ' + value + '/' + max);
      minus.disabled = !canEdit || value <= min; plus.disabled = !canEdit || value >= max;
      minus.addEventListener('click', function () { minus.disabled = plus.disabled = true; onSet(value - 1); });
      plus.addEventListener('click', function () { minus.disabled = plus.disabled = true; onSet(value + 1); });
      box.append(minus, out, plus);
      return box;
    }

    function stepRows(rows, label, unit, cmd, key, maxKey) {
      const list = api.el('ul', null, 'arc-list');
      rows.forEach(function (r) {
        const li = api.el('li', null, 'arc-row');
        li.style.gridTemplateColumns = 'minmax(0,1fr) auto';
        li.appendChild(api.el('div', r.name, 'arc-row-title'));
        li.appendChild(stepper(r.name, r[key], 0, r[maxKey], unit, function (n) { save(cmd, [r.id, String(n)], li); }));
        list.appendChild(li);
      });
      return list;
    }

    function haveInput(item) {
      const input = api.el('input', null, 'arc-have'); input.type = 'number'; input.min = '0'; input.step = '1'; input.inputMode = 'numeric';
      input.value = item.have; input.disabled = !canEdit || !item.id;
      input.setAttribute('aria-label', 'I have, ' + item.item);
      input.addEventListener('change', function () {
        const n = Number(input.value);
        if (!Number.isInteger(n) || n < 0) { input.value = item.have; notice('Enter a whole number, 0 or more.', true); return; }
        if (n === item.have) return;
        input.disabled = true;
        save('have', [item.id, String(n)], input.closest('.arc-row')).then(function (ok) { if (!ok) { input.disabled = false; input.value = item.have; } });
      });
      return input;
    }

    function needRows(rows) {
      const list = api.el('ul', null, 'arc-list');
      rows.forEach(function (n) {
        const li = api.el('li', null, 'arc-row');
        li.style.gridTemplateColumns = 'minmax(0,1fr) auto';
        const title = api.el('div', null, 'arc-row-title');
        title.append(n.item, api.el('small', n.status + ' · need ' + n.now + ' now, ' + n.later + ' later · ' + (n.for || '')));
        li.append(title, haveInput(n));
        list.appendChild(li);
      });
      return list;
    }

    function paint() {
      wrap.replaceChildren();
      if (data.error) wrap.appendChild(api.el('p', data.error, 'notice notice-warning'));
      if (data.quests_completed != null) wrap.appendChild(api.section('Quest progress', api.el('p', data.quests_completed + ' quests completed', 'ih-muted')));
      if (data.open_quests) wrap.appendChild(api.section('Open quests (' + data.open_quests.length + ')', (function () { const c = api.el('div'); c.append(data.open_quests.length ? questRows() : api.el('p', 'No open quests.', 'ih-muted'), doneList()); return c; }())));
      if (data.hideout && data.hideout.length) wrap.appendChild(api.section('Hideout benches', stepRows(data.hideout, 'bench', 'Level', 'hideout_set', 'level', 'max_level')));
      const projects = (data.projects || []).filter(function (p) { return p.active; });
      if (projects.length) wrap.appendChild(api.section('Active projects', stepRows(projects, 'project', 'Phase', 'project_set', 'done_phases', 'total_phases')));
      if (data.needs) wrap.appendChild(api.section('Needs and materials (' + data.needs.length + ')', data.needs.length ? needRows(data.needs) : api.el('p', 'Nothing needed.', 'ih-muted')));
      if (data.maps) wrap.appendChild(api.section('Quest maps (' + data.maps.length + ')', api.dataTable([
        { label: 'Map', value: function (item) { return item.map; } },
        { label: 'Quests', value: function (item) { return item.count; } },
        { label: 'Quest names', value: function (item) { return (item.quests || []).join(', '); } }
      ], data.maps)));
      if (data.inventory) wrap.appendChild(api.section('Inventory advice (' + data.inventory.length + ')', api.dataTable([
        { label: 'Item', value: function (item) { return item.item; } },
        { label: 'Have', value: function (item) { return item.have; } },
        { label: 'Surplus', value: function (item) { return item.surplus; } },
        { label: 'Advice', value: function (item) { return item.action; } },
        { label: 'Value', value: function (item) { return item.gain; } }
      ], data.inventory)));
      if (!wrap.childElementCount) wrap.appendChild(api.el('p', 'No Arc Raiders details are available right now.', 'ih-muted'));
    }

    paint();
    return wrap;
  }

  h.arc_raiders = { render: render };
}());
