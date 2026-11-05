// Run by test_jobs_ui.py with node. Prints one JSON object of results.
const L = require('../app/ui/jobs-logic.js');
// Fixed local "now": 5 Nov 2026 09:30 (local time), built from parts so it is timezone independent.
const at = (d, h, m) => new Date(2026, 10, d, h, m, 0).getTime();
const iso = (d, h, m) => new Date(at(d, h, m)).toISOString();
const NOW = at(5, 9, 30);
const out = {};
const job = (name, o) => Object.assign({ name, kind: 'Plugin', mode: 'live', runs: 1, errors: 0, last_ok: true, last_run: null, next_run: null, schedule: null }, o || {});

// Sort: a job due tomorrow must come after one due later today.
const jobs = [
  job('tomorrow', { next_run: iso(6, 3, 20) }),
  job('later_today', { next_run: iso(5, 22, 30) }),
  job('soon', { next_run: iso(5, 9, 31) }),
  job('never', { next_run: null }),
  job('overdue', { next_run: iso(5, 9, 0) })
];
out.next = L.sortJobs(jobs, 'next', NOW).map((j) => j.name);
out.last = L.sortJobs([job('a', { last_run: iso(5, 9, 0) }), job('b', { last_run: iso(5, 9, 25) }), job('c'), job('d', { last_run: null, last_run_s: 60 })], 'last', NOW).map((j) => j.name);
out.name = L.sortJobs([job('b'), job('A'), job('c')], 'name', NOW).map((j) => j.name);
out.runs = L.sortJobs([job('x', { runs: 5 }), job('y', { runs: 50 }), job('z', { runs: 0 }), job('w', { runs: null })], 'runs', NOW).map((j) => j.name);

// Filter
const mix = [job('car_log'), job('bus_watch', { mode: 'dry' }), job('brain_stats', { mode: 'off', runs: 0, last_ok: null }), job('birthday', { runs: 0, last_ok: null, schedule: { kind: 'daily', at: '18:00' } })];
out.fLive = L.filterJobs(mix, '', 'live').map((j) => j.name);
out.fDry = L.filterJobs(mix, '', 'dry').map((j) => j.name);
out.fOff = L.filterJobs(mix, '', 'off').map((j) => j.name);
out.fWait = L.filterJobs(mix, '', 'wait').map((j) => j.name);
out.fSearch = L.filterJobs(mix, '  CAR ', 'all').map((j) => j.name);
out.fNone = L.filterJobs(mix, 'zzz', 'all').length;
out.counts = L.counts(mix);

// Summary
const sum = L.summarise([
  job('ok1', { last_run: iso(5, 9, 25), next_run: iso(5, 9, 40), runs: 4 }),
  job('bad', { last_ok: false, errors: 2, last_run: iso(5, 9, 10), next_run: iso(5, 9, 35) }),
  job('daily', { runs: 0, last_ok: null, schedule: { kind: 'daily', at: '18:00' }, next_run: iso(5, 18, 0) }),
  job('event', { runs: 0, last_ok: null, schedule: { kind: 'event' } }),
  job('off', { mode: 'off', runs: 0, last_ok: null, errors: 9 }),
  job('builtin', { kind: 'Built-in job', mode: 'dry', runs: 1, last_run: null, last_run_s: 30 })
], NOW);
out.sum = { failing: sum.failing, errors: sum.errors, last: sum.last && sum.last.name, next: sum.next && sum.next.name, waiting: sum.waiting, groups: sum.waitingGroups, live: sum.live, dry: sum.dry, off: sum.off };

// Text helpers
out.sched = [
  { kind: 'daily', at: '18:00' }, { kind: 'every', minutes: 10 }, { kind: 'every', minutes: 60 }, { kind: 'every', minutes: 360 },
  { kind: 'service', seconds: 90 }, { kind: 'event' }, null
].map((s) => L.scheduleText({ schedule: s }));
out.when = [L.formatWhen(at(5, 9, 25), NOW), L.formatWhen(at(6, 3, 20), NOW), L.formatWhen(at(4, 22, 30), NOW), L.formatWhen(null, NOW)];
out.why = [L.whyText('live is false'), L.whyText('kill switch file STOP_ALL exists'), L.whyText('something odd'), L.whyText(null)];
out.words = [L.modeLabel('dry'), L.kindLabel('Plugin'), L.kindLabel('Built-in job')];
console.log(JSON.stringify(out));
