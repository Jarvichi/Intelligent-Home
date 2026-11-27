/* Run with node ih_ui/tests/test_components.js; no browser or dependencies required. */
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const listeners = {};
const document = {
  addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
  removeEventListener(type, fn) { listeners[type] = (listeners[type] || []).filter(f => f !== fn); },
  querySelector() { return null; }
};
const window = {};
const customElements = {get() { return true; }, define() {}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'components.js'), 'utf8'), {window, document, customElements, HTMLElement: class {}, setTimeout, Object, Array, String});
function node() {
  const handlers = {}, attributes = {};
  return {hidden: false, tabIndex: 0, focused: false, handlers,
    addEventListener(type, fn) { handlers[type] = fn; },
    setAttribute(k, v) { attributes[k] = v; }, getAttribute(k) { return attributes[k] || null; },
    focus() { this.focused = true; }};
}
const a = node(), b = node(), p = node(), q = node();
const tabs = window.IHUI.tabs({querySelectorAll() { return [a, b]; }}, [p, q]);
assert.equal(a.getAttribute('aria-selected'), 'true'); assert.equal(q.hidden, true);
b.handlers.click(); assert.equal(a.tabIndex, -1); assert.equal(p.hidden, true); assert.equal(q.hidden, false);
let prevented = false; b.handlers.keydown({key: 'ArrowLeft', preventDefault() { prevented = true; }});
assert(prevented && a.focused && !p.hidden && q.hidden);
tabs.select(1, false); assert(!q.hidden);
const summary = node(), menu = {open: true, contains(target) { return target === this; }, querySelector() { return summary; }};
let closed = 0;
const control = window.IHUI.menu(menu, () => closed++);
listeners.keydown[0]({key: 'Escape', preventDefault() {}});
assert(!menu.open && summary.focused && closed === 1);
menu.open = true; listeners.click[0]({target: {}}); assert(!menu.open && closed === 2);
control.destroy(); assert.equal(listeners.click.length, 0); assert.equal(listeners.keydown.length, 0);
console.log('Shared menu and tabs interactions passed.');
