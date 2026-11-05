/* Pure logic for the Jobs page: no DOM, no network. Loaded by the page and by the unit tests. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.JobsLogic = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  const isNum = (v) => typeof v === 'number' && isFinite(v);
  const pad = (n) => (n < 10 ? '0' : '') + n;

  function parseMs(value) {
    if (typeof value !== 'string' || !value) return null;
    const ms = Date.parse(value);
    return isNaN(ms) ? null : ms;
  }
  function modeOf(job) { return job && (job.mode === 'live' || job.mode === 'dry' || job.mode === 'off') ? job.mode : 'dry'; }
  // When the job last ran, in ms. Tasks report a time; built-in jobs report seconds before the check.
  function lastMs(job, checkedMs) {
    const t = parseMs(job.last_run);
    if (t !== null) return t;
    if (isNum(job.last_run_s) && isNum(checkedMs)) return checkedMs - Math.max(0, job.last_run_s) * 1000;
    return null;
  }
  function nextMs(job) { return parseMs(job.next_run); }
  function hasRun(job) { return isNum(job.runs) ? job.runs > 0 : job.last_ok === true || job.last_ok === false; }
  function notRunYet(job) { return modeOf(job) !== 'off' && !hasRun(job); }
  function failed(job) { return modeOf(job) !== 'off' && job.last_ok === false; }

  function scheduleKind(job) {
    const s = job && job.schedule;
    return s && typeof s === 'object' && typeof s.kind === 'string' ? s.kind : 'none';
  }
  function scheduleText(job) {
    const s = job && job.schedule;
    if (!s || typeof s !== 'object') return 'not scheduled';
    if (s.kind === 'daily' && typeof s.at === 'string') return 'daily at ' + s.at;
    if (s.kind === 'every' && isNum(s.minutes)) return s.minutes % 60 === 0 && s.minutes >= 60 ? 'every ' + (s.minutes / 60) + (s.minutes === 60 ? ' hour' : ' hours') : 'every ' + s.minutes + ' min';
    if (s.kind === 'service' && isNum(s.seconds)) return 'always on, every ' + s.seconds + ' s';
    if (s.kind === 'event') return 'when something happens';
    return 'not scheduled';
  }

  // "09:25", "09:25 tomorrow", "22:30 yesterday", or "8 Nov, 03:20".
  function formatWhen(ms, nowMs, locale) {
    if (!isNum(ms)) return null;
    const d = new Date(ms), n = new Date(isNum(nowMs) ? nowMs : Date.now());
    const clock = pad(d.getHours()) + ':' + pad(d.getMinutes());
    const day = (x) => Date.UTC(x.getFullYear(), x.getMonth(), x.getDate());
    const diff = Math.round((day(d) - day(n)) / 86400000);
    if (diff === 0) return clock;
    if (diff === 1) return clock + ' tomorrow';
    if (diff === -1) return clock + ' yesterday';
    return d.toLocaleDateString(locale || undefined, { day: 'numeric', month: 'short' }) + ', ' + clock;
  }

  const WHY = {
    'live': 'Live',
    'live is false': 'Test mode: it has not been set live',
    'mode option is dry': 'Test mode: the add-on is set to test mode',
    'not in live_jobs': 'Test mode: not on the live list',
    'old_loops_disabled is false': 'Test mode: the older loop is still switched on',
    'not listed in plugins option': 'Switched off: not in the add-on task list',
    'unknown plugin name': 'Switched off: the task name is not recognised'
  };
  function whyText(why) {
    if (typeof why !== 'string' || !why) return 'Not reported';
    if (WHY[why]) return WHY[why];
    if (why.indexOf('kill switch') === 0) return 'Test mode: the stop switch is on';
    return why;
  }
  function modeLabel(mode) { return mode === 'live' ? 'Live' : mode === 'off' ? 'Switched off' : 'Test mode'; }
  function kindLabel(kind) { return kind === 'Plugin' ? 'Background task' : typeof kind === 'string' && kind ? kind : 'Not reported'; }

  function compare(a, b) { return a < b ? -1 : a > b ? 1 : 0; }
  // Sort by real time values, never by display text. Missing values go last. Name breaks ties.
  function sortJobs(jobs, key, checkedMs) {
    const out = jobs.slice();
    const byName = (a, b) => String(a.name).localeCompare(String(b.name));
    const asc = (get) => (a, b) => {
      const x = get(a), y = get(b);
      if (x === null && y === null) return byName(a, b);
      if (x === null) return 1;
      if (y === null) return -1;
      return compare(x, y) || byName(a, b);
    };
    if (key === 'name') out.sort(byName);
    else if (key === 'runs') out.sort((a, b) => (isNum(b.runs) ? b.runs : -1) - (isNum(a.runs) ? a.runs : -1) || byName(a, b));
    else if (key === 'last') out.sort(asc((j) => { const t = lastMs(j, checkedMs); return t === null ? null : -t; }));
    else out.sort(asc(nextMs));
    return out;
  }

  // show: all | live | dry | off | wait. Search matches the name, case-insensitively.
  function matches(job, query, show) {
    const q = String(query || '').trim().toLowerCase();
    if (q && String(job.name).toLowerCase().indexOf(q) < 0) return false;
    const m = modeOf(job);
    if (show === 'live') return m === 'live';
    if (show === 'dry') return m === 'dry';
    if (show === 'off') return m === 'off';
    if (show === 'wait') return notRunYet(job);
    return true;
  }
  function filterJobs(jobs, query, show) { return jobs.filter((j) => matches(j, query, show)); }

  function counts(jobs) {
    const c = { all: jobs.length, live: 0, dry: 0, off: 0, wait: 0 };
    for (const j of jobs) { c[modeOf(j)]++; if (notRunYet(j)) c.wait++; }
    return c;
  }

  const WAIT_LABEL = { daily: 'daily', event: 'on an event', every: 'repeating', service: 'repeating', none: 'not scheduled' };
  function summarise(jobs, checkedMs) {
    const active = jobs.filter((j) => modeOf(j) !== 'off');
    const failing = active.filter(failed);
    const errors = active.reduce((n, j) => n + (isNum(j.errors) ? j.errors : 0), 0);
    let last = null, next = null;
    for (const j of active) {
      const l = lastMs(j, checkedMs);
      if (l !== null && (last === null || l > last.ms)) last = { ms: l, name: j.name };
      const n = nextMs(j);
      if (n !== null && (next === null || n < next.ms)) next = { ms: n, name: j.name };
    }
    const waiting = active.filter(notRunYet), groups = {};
    for (const j of waiting) { const k = WAIT_LABEL[scheduleKind(j)] || 'not scheduled'; groups[k] = (groups[k] || 0) + 1; }
    const c = counts(jobs);
    return { total: jobs.length, live: c.live, dry: c.dry, off: c.off, failing: failing.map((j) => j.name), errors, last, next, waiting: waiting.length, waitingGroups: groups };
  }

  return { parseMs, modeOf, lastMs, nextMs, hasRun, notRunYet, failed, scheduleKind, scheduleText, formatWhen, whyText, modeLabel, kindLabel, sortJobs, matches, filterJobs, counts, summarise };
});
