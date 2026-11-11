(function () {
  'use strict';
  const h = window.IHGameComponents = window.IHGameComponents || {};
  const TOAST_MS = 5000;
  const TOAST_ERROR_MS = 8000;
  const AVAILABLE = 'available';
  const LOCKED = 'locked';
  const MISSED = 'missed';
  // The three lists of quests that are not ticked. 'count' names the total the add-on sends for that list.
  const VIEWS = [
    { id: AVAILABLE, label: 'Available', empty: 'No quests are available in this group right now.' },
    { id: LOCKED, label: 'Locked', empty: 'No locked quests in this group.' },
    { id: MISSED, label: 'Missed this run', empty: 'No quests missed in this group.' }
  ];

  function render(data, api) {
    const wrap = api.el('div', null, 'details-content cyberpunk-details');
    const canEdit = typeof api.command === 'function' && typeof api.reload === 'function';
    const doneOpen = {};
    let toastTimer = null;
    let filter = '';
    let view = AVAILABLE;

    function plainError(error) {
      const text = error && error.message ? error.message : '';
      return text ? 'Could not save: ' + text : 'Could not save. Check the connection and try again.';
    }

    // Send one change, re-fetch details, repaint. Returns the add-on's answer when saved, else ''.
    // The answer also names the quests that the change ruled out or made possible again.
    async function save(cmd, args, busy) {
      if (busy) busy.setAttribute('aria-busy', 'true');
      try {
        const result = await api.command(cmd, args);
        if (!/^Marked/.test(result.text || '')) throw new Error(result.text || 'The change was refused.');
        data = await api.reload();
        paint();
        return result.text;
      } catch (error) {
        if (busy) busy.removeAttribute('aria-busy');
        notice(plainError(error), true);
        return '';
      }
    }

    function notice(message, isError) {
      const old = wrap.querySelector('.arc-toast');
      if (old) old.remove();
      clearTimeout(toastTimer);
      const bar = api.el('div', null, 'arc-toast' + (isError ? ' notice-error' : ''));
      bar.setAttribute('role', 'status');
      bar.appendChild(api.el('span', message));
      wrap.appendChild(bar);
      toastTimer = setTimeout(function () { bar.remove(); }, isError ? TOAST_ERROR_MS : TOAST_MS);
    }

    function questRow(q) {
      const li = api.el('li', null, 'arc-row');
      li.dataset.name = (q.name || '').toLowerCase();
      const box = api.el('input'); box.type = 'checkbox'; box.className = 'arc-check'; box.checked = !!q.done;
      box.setAttribute('aria-label', (q.done ? 'Untick to mark not done: ' : 'Mark done: ') + q.name);
      box.disabled = !canEdit || !q.id;
      box.addEventListener('change', async function () {
        box.disabled = true;
        const answer = await save(q.done ? 'undone' : 'done', [q.id], li);
        if (answer) notice(answer, false);
        else { box.checked = !!q.done; box.disabled = false; }
      });
      const title = api.el('div', null, 'arc-row-title');
      title.append(q.name, api.el('small', q.type_label || ''));
      rowNotes(q).forEach(function (text) { title.appendChild(api.el('small', text)); });
      li.append(box, title);
      return li;
    }

    // Extra lines under a quest name: why it is locked or missed, what ticking it rules out, and the wiki's hint.
    function rowNotes(q) {
      const notes = [];
      if (q.done) return notes;
      if (q.reason) notes.push(q.reason);
      if ((q.blocks || []).length) notes.push('Ticking this rules out: ' + q.blocks.join(', ') + '.');
      if (q.hint && (q.reason || '').indexOf(q.hint) === -1) notes.push(q.hint);
      return notes;
    }

    function listOf(rows, emptyText) {
      const list = api.el('ul', null, 'arc-list');
      rows.forEach(function (q) { list.appendChild(questRow(q)); });
      if (!rows.length) list.appendChild(api.el('li', emptyText, 'ih-muted'));
      return list;
    }

    function groupBody(group) {
      const body = api.el('div');
      const current = VIEWS.filter(function (v) { return v.id === view; })[0];
      const open = (group.quests || []).filter(function (q) { return !q.done && q.state === view; });
      const done = (group.quests || []).filter(function (q) { return q.done; });
      body.appendChild(listOf(open, current.empty));
      const box = api.el('div', null, 'arc-done');
      const toggle = api.el('button', 'Done quests (' + done.length + ')', 'ih-button'); toggle.type = 'button';
      const list = listOf(done, 'No done quests yet.');
      list.hidden = !doneOpen[group.id];
      toggle.setAttribute('aria-expanded', String(!!doneOpen[group.id]));
      toggle.addEventListener('click', function () {
        doneOpen[group.id] = !doneOpen[group.id];
        list.hidden = !doneOpen[group.id];
        toggle.setAttribute('aria-expanded', String(!!doneOpen[group.id]));
      });
      box.append(toggle, list);
      body.appendChild(box);
      return body;
    }

    function applyFilter() {
      const text = filter.trim().toLowerCase();
      wrap.querySelectorAll('.arc-row').forEach(function (li) {
        li.hidden = !!text && (li.dataset.name || '').indexOf(text) === -1;
      });
    }

    function searchBox() {
      const input = api.el('input', null, 'cyberpunk-filter'); input.type = 'search'; input.value = filter;
      input.placeholder = 'Find a quest by name';
      input.setAttribute('aria-label', 'Find a quest by name');
      input.addEventListener('input', function () { filter = input.value; applyFilter(); });
      return input;
    }

    // One button per list. The pressed button is the list on screen.
    function viewButtons() {
      const bar = api.el('div', null, 'cyberpunk-views');
      bar.setAttribute('role', 'group');
      bar.setAttribute('aria-label', 'Choose which quests to list');
      VIEWS.forEach(function (v) {
        const button = api.el('button', v.label + ' (' + (data[v.id] || 0) + ')', 'ih-button'); button.type = 'button';
        button.setAttribute('aria-pressed', String(v.id === view));
        button.addEventListener('click', function () { view = v.id; paint(); });
        bar.appendChild(button);
      });
      return bar;
    }

    function viewHelp() {
      if (view === LOCKED) return 'Locked quests need an earlier quest. Tick the earlier quest to make them available.';
      if (view === MISSED) return 'Missed quests cannot be done in this playthrough. Each one names the quest and the choice that ruled it out. Untick that quest to bring them back.';
      return 'Quests you can do now. A quest with a prerequisite appears here when you tick the prerequisite.';
    }

    function paint() {
      wrap.replaceChildren();
      if (data.error) wrap.appendChild(api.el('p', data.error, 'notice notice-warning'));
      if (data.total != null) {
        const top = api.el('div');
        const missed = data.missed ? ' · ' + data.missed + ' missed this run' : '';
        top.append(api.el('p', data.done + ' / ' + data.total + ' quests done' + missed, 'ih-muted'), viewButtons(),
          api.el('p', viewHelp(), 'ih-muted'), searchBox());
        wrap.appendChild(api.section('Quest progress', top));
      }
      (data.groups || []).forEach(function (group) {
        const missed = group.missed ? ', ' + group.missed + ' missed' : '';
        wrap.appendChild(api.section(group.label + ' (' + group.done + ' / ' + group.total + missed + ')', groupBody(group)));
      });
      if (!wrap.childElementCount) wrap.appendChild(api.el('p', 'No Cyberpunk 2077 details are available right now.', 'ih-muted'));
      applyFilter();
    }

    paint();
    return wrap;
  }

  h.cyberpunk = { render: render };
}());
