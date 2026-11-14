/* Setup wizard stepper (window.IHWizard). Pure logic (stepsFor, answersFor) is tested in Node; the DOM part uses textContent only.
   Secret fields are password inputs; the server never sends values back, so a blank field means "keep what is stored". */
(function (root) {
  'use strict';
  /* steps: 0 = choose services, then one step per chosen group that has fields, then one review step. */
  function stepsFor(state, chosen) {
    var s = [{ kind: 'choose' }];
    state.groups.forEach(function (g) { if (chosen[g.id]) s.push({ kind: 'group', group: g }); });
    s.push({ kind: 'review' });
    return s;
  }
  /* form = {chosen:{id:bool}, values:{option:string}}. Only options of a chosen group are sent; blanks are dropped. */
  function answersFor(state, form) {
    var out = { groups: {} };
    state.groups.forEach(function (g) {
      var on = !!form.chosen[g.id], vals = {};
      if (on) g.fields.forEach(function (f) { var v = (form.values[f.option] || '').trim(); if (v) vals[f.option] = v; });
      var a = { values: vals };
      if (g.enabled !== null) a.enabled = on;
      out.groups[g.id] = a;
    });
    return out;
  }
  function initialChosen(state) {
    var c = {};
    state.groups.forEach(function (g) { c[g.id] = g.enabled === null ? true : (state.first_run ? false : !!g.enabled); });
    return c;
  }
  var api = { stepsFor: stepsFor, answersFor: answersFor, initialChosen: initialChosen };

  if (typeof document !== 'undefined') {
    var E = function (tag, txt, cls) { var e = document.createElement(tag); if (txt != null) e.textContent = txt; if (cls) e.className = cls; return e; };
    api.open = function (opts) {
      /* opts = {base, slug, onDone}. base is the page's ingress path ending in '/'. */
      fetch(opts.base + 'api/wizard/' + encodeURIComponent(opts.slug)).then(function (r) { return r.json().then(function (j) { return [r.status, j]; }); }).then(function (y) {
        if (y[0] !== 200) { note(y[1].error || 'Could not open the setup.'); return; }
        run(opts, y[1]);
      }).catch(function () { note('Could not open the setup.'); });
    };
    var note = function (t) { var m = document.getElementById('msg'); if (m) { m.textContent = t; m.classList.remove('hidden'); } };
    var run = function (opts, state) {
      var form = { chosen: initialChosen(state), values: {} }, i = 0, saving = false;
      var dlg = document.createElement('section'); dlg.className = 'card'; dlg.setAttribute('role', 'group'); dlg.setAttribute('aria-label', state.title);
      dlg.style.cssText = 'position:fixed;z-index:50;left:50%;top:6vh;transform:translateX(-50%);max-width:520px;width:92vw;max-height:88vh;overflow:auto';
      document.body.appendChild(dlg);
      var close = function () { dlg.remove(); if (opts.onDone) opts.onDone(); };
      var paint = function () {
        dlg.textContent = '';
        var steps = stepsFor(state, form.chosen), st = steps[i];
        dlg.appendChild(E('h2', state.title)); dlg.appendChild(E('p', 'Step ' + (i + 1) + ' of ' + steps.length, 'meta'));
        var msg = E('p', '', 'warnt'); msg.setAttribute('role', 'alert');
        if (st.kind === 'choose') {
          dlg.appendChild(E('p', 'Pick the services to set up.', 'meta'));
          state.groups.forEach(function (g) {
            var l = E('label'); l.style.cssText = 'display:flex;gap:8px;align-items:center;min-height:44px';
            var c = E('input'); c.type = 'checkbox'; c.checked = !!form.chosen[g.id]; c.onchange = function () { form.chosen[g.id] = c.checked; paint(); };
            l.appendChild(c); l.appendChild(document.createTextNode(g.label + (g.complete ? ' (keys already set)' : ''))); dlg.appendChild(l);
          });
        } else if (st.kind === 'group') {
          dlg.appendChild(E('h3', st.group.label)); if (st.group.help) dlg.appendChild(E('p', st.group.help, 'meta'));
          st.group.fields.forEach(function (f) {
            var l = E('label'); l.style.cssText = 'display:grid;gap:4px;margin:8px 0';
            l.appendChild(document.createTextNode(f.label));
            var inp = E('input'); inp.type = f.kind === 'secret' ? 'password' : 'text'; inp.autocomplete = 'off'; inp.disabled = !f.supported;
            inp.placeholder = f.set ? 'Already set. Leave blank to keep it.' : 'Not set'; inp.value = form.values[f.option] || '';
            inp.oninput = function () { form.values[f.option] = inp.value; }; l.appendChild(inp); dlg.appendChild(l);
          });
        } else {
          dlg.appendChild(E('p', 'Saving updates the add-on options' + (state.restarts ? ' and restarts the add-on.' : '.'), 'meta'));
          var ul = E('ul'); state.groups.forEach(function (g) { ul.appendChild(E('li', g.label + ': ' + (form.chosen[g.id] ? 'on' : 'off'))); }); dlg.appendChild(ul);
        }
        dlg.appendChild(msg);
        var bs = E('div', null, 'btns');
        var mk = function (label, fn, cls) { var b = E('button', label, 'btn' + (cls ? ' ' + cls : '')); b.type = 'button'; b.onclick = fn; return b; };
        bs.appendChild(mk('Cancel', close));
        if (i > 0) bs.appendChild(mk('Back', function () { i--; paint(); }));
        if (st.kind !== 'review') bs.appendChild(mk('Next', function () { i++; paint(); }, 'primary'));
        else bs.appendChild(mk('Finish and save', function () {
          if (saving) return; saving = true; msg.textContent = 'Saving ...';
          fetch(opts.base + 'api/wizard/' + encodeURIComponent(opts.slug), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(answersFor(state, form)) })
            .then(function (r) { return r.json().then(function (j) { return [r.status, j]; }); }).then(function (y) {
              saving = false;
              if (y[0] === 200) { msg.textContent = y[1].changed.length ? 'Saved.' + (y[1].restarted ? ' The add-on restarted.' : '') : 'Nothing changed.'; setTimeout(close, 1200); }
              else msg.textContent = y[1].error || 'Could not save.';
            }).catch(function () { saving = false; msg.textContent = 'Could not save. Try again.'; });
        }, 'primary'));
        dlg.appendChild(bs);
      };
      paint(); dlg.tabIndex = -1; dlg.focus();
    };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.IHWizard = api;
})(typeof window !== 'undefined' ? window : globalThis);
