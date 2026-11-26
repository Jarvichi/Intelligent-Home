const base = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
let rows = [];
const $ = s => document.querySelector(s);
const fmt = v => v === null || v === undefined ? '–' : v;
function draw() {
  const q = $('#q').value.trim().toLowerCase();
  const shown = rows.filter(r => !q || [r.at, r.charging, r.source].join(' ').toLowerCase().includes(q));
  const tb = $('#rows'); tb.textContent = '';
  for (const r of shown) {
    const tr = document.createElement('tr');
    const cells = [r.at.replace('T', ' ').replace('Z', ''), r.battery_pct === null ? '–' : r.battery_pct + ' %', r.range_km === null ? '–' : Math.round(r.range_km / 1.609344) + ' mi', r.charging, r.odometer_km === null ? '–' : Math.round(r.odometer_km / 1.609344) + ' mi', r.source];
    cells.forEach((c, i) => { const td = document.createElement('td'); td.textContent = c; if (i > 3) td.className = 'ps-hide'; tr.appendChild(td); });
    tb.appendChild(tr);
  }
  $('#empty').hidden = shown.length > 0;
}
async function refresh() {
  try {
    const [st, rd] = await Promise.all([fetch(base + 'api/status', {cache: 'no-store'}).then(r => r.json()), fetch(base + 'api/readings', {cache: 'no-store'}).then(r => r.json())]);
    rows = rd.readings || [];
    const l = rd.latest;
    $('#headline').textContent = l && l.battery_pct !== null ? l.battery_pct + ' % · ' + l.charging : 'Waiting for car';
    $('#detail').textContent = st.error ? st.error : 'Last reading ' + (st.at || '–');
    $('#mode').textContent = 'Mode: ' + st.mode + ' · source: ' + st.source + (st.mode === 'fixture' ? ' (FAKE values)' : '');
    draw();
  } catch (e) { $('#headline').textContent = 'Status unavailable'; $('#detail').textContent = 'Could not reach the add-on'; }
}
$('#check').addEventListener('click', refresh);
$('#q').addEventListener('input', draw);
$('#clear').addEventListener('click', () => { $('#q').value = ''; draw(); });
refresh(); setInterval(refresh, 30000);
