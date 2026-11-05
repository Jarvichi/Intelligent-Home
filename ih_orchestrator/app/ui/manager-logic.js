/* Pure logic for the Manager page (no DOM, no network). Loaded by index.html as window.IHM and by the unit tests in Node.
   ctx = {addons: rows|null, health: obj|null, usage: obj|null, plugins: obj|null, traces: rows|null, settings: obj|null,
          failed: {addons: bool, health: bool, usage: bool, plugins: bool, traces: bool}}
   A source that could not be read is null AND failed[name] is true: it is never treated as "zero". */
(function (root) {
  'use strict';
  var ROUTINE_ROOTS = ['selfcheck', 'providers', 'mcp_rpc'];

  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }
  function isRoutine(r) { return ROUTINE_ROOTS.indexOf(r || '') >= 0; }

  function ageText(sec) {
    if (sec === null || sec === undefined || isNaN(sec)) return 'never';
    sec = Math.max(0, Math.round(sec));
    if (sec < 90) return sec + ' s ago';
    if (sec < 5400) return Math.round(sec / 60) + ' min ago';
    if (sec < 129600) return Math.round(sec / 3600) + ' h ago';
    return Math.round(sec / 86400) + ' days ago';
  }
  function seenText(seen) { return seen && seen.age_s != null ? ageText(seen.age_s) : 'never'; }

  function installed(rows) { return (rows || []).filter(function (a) { return a.installed; }); }
  function isStopped(a) { return a.installed && a.state !== 'started'; }
  function missing(a) { return a.installed && a.missing_settings > 0 ? a.missing_settings : 0; }
  function title(a) { return (a.ui && a.ui.title) || a.name || a.slug; }
  function addonName(a) { return a.name || (a.ui && a.ui.title) || a.slug; }

  function usageFor(usage, slug) {
    var r = usage && usage.addons ? usage.addons.filter(function (x) { return x.slug === slug; })[0] : null;
    return r || null;
  }
  /* Many failed calls over 7 days: at least 10, or at least 3 and 5% or more. Fewer is background noise. */
  function callsFailing(u) {
    if (!u || !u.failed) return false;
    return u.failed >= 10 || (u.failed >= 3 && u.failed * 20 >= u.total);
  }

  function addonAttention(a, usage) {
    if (!a.installed) return 0;
    var n = 0;
    if (isStopped(a)) n += 2;
    if (missing(a)) n += 1;
    if (a.update) n += 1;
    if (callsFailing(usageFor(usage, a.slug))) n += 1;
    return n;
  }

  function filterAddons(rows, o) {
    o = o || {};
    var q = String(o.q || '').trim().toLowerCase(), show = o.show || 'all', usage = o.usage || null;
    var inst = installed(rows), notInst = (rows || []).filter(function (a) { return !a.installed && a.can_act; });
    var counts = {
      all: inst.length, running: inst.filter(function (a) { return a.state === 'started'; }).length,
      stopped: inst.filter(isStopped).length, setup: inst.filter(missing).length,
      update: inst.filter(function (a) { return a.update; }).length, notinst: notInst.length
    };
    var base = show === 'notinst' ? notInst : inst.filter(function (a) {
      return show === 'all' || (show === 'running' && a.state === 'started') || (show === 'stopped' && isStopped(a)) ||
        (show === 'setup' && missing(a)) || (show === 'update' && a.update);
    });
    var hit = base.filter(function (a) {
      return !q || (addonName(a) + ' ' + title(a) + ' ' + a.slug).toLowerCase().indexOf(q) >= 0;
    });
    var sort = o.sort || 'attn';
    hit = hit.map(function (a, i) { return [a, i]; });
    hit.sort(function (x, y) {
      var a = x[0], b = y[0], c = 0;
      if (sort === 'name') c = addonName(a).localeCompare(addonName(b));
      else if (sort === 'used') c = ((b.used && b.used.today) || 0) - ((a.used && a.used.today) || 0);
      else c = addonAttention(b, usage) - addonAttention(a, usage);
      return c || addonName(a).localeCompare(addonName(b)) || x[1] - y[1];
    });
    return { list: hit.map(function (p) { return p[0]; }), counts: counts, total: base.length };
  }

  function jobProblem(p) { return !!(p && (p.last_ok === false || (p.errors > 0 && p.last_error))); }

  function failingChecks(health) {
    return health && health.services ? health.services.filter(function (s) { return s.state === 'FAIL'; }) : [];
  }
  function healthUnreadable(ctx) {
    var h = ctx.health;
    return !!(ctx.failed && ctx.failed.health) || !!(h && (h.stale || h.error || !h.counts || !Object.keys(h.counts).length));
  }

  /* "Worth a look": one row per thing that is not right. tone: bad | warn. go: {tab, q, show} for the Open button. */
  function worthALook(ctx) {
    var out = [], rows = ctx.addons, f = ctx.failed || {};
    var nameOf = {};
    (rows || []).forEach(function (a) { nameOf[a.slug] = addonName(a); });
    failingChecks(ctx.health).forEach(function (s) {
      out.push({ tone: 'bad', title: (nameOf[s.name] || s.name) + ' failed its health check', detail: (s.checks && s.checks[0]) || 'Open Health to see why.', go: { tab: 'health' } });
    });
    if (f.health) out.push({ tone: 'bad', title: 'Could not read the health checks', detail: 'This is not the same as healthy. Try again.', go: { tab: 'health' } });
    else if (ctx.health && healthUnreadable(ctx)) out.push({ tone: 'warn', title: 'Health data is out of date', detail: 'Open Health before relying on the numbers.', go: { tab: 'health' } });
    if (f.addons) out.push({ tone: 'bad', title: 'Could not read the add-ons', detail: 'Try again from the Add-ons tab.', go: { tab: 'addons' } });
    installed(rows).forEach(function (a) {
      var m = missing(a);
      if (isStopped(a)) {
        out.push({ tone: 'warn', title: addonName(a) + ' is stopped', detail: (m ? plural(m, 'setting', 'settings') + ' missing. ' : '') + 'Last seen ' + seenText(a.seen) + '.', go: { tab: 'addons', q: a.slug, show: 'all' } });
      } else if (m) {
        out.push({ tone: 'warn', title: addonName(a) + ' needs ' + plural(m, 'setting', 'settings'), detail: 'Running, but not fully set up.', go: { tab: 'addons', q: a.slug, show: 'all' } });
      }
      if (a.update) out.push({ tone: 'warn', title: addonName(a) + ' has an update', detail: 'Version ' + (a.version || '?') + ' is installed. ' + (a.latest || 'A newer one') + ' is available.', go: { tab: 'addons', q: a.slug, show: 'all' } });
    });
    if (f.usage) out.push({ tone: 'warn', title: 'Could not read the 7-day usage', detail: 'Failed calls cannot be checked until it loads.', go: { tab: 'health' } });
    else if (ctx.usage && ctx.usage.addons) {
      ctx.usage.addons.forEach(function (u) {
        if (callsFailing(u)) out.push({ tone: 'warn', title: (nameOf[u.slug] || u.slug) + ': ' + u.failed + ' of ' + u.total + ' calls failed', detail: 'Last 7 days. Usage is on the Health tab.', go: { tab: 'health' } });
      });
    }
    if (f.plugins) out.push({ tone: 'warn', title: 'Could not read the jobs', detail: 'Try again from the Jobs tab.', go: { tab: 'jobs' } });
    else if (ctx.plugins && ctx.plugins.plugins) {
      ctx.plugins.plugins.forEach(function (p) {
        if (jobProblem(p)) out.push({ tone: 'warn', title: 'Job ' + p.name + ' did not succeed last time', detail: p.last_error || 'Open Jobs for details.', go: { tab: 'jobs' } });
      });
    }
    var cons = ctx.health && ctx.health.consumer_progress ? ctx.health.consumer_progress : [];
    cons.forEach(function (c) {
      if (c.status === 'stalled') out.push({ tone: 'bad', title: 'Consumer ' + c.consumer + ' looks stuck', detail: 'Something is waiting and not moving. Open Health.', go: { tab: 'health' } });
    });
    if (f.traces) out.push({ tone: 'warn', title: 'Could not read the recent requests', detail: 'Try again from the Requests tab.', go: { tab: 'requests' } });
    return out;
  }

  function sum(a, fn) { return a.reduce(function (n, x) { return n + (fn(x) || 0); }, 0); }

  /* tone: ok | warn | bad | '' ; value is '?' when the source could not be read. */
  function tiles(ctx) {
    var f = ctx.failed || {}, out = [], rows = ctx.addons, inst = installed(rows);
    if (f.addons || !rows) {
      out.push({ value: '?', label: 'Add-ons running (could not read)', tone: 'bad' });
      out.push({ value: '?', label: 'Add-ons need setup (could not read)', tone: 'bad' });
      out.push({ value: '?', label: 'Updates waiting (could not read)', tone: 'bad' });
    } else {
      var running = inst.filter(function (a) { return a.state === 'started'; }).length;
      var setup = inst.filter(missing), miss = sum(setup, missing), upd = inst.filter(function (a) { return a.update; }).length;
      out.push({ value: running + ' of ' + inst.length, label: 'Add-ons running', tone: running === inst.length ? 'ok' : 'warn' });
      out.push({ value: String(setup.length), label: 'Add-ons need setup (' + plural(miss, 'setting', 'settings') + ' missing)', tone: setup.length ? 'warn' : 'ok' });
      out.push({ value: String(upd), label: 'Updates waiting', tone: upd ? 'warn' : 'ok' });
    }
    if (f.health || !ctx.health) out.push({ value: '?', label: 'Health checks failing (could not read)', tone: 'bad' });
    else {
      var cn = ctx.health.counts || {}, fails = cn.FAIL || 0, stale = healthUnreadable(ctx);
      out.push({ value: String(fails), label: stale ? 'Health checks failing (data out of date)' : 'Health checks failing (' + (cn.OK || 0) + ' pass)', tone: fails ? 'bad' : stale ? 'warn' : 'ok' });
    }
    if (f.traces || !ctx.traces) out.push({ value: '?', label: 'Recent requests (could not read)', tone: 'bad' });
    else {
      var bad = ctx.traces.filter(function (t) { return t.status === 'error'; }).length;
      out.push({ value: String(ctx.traces.length), label: 'Recent requests traced, ' + bad + ' failed', tone: bad ? 'warn' : '' });
    }
    if (f.plugins || !ctx.plugins) out.push({ value: '?', label: 'Jobs (could not read)', tone: 'bad' }, { value: '?', label: 'Job errors (could not read)', tone: 'bad' });
    else if (!ctx.plugins.installed) out.push({ value: '-', label: 'Jobs add-on not installed', tone: '' }, { value: '-', label: 'Job errors', tone: '' });
    else {
      var pl = ctx.plugins.plugins || [], live = pl.filter(function (p) { return p.live; }).length, probs = pl.filter(jobProblem).length;
      out.push({ value: live + ' live', label: 'Jobs on (' + (pl.length - live) + ' test mode)', tone: '' });
      out.push({ value: String(probs), label: probs === 1 ? 'Job that did not succeed' : 'Jobs that did not succeed', tone: probs ? 'warn' : 'ok' });
    }
    var cons = ctx.health && ctx.health.consumer_progress;
    if (f.health || !ctx.health) out.push({ value: '?', label: 'Consumers (could not read)', tone: 'bad' });
    else if (!cons || !cons.length) out.push({ value: '-', label: 'Consumers: none reported', tone: '' });
    else {
      var idle = cons.filter(function (c) { return c.status === 'idle'; }).length;
      out.push({ value: idle + ' idle', label: 'Consumers with nothing waiting (of ' + cons.length + ')', tone: idle === cons.length ? 'ok' : '' });
    }
    return out;
  }

  /* The pill by the heading. Uses only add-ons and health so it is the same on every tab. */
  function pill(ctx) {
    var f = ctx.failed || {};
    if (f.addons && f.health) return { tone: 'bad', text: 'Cannot read data' };
    if (!ctx.addons && !ctx.health) return { tone: '', text: 'Checking...' };
    var fails = failingChecks(ctx.health).length;
    if (fails) return { tone: 'bad', text: plural(fails, 'check', 'checks') + ' failing' };
    if (f.health || (ctx.health && healthUnreadable(ctx))) return { tone: 'warn', text: 'Health unknown' };
    if (f.addons) return { tone: 'warn', text: 'Add-ons unknown' };
    var inst = installed(ctx.addons), n = inst.filter(function (a) { return isStopped(a) || missing(a) || a.update; }).length;
    if (n) return { tone: 'warn', text: plural(n, 'thing', 'things') + ' to look at' };
    if (!ctx.health || !ctx.addons) return { tone: '', text: 'Checking...' };
    return { tone: 'ok', text: 'All healthy' };
  }

  function filterTraces(rows, o) {
    o = o || {};
    var q = String(o.q || '').trim().toLowerCase();
    return (rows || []).filter(function (r) {
      return (!q || String(r.trace_id).indexOf(q) >= 0) && (!o.type || (r.root || 'unknown') === o.type) && !(o.hideRoutine && isRoutine(r.root));
    });
  }
  function traceTypes(rows) {
    var seen = {}, out = [];
    (rows || []).forEach(function (r) { var t = r.root || 'unknown'; if (!seen[t]) { seen[t] = 1; out.push(t); } });
    return out.sort();
  }
  function routineCount(rows) { return (rows || []).filter(function (r) { return isRoutine(r.root); }).length; }

  /* state: '' | FAIL | OK | UNKNOWN (UNKNOWN also matches any state that is not OK or FAIL) */
  function filterHealth(services, o) {
    o = o || {};
    var q = String(o.q || '').trim().toLowerCase(), st = o.state || '';
    return (services || []).filter(function (s) {
      var state = s.state === 'OK' || s.state === 'FAIL' ? s.state : 'UNKNOWN';
      return (!q || String(s.name).toLowerCase().indexOf(q) >= 0) && (!st || state === st);
    });
  }

  function attrChips(a) {
    var o = [];
    if (!a || typeof a !== 'object') return o;
    function n(v) { return typeof v === 'number' ? v : parseInt(v, 10) || 0; }
    function B(v) { v = n(v); return v >= 1048576 ? (v / 1048576).toFixed(1) + ' MB' : v >= 1024 ? (v / 1024).toFixed(1) + ' KB' : v + ' B'; }
    var L = {
      sender: function (v) { return 'from ' + v; }, persona: function (v) { return 'persona ' + v; }, kind: function (v) { return 'kind ' + v; },
      in_bytes: function (v) { return 'in ' + B(v); }, out_bytes: function (v) { return 'out ' + B(v); },
      attachments: function (v) { return n(v) + (n(v) === 1 ? ' attachment' : ' attachments'); }, items: function (v) { return n(v) + (n(v) === 1 ? ' item' : ' items'); },
      http_status: function (v) { return 'HTTP ' + v; }, retries: function (v) { return n(v) + (n(v) === 1 ? ' retry' : ' retries'); }, chars: function (v) { return n(v) + ' chars'; },
      capability: function (v) { return v + (a.classified === 1 ? ' (auto)' : ''); }, agent: function (v) { return '→ ' + v; }, model: function (v) { return 'model ' + v; },
      score: function (v) { return 'score ' + (n(v) / 1000).toFixed(3); }, candidates: function (v) { return n(v) + (n(v) === 1 ? ' candidate' : ' candidates'); },
      tries: function (v) { return n(v) + (n(v) === 1 ? ' try' : ' tries'); }, results: function (v) { return n(v) + (n(v) === 1 ? ' result' : ' results'); }
    };
    Object.keys(L).forEach(function (k) { if (a[k] !== undefined && a[k] !== null) o.push(L[k](a[k])); });
    return o;
  }

  function usedTxt(u) {
    if (!u) return null;
    return u.today + ' today' + (u.failed_today ? ' (' + u.failed_today + ' failed)' : '') + ', last ' + (u.last ? (u.last_day === u.today_day || !u.last_day ? u.last : u.last + ' on ' + u.last_day.slice(5)) : 'never seen');
  }
  function imgTxt(a) { return a.image ? 'yes (tag ' + (a.latest || a.version || '?') + ')' : 'no (built on the Pi)'; }
  function schedText(s) {
    if (!s) return '-';
    return s.kind === 'daily' ? 'daily at ' + s.at : s.kind === 'every' ? 'every ' + s.minutes + ' min' : String(s.kind);
  }
  /* Old #hash names still open the right tab. */
  var TAB_ALIAS = { overview: 'today', install: 'addons', plugins: 'jobs', traces: 'requests', settings: 'addons', usage: 'health' };
  function tabFromHash(h) {
    h = String(h || '').replace(/^#/, '');
    var tabs = ['today', 'addons', 'jobs', 'requests', 'health'];
    if (tabs.indexOf(h) >= 0) return h;
    return TAB_ALIAS[h] || null;
  }

  /* Jump to: every installed IH add-on that has its own page. Plain names, sorted, never the Manager itself. */
  function plainName(a) {
    return String(addonName(a)).replace(/^\s*Intelligent Home\s*[-:\u2013\u2014]\s*/i, '').trim() || a.slug;
  }
  function jumpTargets(rows) {
    return installed(rows).filter(function (a) { return a.full && a.ingress && a.slug !== 'ih_orchestrator'; }).map(function (a) {
      return { slug: a.slug, full: a.full, name: plainName(a), running: a.state === 'started', href: '/app/' + encodeURIComponent(a.full) };
    }).sort(function (x, y) { return x.name.localeCompare(y.name); });
  }

  var api = {
    jumpTargets: jumpTargets, plainName: plainName,
    ROUTINE_ROOTS: ROUTINE_ROOTS, plural: plural, isRoutine: isRoutine, ageText: ageText, seenText: seenText, addonAttention: addonAttention,
    filterAddons: filterAddons, worthALook: worthALook, tiles: tiles, pill: pill, filterTraces: filterTraces, traceTypes: traceTypes,
    routineCount: routineCount, filterHealth: filterHealth, attrChips: attrChips, usedTxt: usedTxt, imgTxt: imgTxt, schedText: schedText,
    callsFailing: callsFailing, jobProblem: jobProblem, tabFromHash: tabFromHash, addonName: addonName, missing: missing, isStopped: isStopped,
    healthUnreadable: healthUnreadable, usageFor: usageFor, installed: installed
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.IHM = api;
})(typeof window !== 'undefined' ? window : globalThis);
