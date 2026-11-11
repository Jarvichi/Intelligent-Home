(function () {
  'use strict';
  const h = window.IHGameComponents = window.IHGameComponents || {};
  const TOAST_MS = 5000;
  const TOAST_ERROR_MS = 8000;

  function render(data, api) {
    const wrap = api.el('div', null, 'details-content cyberpunk-details');
    const canEdit = typeof api.command === 'function' && typeof api.reload === 'function';
    const doneOpen = {};
    let toastTimer = null;
    let filter = '';

    function plainError(error) {
      const text = error && error.message ? error.message : '';
      return text ? 'Could not save: ' + text : 'Could not save. Check the connection and try again.';
    }

    // Send one change, re-fetch details, repaint. Returns true when saved.
    async function save(cmd, args, busy) {
      if (busy) busy.setAttribute('aria-busy', 'true');
      try {
        const result = await api.command(cmd, args);
        if (!/^Marked/.test(result.text || '')) throw new Error(result.text || 'The change was refused.');
        data = await api.reload();
        paint();
        return true;
      } catch (error) {
        if (busy) busy.removeAttribute('aria-busy');
        notice(plainError(error), true);
        return false;
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
        const ok = await save(q.done ? 'undone' : 'done', [q.id], li);
        if (ok) notice((q.done ? 'Marked not done: ' : 'Marked done: ') + q.name, false);
        else { box.checked = !!q.done; box.disabled = false; }
      });
      const title = api.el('div', null, 'arc-row-title');
      title.append(q.name, api.el('small', q.type_label || ''));
      li.append(box, title);
      return li;
    }

    function listOf(rows, emptyText) {
      const list = api.el('ul', null, 'arc-list');
      rows.forEach(function (q) { list.appendChild(questRow(q)); });
      if (!rows.length) list.appendChild(api.el('li', emptyText, 'ih-muted'));
      return list;
    }

    function groupBody(group) {
      const body = api.el('div');
      const open = (group.quests || []).filter(function (q) { return !q.done; });
      const done = (group.quests || []).filter(function (q) { return q.done; });
      body.appendChild(listOf(open, 'All quests in this group are ticked.'));
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

    function paint() {
      wrap.replaceChildren();
      if (data.error) wrap.appendChild(api.el('p', data.error, 'notice notice-warning'));
      if (data.total != null) {
        const top = api.el('div');
        top.append(api.el('p', data.done + ' / ' + data.total + ' quests done', 'ih-muted'), searchBox());
        wrap.appendChild(api.section('Quest progress', top));
      }
      (data.groups || []).forEach(function (group) {
        wrap.appendChild(api.section(group.label + ' (' + group.done + ' / ' + group.total + ')', groupBody(group)));
      });
      if (!wrap.childElementCount) wrap.appendChild(api.el('p', 'No Cyberpunk 2077 details are available right now.', 'ih-muted'));
      applyFilter();
    }

    paint();
    return wrap;
  }

  h.cyberpunk = { render: render };
}());
