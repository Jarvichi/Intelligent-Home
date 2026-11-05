/* Pure page logic. Run: node --test ih_brain/tests/ (Node 22). Synthetic data only. */
const test = require('node:test'), assert = require('node:assert');
const L = require('../app/ui/logic.js');
const notes = [
  {path: 'index.md', title: 'Home'}, {path: 'tech/a.md', title: 'Beta'}, {path: 'tech/net/x.md', title: 'X'},
  {path: 'tech/net/y.md', title: 'Y'}, {path: 'people/p.md', title: 'P'},
];
test('folderView lists sub-folders with counts and direct notes', () => {
  const v = L.folderView(notes, 'tech');
  assert.deepStrictEqual(v.folders, [{name: 'net', path: 'tech/net', count: 2}]);
  assert.deepStrictEqual(v.notes.map(n => n.path), ['tech/a.md']);
  assert.deepStrictEqual(L.folderView(notes, 'tech/net').notes.map(n => n.title), ['X', 'Y']);
});
test('areaCards counts areas and names the root "top level"', () => {
  const c = L.areaCards(notes);
  assert.strictEqual(c[0].path, 'tech'); assert.strictEqual(c[0].count, 3);
  assert.ok(c.find(a => a.path === '' && a.title === 'top level'));
});
test('groupByDay groups newest first with Today and Yesterday labels', () => {
  const now = new Date(2026, 10, 5, 12, 0, 0), at = (d, h) => new Date(2026, 10, d, h).getTime() / 1000;
  const g = L.groupByDay([{mtime: at(5, 9), n: 1}, {mtime: at(4, 20), n: 2}, {mtime: at(5, 11), n: 3}, {mtime: at(1, 8), n: 4}, {mtime: new Date(2025, 9, 1).getTime() / 1000, n: 5}], now);
  assert.deepStrictEqual(g.map(x => x.label), ['Today, 5 Nov', 'Yesterday, 4 Nov', 'Sun 1 Nov', '1 Oct 2025']);
  assert.deepStrictEqual(g[0].rows.map(r => r.n), [3, 1]);
});
test('formatDate', () => {
  assert.strictEqual(L.formatDate('2026-10-13'), '13 Oct 2026');
  assert.strictEqual(L.formatDate(''), ''); assert.strictEqual(L.formatDate('unknown'), '');
});
test('snippetParts highlights matches and trims around the first hit', () => {
  const text = 'x '.repeat(100) + 'the Kindle refresh runs often ' + 'y '.repeat(100);
  const parts = L.snippetParts(text, L.searchTerms('kindle refresh'), 100);
  assert.ok(parts.some(p => p.hit && p.text.toLowerCase() === 'kindle'));
  assert.ok(parts.some(p => p.hit && p.text.toLowerCase() === 'refresh'));
  assert.ok(parts[0].text.startsWith('…'));
  assert.deepStrictEqual(L.snippetParts('', ['a']), []);
  assert.deepStrictEqual(L.snippetParts('plain words', []).map(p => p.hit), [false]);
});
test('searchTerms drops one-letter words', () => assert.deepStrictEqual(L.searchTerms('a Kindle-x b ok'), ['kindle-x', 'ok']));
test('mostConnected ranks by total links and counts inbound', () => {
  const nodes = [{id: 'a', t: 'A', g: 'g'}, {id: 'b', t: 'B'}, {id: 'c', t: 'C'}], links = [{s: 'a', t: 'b'}, {s: 'c', t: 'b'}, {s: 'a', t: 'c'}];
  const top = L.mostConnected(nodes, links, 2);
  assert.deepStrictEqual(top.map(t => [t.id, t.links, t.linkedFrom]), [['a', 2, 0], ['b', 2, 2]].sort((x, y) => x[0].localeCompare(y[0])));
  assert.strictEqual(top.length, 2);
});
test('showLabel: hubs always, more labels as zoom grows', () => {
  assert.ok(L.showLabel(0, 1)); assert.ok(!L.showLabel(20, 1)); assert.ok(L.showLabel(20, 1.6));
  assert.ok(!L.showLabel(50, 1.6)); assert.ok(L.showLabel(40, 2.6)); assert.ok(!L.showLabel(60, 2.6)); assert.ok(L.showLabel(100, 5)); assert.ok(L.showLabel(700, 8));
});
test('filterGraph keeps only links inside the area', () => {
  const nodes = [{id: 'a', g: 'x'}, {id: 'b', g: 'x'}, {id: 'c', g: 'y'}], links = [{s: 'a', t: 'b'}, {s: 'a', t: 'c'}];
  const f = L.filterGraph(nodes, links, 'x'); assert.strictEqual(f.nodes.length, 2); assert.strictEqual(f.links.length, 1);
  assert.strictEqual(L.filterGraph(nodes, links, '').links.length, 2);
});
test('plainText drops headings, code and link brackets', () => {
  assert.strictEqual(L.plainText('# Title\n\nSee [[a/b|Bee]] and **bold**.\n```\nhidden\n```\nEnd'), 'See Bee and bold. End');
});
