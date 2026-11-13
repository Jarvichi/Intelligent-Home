(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let names = [];
  const url = (p) => new URL(p, location.href);
  function draw() {
    const min = Math.max(1, +$('#min').value || 1), only = $('#only').checked, q = $('#find').value.trim().toLowerCase();
    const rows = names.filter((n) => n.notes >= min && (!only || n.scrub) && (!q || n.name.toLowerCase().includes(q)));
    $('#list').innerHTML = rows.length ? rows.map((n) => `<div class="nm-row" data-n="${esc(n.name)}">
      <input type="checkbox" aria-label="Scrub ${esc(n.name)}" ${n.scrub ? 'checked' : ''}>
      <div><strong>${esc(n.name)}</strong> <small>${n.notes} notes · ${n.count} times</small><small>…${esc(n.sample)}…</small></div>
      <input type="text" value="${esc(n.repl)}" aria-label="Replace ${esc(n.name)} with" maxlength="60"></div>`).join('') : '<ih-empty>No words to show.</ih-empty>';
  }
  $('#list').addEventListener('input', (e) => {
    const row = e.target.closest('.nm-row'); if (!row) return;
    const n = names.find((x) => x.name === row.dataset.n); if (!n) return;
    if (e.target.type === 'checkbox') n.scrub = e.target.checked; else n.repl = e.target.value;
    $('#msg').textContent = '';
  });
  ['min', 'only', 'find'].forEach((id) => $('#' + id).addEventListener('input', draw));
  $('#save').addEventListener('click', async () => {
    const body = {names: {}, relaxed_too: $('#relaxed').checked};
    names.filter((n) => n.scrub || n.repl !== '[name]').forEach((n) => { body.names[n.name] = {scrub: n.scrub, repl: n.repl}; });
    $('#msg').textContent = 'Saving…';
    try {
      const r = await fetch(url('api/names'), {method: 'POST', headers: {'Content-Type': 'application/json'}, credentials: 'same-origin', body: JSON.stringify(body)});
      const d = await r.json(); if (!r.ok) throw new Error(d.error || r.status);
      $('#msg').textContent = `Saved. ${d.scrubbed.length} word${d.scrubbed.length === 1 ? '' : 's'} will be scrubbed${d.relaxed_too ? ', including in relaxed notes' : ''}. Takes effect at the next sync.`;
    } catch (e) { $('#msg').textContent = 'Not saved: ' + e.message; }
  });
  fetch(url('api/names'), {credentials: 'same-origin'}).then((r) => r.json()).then((d) => {
    names = d.names; $('#relaxed').checked = !!d.relaxed_too; $('#rn').textContent = d.relaxed_notes.length;
    $('#rn').title = d.relaxed_notes.join('\n'); draw();
  }).catch(() => { $('#list').innerHTML = '<ih-empty>Could not load the names.</ih-empty>'; });
})();
