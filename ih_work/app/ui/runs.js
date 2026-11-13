/* Runs card: scheduled night/day runs and Run now. Talks to api/runs (same origin, ingress). Every value is rendered with textContent / .value. */
(() => {
  const base = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
  const $ = (id) => document.getElementById(id);
  const card = $('runs-card');
  if (!card) return;
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
  const NAMES = { night: 'Night run', day: 'Day run' };
  const forms = {};
  let editing = false;

  function clockText(iso) {
    const t = new Date(iso);
    return isNaN(t) ? '' : t.toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });
  }

  async function call(method, path, body) {
    const opt = { method, cache: 'no-store' };
    if (body !== undefined) { opt.headers = { 'Content-Type': 'application/json' }; opt.body = JSON.stringify(body); }
    const r = await fetch(base + 'api/runs' + path, opt);
    let j = null;
    try { j = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error(j && typeof j.error === 'string' ? j.error : 'Request failed (' + r.status + ')');
    return j;
  }

  function maxValue(unl, num) { return unl.checked ? null : parseInt(num.value, 10); }

  function field(label, input) { const l = el('label', '', label); l.appendChild(input); return l; }

  function buildForm(kind) {
    const f = { box: el('div', 'runs-set') };
    f.box.appendChild(el('h3', 'runs-h', NAMES[kind]));
    f.on = Object.assign(document.createElement('input'), { type: 'checkbox' });
    const onLabel = el('label', 'runs-check'); onLabel.appendChild(f.on); onLabel.appendChild(document.createTextNode(' Turned on'));
    f.start = Object.assign(document.createElement('input'), { type: 'time' });
    f.stop = Object.assign(document.createElement('input'), { type: 'time' });
    f.pace = Object.assign(document.createElement('input'), { type: 'number', min: '-10', max: '10', step: '0.5' });
    f.max = Object.assign(document.createElement('input'), { type: 'number', min: '1', max: '1000', step: '1' });
    f.unl = Object.assign(document.createElement('input'), { type: 'checkbox' });
    const unlLabel = el('label', 'runs-check'); unlLabel.appendChild(f.unl); unlLabel.appendChild(document.createTextNode(' No limit (keep going through the to-do list until the pace target or stop time)'));
    const grid = el('div', 'runs-grid');
    for (const n of [onLabel, field('Start', f.start), field('Stop by', f.stop), field('Pace target (-10 to 10)', f.pace), field('Max to-dos per run', f.max), unlLabel]) grid.appendChild(n);
    f.box.appendChild(grid);
    const actions = el('div', 'runs-actions');
    f.save = el('button', 'ih-button', 'Save ' + NAMES[kind].toLowerCase());
    f.save.type = 'button';
    actions.appendChild(f.save);
    f.box.appendChild(actions);
    for (const i of [f.on, f.start, f.stop, f.pace, f.max, f.unl]) i.addEventListener('input', () => { editing = true; });
    f.unl.addEventListener('change', () => { f.max.disabled = f.unl.checked; });
    f.save.addEventListener('click', async () => {
      const patch = {};
      patch[kind] = { enabled: f.on.checked, start: f.start.value, stop_by: f.stop.value, pace_target: parseFloat(f.pace.value), max_items: maxValue(f.unl, f.max) };
      if (isNaN(patch[kind].pace_target) || patch[kind].pace_target < -10 || patch[kind].pace_target > 10) { say('Pace target must be from -10 to 10.'); return; }
      f.save.disabled = true;
      try { render(await call('POST', '/settings', patch), true); say(NAMES[kind] + ' saved.'); } catch (e) { say(e.message); }
      f.save.disabled = false;
    });
    forms[kind] = f;
    $('run-forms').appendChild(f.box);
  }

  function fill(kind, s) {
    const f = forms[kind];
    f.on.checked = !!s.enabled; f.start.value = s.start; f.stop.value = s.stop_by; f.pace.value = s.pace_target;
    f.unl.checked = s.max_items === null; f.max.disabled = f.unl.checked; f.max.value = s.max_items === null ? 3 : s.max_items;
  }

  function say(t) { $('run-msg').textContent = t || ''; }

  function render(v, force) {
    if (!v || typeof v !== 'object') return;
    card.hidden = false;
    if (!forms.night) { buildForm('night'); buildForm('day'); }
    if (v.settings && (force || !editing)) { fill('night', v.settings.night); fill('day', v.settings.day); if (force) editing = false; }
    const a = v.active, st = $('run-status');
    if (a) {
      const items = a.max_items === null ? 'no item limit' : a.started + ' of ' + a.max_items + ' items';
      st.textContent = (a.dry ? '[test mode] ' : '') + (a.kind === 'now' ? 'Manual run' : NAMES[a.kind]) + ' is ' + a.state + '. ' + items + ', pace target ' + a.pace_target +
        (a.stop_by_at ? ', stop by ' + clockText(a.stop_by_at) : '') + '. ' + (a.reason || '');
    } else if (v.last) {
      st.textContent = 'No run active. Last run ended ' + clockText(v.last.ended_at) + ': ' + v.last.reason + ' (' + v.last.started + ' items).';
    } else st.textContent = 'No run active.';
    $('now-go').disabled = !!a;
    $('now-stop').disabled = !a;
  }

  async function load() {
    try { render(await call('GET', '')); } catch (e) { card.hidden = true; }
  }

  $('now-unl').addEventListener('change', () => { $('now-max').disabled = $('now-unl').checked; });
  $('now-go').addEventListener('click', async () => {
    const pace = parseFloat($('now-pace').value);
    if (isNaN(pace) || pace < -10 || pace > 10) { say('Pace target must be from -10 to 10.'); return; }
    $('now-go').disabled = true;
    try { render(await call('POST', '/start', { pace_target: pace, max_items: maxValue($('now-unl'), $('now-max')) })); say(''); } catch (e) { say(e.message); load(); }
  });
  $('now-stop').addEventListener('click', async () => {
    try { render(await call('POST', '/stop', {})); say(''); } catch (e) { say(e.message); load(); }
  });
  load();
  setInterval(load, 10000);
})();
