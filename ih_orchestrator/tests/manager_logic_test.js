// Run by test_manager_logic.py (node). Pure logic of the Manager page.
const assert = require('assert');
const M = require('../app/ui/manager-logic.js');
const A = (o) => Object.assign({ slug: 'ih_x', name: 'X', installed: true, state: 'started', update: false, missing_settings: 0, can_act: true, seen: null, used: null, ui: {} }, o);
const H = (o) => Object.assign({ services: [], counts: { OK: 5, FAIL: 0 }, stale: false, consumer_progress: [], ui_contributions: [] }, o);
const ctx = (o) => Object.assign({ addons: [A({})], health: H({}), usage: { addons: [] }, plugins: { installed: true, running: true, plugins: [] }, traces: [], settings: null, failed: {} }, o);

// ageText / plural
assert.strictEqual(M.ageText(null), 'never');
assert.strictEqual(M.ageText(30), '30 s ago');
assert.strictEqual(M.ageText(681936), '8 days ago');
assert.strictEqual(M.plural(1, 'setting', 'settings'), '1 setting');

// callsFailing: noise ignored, real failures flagged
assert.ok(!M.callsFailing({ failed: 4, total: 23671 }));
assert.ok(!M.callsFailing({ failed: 2, total: 271 }));
assert.ok(M.callsFailing({ failed: 18, total: 107 }));
assert.ok(M.callsFailing({ failed: 20, total: 583 }));
assert.ok(M.callsFailing({ failed: 3, total: 30 }));

// filterAddons: counts, filters, sort, search, not installed
const rows = [A({ slug: 'a', name: 'Alpha', used: { today: 5 } }), A({ slug: 'b', name: 'Beta', state: 'stopped', missing_settings: 3 }),
  A({ slug: 'c', name: 'Gamma', missing_settings: 1, update: true, used: { today: 9 } }), A({ slug: 'n', name: 'New', installed: false, state: null, can_act: true }),
  A({ slug: 'o', name: 'Own', installed: false, can_act: false })];
let r = M.filterAddons(rows, {});
assert.deepStrictEqual(r.counts, { all: 3, running: 2, stopped: 1, setup: 2, update: 1, notinst: 1 });
assert.deepStrictEqual(r.list.map(a => a.slug), ['b', 'c', 'a']); // needs a look first
assert.deepStrictEqual(M.filterAddons(rows, { sort: 'name' }).list.map(a => a.slug), ['a', 'b', 'c']);
assert.deepStrictEqual(M.filterAddons(rows, { sort: 'used' }).list.map(a => a.slug), ['c', 'a', 'b']);
assert.deepStrictEqual(M.filterAddons(rows, { show: 'stopped' }).list.map(a => a.slug), ['b']);
assert.deepStrictEqual(M.filterAddons(rows, { show: 'setup' }).list.map(a => a.slug), ['b', 'c']);
assert.deepStrictEqual(M.filterAddons(rows, { show: 'notinst' }).list.map(a => a.slug), ['n']);
assert.deepStrictEqual(M.filterAddons(rows, { q: ' GAM ' }).list.map(a => a.slug), ['c']);
assert.strictEqual(M.filterAddons(rows, { q: 'zzz' }).list.length, 0);
assert.strictEqual(M.filterAddons(rows, { q: 'zzz' }).total, 3);
const many = Array.from({ length: 40 }, (_, i) => A({ slug: 's' + i, name: 'N' + i }));
assert.strictEqual(M.filterAddons(many, {}).list.length, 40); // never silently capped

// worthALook
assert.deepStrictEqual(M.worthALook(ctx({})), []);
let w = M.worthALook(ctx({ addons: [A({ slug: 'ih_b', name: 'Beta', state: 'stopped', missing_settings: 3, seen: { age_s: 681936 } }), A({ slug: 'ih_c', name: 'Cee', missing_settings: 2, update: true, version: '1', latest: '2' })] }));
assert.strictEqual(w[0].title, 'Beta is stopped');
assert.strictEqual(w[0].detail, '3 settings missing. Last seen 8 days ago.');
assert.deepStrictEqual(w[0].go, { tab: 'addons', q: 'ih_b', show: 'all' });
assert.ok(w.some(x => x.title === 'Cee needs 2 settings'));
assert.ok(w.some(x => x.title === 'Cee has an update'));
w = M.worthALook(ctx({ usage: { addons: [{ slug: 'ih_x', total: 107, failed: 18 }] } }));
assert.strictEqual(w[0].title, 'X: 18 of 107 calls failed');
w = M.worthALook(ctx({ health: H({ counts: { OK: 4, FAIL: 1 }, services: [{ name: 'ih_x', state: 'FAIL', checks: ['no answer'] }] }) }));
assert.strictEqual(w[0].title, 'X failed its health check');
assert.strictEqual(w[0].tone, 'bad');
w = M.worthALook(ctx({ health: null, failed: { health: true } }));
assert.ok(w.some(x => x.tone === 'bad' && /health checks/.test(x.title)));
w = M.worthALook(ctx({ plugins: { installed: true, plugins: [{ name: 'car', last_ok: false, last_error: 'timeout' }] } }));
assert.strictEqual(w[0].title, 'Job car did not succeed last time');
w = M.worthALook(ctx({ health: H({ consumer_progress: [{ consumer: 'rob', status: 'stalled' }] }) }));
assert.ok(/rob looks stuck/.test(w[0].title));

// tiles: a failed read is "?" and never a calm 0
let t = M.tiles(ctx({ addons: null, failed: { addons: true } }));
assert.strictEqual(t[0].value, '?');
assert.strictEqual(t[0].tone, 'bad');
t = M.tiles(ctx({ traces: null, failed: { traces: true } }));
assert.ok(t.some(x => x.value === '?' && /requests/i.test(x.label)));
t = M.tiles(ctx({ addons: [A({}), A({ state: 'stopped', missing_settings: 2 })] }));
assert.strictEqual(t[0].value, '1 of 2');
assert.strictEqual(t[1].value, '1');
assert.ok(/2 settings missing/.test(t[1].label));
t = M.tiles(ctx({ health: H({ stale: true }) }));
assert.ok(/out of date/.test(t[3].label));

// pill
assert.deepStrictEqual(M.pill(ctx({})), { tone: 'ok', text: 'All healthy' });
assert.strictEqual(M.pill({ addons: null, health: null, failed: {} }).text, 'Checking...');
assert.strictEqual(M.pill(ctx({ addons: null, health: null, failed: { addons: true, health: true } })).tone, 'bad');
assert.strictEqual(M.pill(ctx({ health: H({ counts: { OK: 1, FAIL: 2 }, services: [{ state: 'FAIL', name: 'a' }, { state: 'FAIL', name: 'b' }] }) })).text, '2 checks failing');
assert.strictEqual(M.pill(ctx({ health: H({ stale: true }) })).tone, 'warn');
assert.strictEqual(M.pill(ctx({ addons: [A({ state: 'stopped' })] })).text, '1 thing to look at');

// traces
const tr = [{ trace_id: 'aa11', root: 'selfcheck' }, { trace_id: 'bb22', root: 'orchestrate' }, { trace_id: 'cc33', root: 'providers' }, { trace_id: 'dd44' }];
assert.strictEqual(M.filterTraces(tr, {}).length, 4);
assert.deepStrictEqual(M.filterTraces(tr, { hideRoutine: true }).map(x => x.trace_id), ['bb22', 'dd44']);
assert.strictEqual(M.filterTraces(tr, { q: ' BB' }).length, 1); // search ignores case and spaces
assert.deepStrictEqual(M.filterTraces(tr, { q: 'bb' }).map(x => x.trace_id), ['bb22']);
assert.deepStrictEqual(M.filterTraces(tr, { type: 'unknown' }).map(x => x.trace_id), ['dd44']);
assert.strictEqual(M.routineCount(tr), 2);
assert.deepStrictEqual(M.traceTypes(tr), ['orchestrate', 'providers', 'selfcheck', 'unknown']);

// health list filter
const sv = [{ name: 'Alpha', state: 'OK' }, { name: 'Beta', state: 'FAIL' }, { name: 'Gamma', state: 'MAYBE' }];
assert.deepStrictEqual(M.filterHealth(sv, { state: 'FAIL' }).map(x => x.name), ['Beta']);
assert.deepStrictEqual(M.filterHealth(sv, { state: 'UNKNOWN' }).map(x => x.name), ['Gamma']);
assert.deepStrictEqual(M.filterHealth(sv, { q: 'alp' }).map(x => x.name), ['Alpha']);

// attrChips keeps the old wording
assert.deepStrictEqual(M.attrChips({ sender: 'alice', in_bytes: 1300, capability: 'general_qa', classified: 1, tries: 1, retries: 2 }),
  ['from alice', 'in 1.3 KB', '2 retries', 'general_qa (auto)', '1 try']);
assert.ok(M.attrChips({ model: 'openai/gpt-oss-120b', score: 741 }).includes('score 0.741'));
assert.deepStrictEqual(M.attrChips(null), []);

// text helpers and old hashes
assert.strictEqual(M.usedTxt({ today: 3, failed_today: 1, last: '09:10', last_day: '2026-11-05', today_day: '2026-11-05' }), '3 today (1 failed), last 09:10');
assert.strictEqual(M.usedTxt(null), null);
assert.strictEqual(M.schedText({ kind: 'daily', at: '18:00' }), 'daily at 18:00');
assert.strictEqual(M.schedText({ kind: 'every', minutes: 10 }), 'every 10 min');
assert.strictEqual(M.imgTxt({ image: false }), 'no (built on the Pi)');
assert.strictEqual(M.tabFromHash('#plugins'), 'jobs');
assert.strictEqual(M.tabFromHash('traces'), 'requests');
assert.strictEqual(M.tabFromHash('#health'), 'health');
assert.strictEqual(M.tabFromHash('#nope'), null);
console.log('ok');

// jumpTargets: installed add-ons with a page, plain names, sorted, never the Manager itself
{
  const J = (o) => A(Object.assign({ full: 'abc_' + (o.slug || 'ih_x'), ingress: true }, o));
  const jt = M.jumpTargets([
    J({ slug: 'ih_jobs', name: 'Intelligent Home - Jobs' }), J({ slug: 'ih_games', name: 'Intelligent Home - Games', state: 'stopped' }),
    J({ slug: 'ih_orchestrator', name: 'Intelligent Home' }), J({ slug: 'ih_brain', name: 'Intelligent Home - Brain', ingress: false }),
    J({ slug: 'ih_new', name: 'Intelligent Home - New', installed: false }), J({ slug: 'ih_jobs_backup', name: 'Intelligent Home - Jobs - Backup' }),
    J({ slug: 'ih_nofull', name: 'X', full: null })]);
  assert.deepStrictEqual(jt.map(t => t.name), ['Games', 'Jobs', 'Jobs - Backup']);
  assert.deepStrictEqual(jt.map(t => t.running), [false, true, true]);
  assert.strictEqual(jt[0].href, '/app/abc_ih_games');
  assert.deepStrictEqual(M.jumpTargets(null), []);
  assert.strictEqual(M.plainName({ slug: 's', name: '' }), 's');
}
