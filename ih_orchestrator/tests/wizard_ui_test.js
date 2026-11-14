// Run by test_manager_logic.py (node). Pure logic of the setup wizard stepper.
const assert = require('assert');
const W = require('../app/ui/wizard-ui.js');
const state = { title: 't', first_run: true, groups: [
  { id: 'steam', label: 'Steam', enabled: false, fields: [{ option: 'steam_api_key', set: false }] },
  { id: 'psn', label: 'PSN', enabled: true, fields: [{ option: 'psn_npsso', set: true }] }] };
assert.deepStrictEqual(W.initialChosen(state), { steam: false, psn: false });            // first run: nothing pre-ticked
assert.deepStrictEqual(W.initialChosen(Object.assign({}, state, { first_run: false })), { steam: false, psn: true });
const steps = W.stepsFor(state, { steam: true, psn: false });
assert.deepStrictEqual(steps.map((s) => s.kind), ['choose', 'group', 'review']);
const a = W.answersFor(state, { chosen: { steam: true, psn: false }, values: { steam_api_key: ' K ', psn_npsso: 'ignored', other: 'x' } });
assert.deepStrictEqual(a, { groups: { steam: { values: { steam_api_key: 'K' }, enabled: true }, psn: { values: {}, enabled: false } } });
const blank = W.answersFor(state, { chosen: { steam: true, psn: true }, values: { steam_api_key: '   ' } });
assert.deepStrictEqual(blank.groups.steam.values, {});                                  // blank keeps the stored value
console.log('wizard ui ok');
