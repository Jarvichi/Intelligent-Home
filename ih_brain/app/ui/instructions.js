/* Instructions editor page. All text goes into the page with textContent or a textarea value, never as HTML. */
(() => {
  const $ = (s) => document.querySelector(s);
  const HEADER = 'X-IH-Instructions';
  const text = $('#text'), notice = $('#notice'), fileSel = $('#file');
  const btnReload = $('#reload'), btnPreview = $('#preview'), btnSave = $('#save');
  let state = null, fileId = '', revision = '', loaded = '', editable = false, previewToken = '', previewText = '';
  const h = (tag, cls, t) => { const e = document.createElement(tag); if (cls) e.className = cls; if (t !== undefined) e.textContent = t; return e; };
  const bytes = (s) => new TextEncoder().encode(s).length;
  const dirty = () => text.value !== loaded;
  function say(msg, tone) { notice.textContent = msg; notice.dataset.tone = tone || ''; notice.hidden = !msg; }
  async function api(path, body) {
    const opt = {headers: {'Accept': 'application/json'}, credentials: 'same-origin', cache: 'no-store'};
    if (body !== undefined) {
      opt.method = 'POST'; opt.body = JSON.stringify(body);
      opt.headers['Content-Type'] = 'application/json'; opt.headers[HEADER] = '1';
    }
    const res = await fetch(new URL('api/instructions/' + path, location.href), opt);
    let data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (!res.ok) { const err = new Error(data.error || ('Request failed (' + res.status + ').')); err.status = res.status; throw err; }
    return data;
  }
  function clearPreview() {
    previewToken = ''; previewText = ''; btnSave.disabled = true;
    $('#diff-box').hidden = true; $('#diff').textContent = '';
  }
  function showSize() {
    const n = bytes(text.value), max = state ? state.max_bytes : 0;
    $('#size').textContent = n + ' bytes' + (max ? ' of ' + max + ' allowed' : '') + (dirty() ? ' · not saved' : '');
  }
  function setMode() {
    const pill = $('#mode'), label = {off: 'Switched off', view: 'Read-only', edit: 'Editing allowed'}[state.mode] || state.mode;
    $('#mode-text').textContent = label; pill.dataset.ok = String(state.mode !== 'off');
  }
  function rows(box, items, empty, make) {
    box.textContent = '';
    if (!items.length) { box.append(h('ih-empty', '', empty)); return; }
    for (const it of items) box.append(make(it));
  }
  async function loadSide() {
    try {
      const b = await api('backups?id=' + encodeURIComponent(fileId));
      rows($('#backups'), b.backups, 'No backups yet.', (it) => {
        const r = h('div', 'row'); r.append(h('span', '', it.time.replace('T', ' ').replace('Z', ' UTC') + ' · ' + it.size + ' bytes'));
        if (editable) {
          const b2 = h('button', 'btn', 'Load'); b2.type = 'button';
          b2.addEventListener('click', () => loadBackup(it.name)); r.append(b2);
        }
        return r;
      });
      const a = await api('audit');
      rows($('#audit'), a.entries, 'No saves yet.', (it) => {
        const r = h('div', 'row');
        r.append(h('span', '', it.time.replace('T', ' ').replace('Z', ' UTC') + ' · ' + it.file + ' · ' + it.bytes_before + ' → ' + it.bytes_after + ' bytes'));
        return r;
      });
    } catch (e) { /* the editor itself still works without the side lists */ }
  }
  async function loadBackup(name) {
    if (dirty() && !window.confirm('Replace your unsaved edits with this backup?')) return;
    try {
      const d = await api('backup?id=' + encodeURIComponent(fileId) + '&name=' + encodeURIComponent(name));
      text.value = d.content; clearPreview(); showSize();
      say('Backup loaded into the editor. It is not saved yet: preview the changes, then save.', 'warn');
    } catch (e) { say(e.message, 'bad'); }
  }
  async function loadFile(force) {
    if (!force && dirty() && !window.confirm('Throw away your unsaved edits and reload the saved text?')) return;
    clearPreview();
    const info = state.files.find((f) => f.id === fileId);
    $('#file-path').textContent = info ? info.path : '';
    try {
      const d = await api('file?id=' + encodeURIComponent(fileId));
      revision = d.revision; loaded = d.content; editable = !!d.editable; text.value = d.content;
      text.readOnly = !editable; btnPreview.hidden = !editable; btnSave.hidden = !editable;
      say(editable ? '' : 'Read-only: this file cannot be changed here.', editable ? '' : 'warn');
    } catch (e) {
      revision = ''; loaded = ''; editable = false; text.value = ''; text.readOnly = true; btnPreview.hidden = true; btnSave.hidden = true;
      say(e.status === 503 ? 'This file cannot be read. The add-on may not have the Home Assistant config folder mapped.' : e.message, 'bad');
    }
    showSize(); loadSide();
  }
  async function preview() {
    clearPreview();
    try {
      const d = await api('preview', {id: fileId, content: text.value, revision});
      const box = $('#diff'); box.textContent = '';
      for (const line of d.diff) {
        const cls = line.startsWith('@@') ? 'hunk' : line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : 'ctx';
        box.append(h('span', cls, line + '\n'));
      }
      $('#diff-summary').textContent = d.added + ' line(s) added, ' + d.removed + ' removed · ' + d.bytes_before + ' → ' + d.bytes_after + ' bytes'
        + (d.truncated ? ' · the list is cut short' : '');
      $('#diff-box').hidden = false;
      if (d.stale) return say('The saved file changed after you opened it. Your edits are still here. Copy them, reload, then make the edits again.', 'bad');
      if (!d.changed) return say('Nothing to save: the text is the same as the saved file.', 'warn');
      if (!d.diff.length) box.append(h('span', 'ctx', 'Only line endings or the last line break differ.\n'));
      previewToken = d.preview; previewText = text.value; btnSave.disabled = false;
      say('Check the changes below, then press Save.', 'warn');
    } catch (e) { say(e.message, 'bad'); }
  }
  async function save() {
    if (!previewToken || text.value !== previewText) { clearPreview(); return say('The text changed after the preview. Preview again.', 'warn'); }
    btnSave.disabled = true;
    try {
      const d = await api('save', {id: fileId, content: previewText, revision, preview: previewToken});
      revision = d.revision; loaded = previewText; clearPreview(); showSize(); loadSide();
      say(d.changed ? 'Saved. The previous text was kept as a backup.' : 'Nothing to save.', 'ok');
    } catch (e) {
      clearPreview();
      say(e.status === 409 ? 'Not saved: the file changed on disk. Your edits are still here. Copy them, reload, then make the edits again.'
        : 'Not saved: ' + e.message + ' Your edits are still here.', 'bad');
    }
  }
  async function start() {
    try { state = await api('state'); } catch (e) { $('#mode-text').textContent = 'Unavailable'; $('#mode').dataset.ok = 'false'; return say(e.message, 'bad'); }
    setMode();
    if (state.mode === 'off' || !state.files.length) return say('The instructions editor is switched off in the add-on configuration (instructions_mode).', 'warn');
    for (const f of state.files) { const o = h('option', '', f.label + (f.available ? '' : ' (not available)')); o.value = f.id; fileSel.append(o); }
    $('#file-row').hidden = state.files.length < 2;
    fileId = state.files[0].id;
    loadFile(true);
  }
  text.addEventListener('input', () => { if (previewToken || !$('#diff-box').hidden) clearPreview(); showSize(); });
  fileSel.addEventListener('change', () => {
    if (dirty() && !window.confirm('Throw away your unsaved edits and open the other file?')) { fileSel.value = fileId; return; }
    fileId = fileSel.value; loadFile(true);
  });
  btnReload.addEventListener('click', () => loadFile(false));
  btnPreview.addEventListener('click', preview);
  btnSave.addEventListener('click', save);
  window.addEventListener('beforeunload', (ev) => { if (dirty()) { ev.preventDefault(); ev.returnValue = ''; } });
  start();
})();
