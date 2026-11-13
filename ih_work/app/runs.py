"""Scheduled and manual runs (pure logic, no files, no clock, no network). A run is a time-boxed spell in which the backlog feeder queues
to-do items and the gate follows the run's own pace target instead of the day/night levels.
Kinds: 'night' and 'day' (scheduled, each with its own settings) and 'now' (started by hand). Only one run is active at a time.
Phases (0.6.0): DRAIN (feed nothing while the queue holds queued or running jobs) -> PLAN (one free-AI plan_todos job, bounded wait) -> REVIEW (one review_batch job: Claude checks the oldest unreviewed outside-AI answers, bounded wait; skipped when review_batch_size is 0) -> BLOCK (feed
block_size to-do items at once, wait until the worker has consumed them) -> DRAIN again. A short block (fewer items than asked) means the backlog is empty.
A run ends when: the item count is reached, the pace target is reached (after at least one item was started), the stop-by time passes,
the 5-hour session or week limit trips, the STOP switch is on, the backlog is empty, or the user stops it."""
import re
from datetime import datetime, timedelta

import gate as G

KINDS = ('night', 'day')
MAX_ITEMS_LIMIT = 1000
PACE_LIMIT = 10.0
HHMM = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')
BLOCK_DEFAULT, BLOCK_MAX = 5, 20
PLAN_WAIT_MIN = 60     # the PLAN phase waits this long for the plan_todos job (the runner allows it 50 min), then carries on
PLAN_KIND = 'plan_todos'
REVIEW_KIND = 'review_batch'
REVIEW_DEFAULT, REVIEW_MAX = 10, 30
REVIEW_WAIT_MIN = 60   # the REVIEW phase waits this long for the review_batch job, then cancels it if still queued and carries on
FIELDS = ('enabled', 'start', 'stop_by', 'pace_target', 'max_items', 'block_size', 'review_batch_size')
DEFAULTS = {
    'night': dict(enabled=True, start='01:00', stop_by='05:30', pace_target=0.0, max_items=None, block_size=BLOCK_DEFAULT, review_batch_size=REVIEW_DEFAULT),
    'day': dict(enabled=False, start='10:00', stop_by='17:00', pace_target=-2.0, max_items=None, block_size=BLOCK_DEFAULT, review_batch_size=REVIEW_DEFAULT),
}


def parse_hhmm(text):
    m = HHMM.match(text.strip()) if isinstance(text, str) else None
    if not m:
        raise ValueError('time must be HH:MM (24 hours)')
    return int(m.group(1)) * 60 + int(m.group(2))


def clean_pace(v):
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ValueError('pace target must be a number')
    try:
        f = float(v)
    except ValueError:
        raise ValueError('pace target must be a number')
    if f != f or abs(f) > PACE_LIMIT:
        raise ValueError('pace target must be between -%g and %g' % (PACE_LIMIT, PACE_LIMIT))
    return round(f, 2)


def clean_max(v):
    """None / 'unlimited' / '' -> None (unlimited). Otherwise a whole number 1..MAX_ITEMS_LIMIT."""
    if v is None or (isinstance(v, str) and v.strip().lower() in ('', 'unlimited')):
        return None
    if isinstance(v, bool) or isinstance(v, float) and v != int(v):
        raise ValueError('number of items must be a whole number or unlimited')
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError('number of items must be a whole number or unlimited')
    if not 1 <= n <= MAX_ITEMS_LIMIT:
        raise ValueError('number of items must be from 1 to %d, or unlimited' % MAX_ITEMS_LIMIT)
    return n


def clean_block(v):
    """Block size: a whole number 1..BLOCK_MAX (None or '' -> the default)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return BLOCK_DEFAULT
    if isinstance(v, bool) or isinstance(v, float) and v != int(v):
        raise ValueError('block size must be a whole number')
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError('block size must be a whole number')
    if not 1 <= n <= BLOCK_MAX:
        raise ValueError('block size must be from 1 to %d' % BLOCK_MAX)
    return n


def clean_review(v):
    """Answers to check per run: a whole number 0..REVIEW_MAX (0 = skip the review phase; None or '' -> the default)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return REVIEW_DEFAULT
    if isinstance(v, bool) or isinstance(v, float) and v != int(v):
        raise ValueError('answers to check must be a whole number')
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError('answers to check must be a whole number')
    if not 0 <= n <= REVIEW_MAX:
        raise ValueError('answers to check must be from 0 to %d (0 = skip)' % REVIEW_MAX)
    return n


def clean_one(raw, base):
    """Merge a partial dict over a full one and validate. Raises ValueError with a plain message."""
    if not isinstance(raw, dict):
        raise ValueError('settings must be an object')
    extra = sorted(set(raw) - set(FIELDS))
    if extra:
        raise ValueError('unknown setting: %s' % ', '.join(str(x)[:30] for x in extra[:3]))
    out = dict(base)
    out.update(raw)
    if not isinstance(out['enabled'], bool):
        raise ValueError('enabled must be true or false')
    for k in ('start', 'stop_by'):
        parse_hhmm(out[k])
        out[k] = out[k].strip()
    if out['start'] == out['stop_by']:
        raise ValueError('start and stop-by must differ')
    out['pace_target'] = clean_pace(out['pace_target'])
    out['max_items'] = clean_max(out['max_items'])
    out['block_size'] = clean_block(out.get('block_size'))
    out['review_batch_size'] = clean_review(out.get('review_batch_size'))
    return {k: out[k] for k in FIELDS}


def clean_all(raw, base):
    """raw = {'night': {...}, 'day': {...}} (either may be missing) over base (a full valid dict) -> full valid dict."""
    if not isinstance(raw, dict):
        raise ValueError('settings must be an object')
    extra = sorted(set(raw) - set(KINDS))
    if extra:
        raise ValueError('unknown run: %s' % ', '.join(str(x)[:30] for x in extra[:3]))
    return {k: clean_one(raw[k], base[k]) if k in raw else dict(base[k]) for k in KINDS}


def defaults_from(opts, log=lambda *a: None):
    """Options -> full valid settings; a bad option falls back to the built-in default for that run."""
    out = {}
    for k in KINDS:
        raw = {f: opts[k + '_run_' + f] for f in FIELDS if (k + '_run_' + f) in opts}
        try:
            out[k] = clean_one(raw, DEFAULTS[k])
        except ValueError as e:
            log('%s run options ignored: %s' % (k, e))
            out[k] = dict(DEFAULTS[k])
    return out


def in_window(local_dt, start, stop_by):
    """-> (inside, window_date_text). The window may cross midnight; its date is the date it began."""
    s, e, m = parse_hhmm(start), parse_hhmm(stop_by), local_dt.hour * 60 + local_dt.minute
    if s < e:
        return s <= m < e, local_dt.date().isoformat()
    inside = m >= s or m < e
    day = local_dt.date() if m >= s else local_dt.date() - timedelta(days=1)
    return inside, day.isoformat()


def stop_by_at(local_dt, stop_by):
    """The next moment (aware, local) at or after local_dt when the clock reads stop_by."""
    e = parse_hhmm(stop_by)
    t = local_dt.replace(hour=e // 60, minute=e % 60, second=0, microsecond=0)
    return t if t > local_dt else t + timedelta(days=1)


def due_kind(settings, windows, local_dt):
    """Which scheduled run should start now? -> (kind, window_date) or (None, None). Night is checked first.
    windows = {kind: last window date started}; each window starts at most one run."""
    for k in KINDS:
        s = settings[k]
        if not s['enabled']:
            continue
        inside, wdate = in_window(local_dt, s['start'], s['stop_by'])
        if inside and windows.get(k) != wdate:
            return k, wdate
    return None, None


def new_run(kind, pace_target, max_items, now, stop_by=None, dry=False, ident=None, block_size=BLOCK_DEFAULT, review_batch_size=REVIEW_DEFAULT):
    return dict(id=ident or now.strftime('%Y%m%d%H%M%S'), kind=kind, started_at=now.isoformat(timespec='seconds'), pace_target=pace_target,
                max_items=max_items, stop_by_at=stop_by.isoformat(timespec='seconds') if stop_by else None, started=0, fed=0, gate_mode='idle',
                dry=bool(dry), block_size=block_size, review_batch_size=review_batch_size, phase='drain', block_no=0, block_ids=[], block_total=0, ids=[], plan_id=None, plan_at=None, review_id=None, review_at=None, short=False)


def effective_started(run):
    """Items the run has started. In test mode nothing is claimed, so an item the run would have queued counts as started."""
    return run['fed'] if run.get('dry') else run['started']


def gate(run, pace, session, week, stopped):
    """The run's own pace gate (reuses gate.step: same pace number, same session/week limits). -> (may_run, new_mode, reason)."""
    t = run['pace_target']
    if stopped:
        return False, run['gate_mode'], 'STOP switch is on: nothing runs'
    if pace is None or week is None:
        return False, run['gate_mode'], 'pace cannot be read, nothing runs'
    ok, mode, why = G.step(run['gate_mode'], pace, session, week, t, t, 'run')
    return ok, mode, why


def end_reason(run, now, pace, session, week, stopped):
    """Why the run is over, or '' to carry on."""
    if stopped:
        return 'STOP switch is on'
    n = run.get('max_items')
    if n is not None and effective_started(run) >= n:
        return 'item count reached (%d)' % n
    sb = run.get('stop_by_at')
    if sb and now >= datetime.fromisoformat(sb):
        return 'stop-by time reached'
    if session is not None and session >= G.SESSION_STOP:
        return '5-hour session limit (%d%%)' % G.SESSION_STOP
    if week is not None and week >= G.WEEK_STOP:
        return 'week limit (%d%%)' % G.WEEK_STOP
    if pace is not None and pace >= run['pace_target']:
        if run['kind'] == 'now' or effective_started(run) > 0:
            return 'pace target reached (%+.1f)' % pace
    return ''


def phase_text(run):
    """Plain words for the run's phase, e.g. 'block 2: 3 of 5 done'."""
    ph, b = run.get('phase', 'drain'), run.get('block_ids') or []
    if ph == 'drain':
        return 'waiting for the queue to be consumed' if run.get('block_no') else 'waiting for the queue to be empty'
    if ph == 'plan':
        return 'planning (free AIs write plans for to-dos without one)'
    if ph == 'review':
        return 'reviewing (Claude checks outside-AI answers)'
    return 'block %d: %d of %d %s' % (run.get('block_no', 0), run.get('block_done', 0), run.get('block_total', 0), 'would be fed' if run.get('dry') else 'done')
