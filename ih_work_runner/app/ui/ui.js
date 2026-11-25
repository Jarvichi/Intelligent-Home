/* Work Runner status page. Read-only. Every value is rendered with textContent. */
(() => {
  const base = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
  const dur = (s) => { if (typeof s !== 'number') return '–'; const m = Math.floor(s / 60); return m >= 60 ? Math.floor(m / 60) + ' h ' + (m % 60) + ' min' : m ? m + ' min ' + (s % 60) + ' s' : s + ' s'; };
  const row = (box, k, v, bad) => { const r = el('div', 'wr-row'); r.appendChild(el('span', '', k)); r.appendChild(el('span', bad ? 'wr-bad' : '', v)); box.appendChild(r); };

  function render(j) {
    const pill = $('service-status');
    const bad = j.problems && j.problems.length;
    pill.setAttribute('status', j.paused ? 'warn' : bad ? 'error' : 'ok');
    pill.textContent = j.paused ? 'Paused' : (j.mode === 'live' ? 'Live' : 'Dry run');
    const now = $('now-rows'); now.replaceChildren();
    for (const v of j.jobs || []) {
      row(now, 'Task', (v.ref ? v.ref + ' · ' : '') + (v.title || '(no title)'));
      row(now, 'Stage', v.stage + (v.stalled ? ' (no update for a while)' : ''), v.stalled);
      row(now, 'Running for', dur(v.elapsed_s));
    }
    $('now-empty').hidden = !!(j.jobs || []).length || !j.work_reachable;
    $('work-note').hidden = !!j.work_reachable;
    const st = $('state-rows'); st.replaceChildren();
    row(st, 'Mode', j.mode === 'live' ? 'live' : 'dry (only looks, claims nothing)');
    row(st, 'LIVE file', j.live_file ? 'present' : 'missing');
    row(st, 'Paused', j.paused || 'no', !!j.paused);
    row(st, 'Last pass', (j.last_pass || 'none yet') + (j.last_result ? ' · ' + j.last_result : ''));
    row(st, 'Pace gate', j.gate ? j.gate.text + ' (' + j.gate.at + ')' : 'not reported');
    if (j.pace !== null && j.pace !== undefined) row(st, 'Pace', String(j.pace));
    if (j.run) row(st, 'Work run', [j.run.kind, j.run.state, j.run.text].filter(Boolean).join(' · '));
    row(st, 'Worker lock', j.lock && j.lock.present ? 'file last used ' + dur(j.lock.age_s) + ' ago' : 'no lock file');
    const pr = $('problems'); pr.replaceChildren();
    for (const p of j.problems || []) pr.appendChild(el('li', 'wr-bad', p));
    const tb = $('runs'); tb.replaceChildren();
    for (const r of j.runs || []) {
      const tr = el('tr');
      tr.appendChild(el('td', '', r.task ? '#' + r.task + (r.title ? ' ' + r.title : '') : '–'));
      tr.appendChild(el('td', '', r.mode || '–'));
      tr.appendChild(el('td', r.result === 'failed' ? 'wr-bad' : '', r.result + (r.error ? ' (' + r.error + ')' : '')));
      tr.appendChild(el('td', '', dur(r.seconds)));
      tr.appendChild(el('td', '', r.at));
      tb.appendChild(tr);
    }
    $('runs-empty').hidden = !!(j.runs || []).length;
    $('checked').textContent = 'Checked ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  async function refresh() {
    try {
      const r = await fetch(base + 'api/status', { cache: 'no-store' });
      if (!r.ok) throw new Error('status ' + r.status);
      render(await r.json());
    } catch (e) {
      const pill = $('service-status'); pill.setAttribute('status', 'error'); pill.textContent = 'Not reachable';
    }
  }
  refresh();
  setInterval(refresh, 15000);
})();
