"""ih_usage_trace storage: the add-on's own SQLite file (/data/ih.db) through ihlib.db.
Only numbers, labels and times are stored. Never message text. No secrets in the database. Stdlib only."""
import os
import time
from datetime import datetime, timezone

from ihlib import db as ihdb

MIGRATIONS = [
    (1, '''CREATE TABLE series(ts INTEGER PRIMARY KEY, session REAL, week REAL, pace REAL)'''),
    (2, '''CREATE TABLE events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, label TEXT NOT NULL, ref TEXT NOT NULL DEFAULT '',
            start INTEGER NOT NULL, "end" INTEGER)'''),
    (3, 'CREATE INDEX events_start ON events(start)'),
    (4, 'CREATE INDEX events_ref ON events(source, ref)'),
]
MAX_TEXT = 80
OPEN_CAP_S = 6 * 3600       # an event with no end is shown for at most this long
MAX_STEP_S = 15 * 60        # a gap between samples longer than this (add-on was down) is never flagged
MAX_EVENTS = 5000
STALE_S = 15 * 60           # the countdown says "no data" when the newest sample is older than this
PACE_PER_HOUR = 100 / 168   # an even spread of 100% over a 168 h week: the pace line rises this many points an hour with no use


class BadInput(ValueError):
    pass


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_time(v, default=None):
    """epoch seconds (number or digit string) or ISO 8601 text -> int epoch seconds."""
    if v in (None, ''):
        if default is None:
            raise BadInput('a time is needed')
        return int(default)
    if isinstance(v, bool):
        raise BadInput('bad time')
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if s.replace('.', '', 1).isdigit():
            return int(float(s))
        try:
            d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        except ValueError:
            raise BadInput('bad time: use ISO 8601 or epoch seconds')
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return int(d.timestamp())
    raise BadInput('bad time')


def clean_text(v, name, required=False):
    if v is None:
        v = ''
    if not isinstance(v, str):
        raise BadInput('%s must be text' % name)
    v = ' '.join(v.split())[:MAX_TEXT]
    if required and not v:
        raise BadInput('%s is needed' % name)
    return v


def number(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) < 1e6 else None


class UsageStore:
    def __init__(self, data_dir='/data', path=None, clock=time.time):
        self.clock = clock
        self.path = path or os.path.join(data_dir, ihdb.DB_FILE_NAME)
        self.db = ihdb.open(self.path, MIGRATIONS, snapshot_dir=os.path.join(data_dir, 'backup'), clock=clock)

    # ---- series
    def add_sample(self, session, week, pace, ts=None):
        """Store one reading; a reading with no number at all is skipped. -> True if stored."""
        s, w, p = number(session), number(week), number(pace)
        if s is None and w is None:
            return False
        self.db.execute('INSERT OR REPLACE INTO series(ts, session, week, pace) VALUES(?,?,?,?)', (int(ts or self.clock()), s, w, p))
        return True

    def series(self, since=0, until=None):
        until = until or 2 ** 40
        return [{'ts': r[0], 'session': r[1], 'week': r[2], 'pace': r[3]}
                for r in self.db.query('SELECT ts, session, week, pace FROM series WHERE ts >= ? AND ts <= ? ORDER BY ts', (since, until))]

    def latest(self):
        r = self.db.query('SELECT ts, session, week, pace FROM series ORDER BY ts DESC LIMIT 1')
        return {'ts': r[0][0], 'session': r[0][1], 'week': r[0][2], 'pace': r[0][3]} if r else None

    # ---- events
    def add_event(self, source, label, start=None, end=None, ref=''):
        """A start (no end) opens an event; a later post with the same source+ref and an end closes the newest open one.
        A post with start and end stores a finished event. -> the event."""
        now = int(self.clock())
        source = clean_text(source, 'source', True).lower()
        label = clean_text(label, 'label', True)
        ref = clean_text(ref, 'ref')
        s = parse_time(start, now)
        e = None if end in (None, '') else parse_time(end)
        if e is not None and e < s:
            raise BadInput('end is before start')
        with self.db.tx() as con:
            if e is not None and ref:
                row = con.execute('SELECT id FROM events WHERE source=? AND ref=? AND "end" IS NULL ORDER BY start DESC LIMIT 1', (source, ref)).fetchone()
                if row:
                    con.execute('UPDATE events SET "end"=?, label=? WHERE id=?', (max(e, s), label, row[0]))
                    return self._one(con, row[0])
            cur = con.execute('INSERT INTO events(source, label, ref, start, "end") VALUES(?,?,?,?,?)', (source, label, ref, s, e))
            return self._one(con, cur.lastrowid)

    def _one(self, con, eid):
        r = con.execute('SELECT id, source, label, ref, start, "end" FROM events WHERE id=?', (eid,)).fetchone()
        return self._row(r)

    def _row(self, r):
        now = int(self.clock())
        end = r[5]
        shown_end = end if end is not None else min(now, r[4] + OPEN_CAP_S)
        return {'id': r[0], 'source': r[1], 'label': r[2], 'ref': r[3], 'start': r[4], 'end': end, 'shown_end': shown_end,
                'start_iso': iso(r[4]), 'end_iso': iso(end) if end is not None else None, 'open': end is None and now - r[4] <= OPEN_CAP_S}

    def events(self, since=0, until=None, source=None):
        until = until or 2 ** 40
        sql = 'SELECT id, source, label, ref, start, "end" FROM events WHERE COALESCE("end", start + ?) >= ? AND start <= ?'
        args = [OPEN_CAP_S, since, until]
        if source:
            sql += ' AND source = ?'
            args.append(source)
        sql += ' ORDER BY start DESC LIMIT %d' % MAX_EVENTS
        return [self._row(r) for r in self.db.query(sql, args)]

    # ---- upkeep
    def purge(self, retention_days):
        cut = int(self.clock()) - int(retention_days) * 86400
        with self.db.tx() as con:
            a = con.execute('DELETE FROM series WHERE ts < ?', (cut,)).rowcount
            b = con.execute('DELETE FROM events WHERE COALESCE("end", start) < ?', (cut,)).rowcount
        return a, b

    def counts(self):
        return {'series': self.db.query('SELECT COUNT(*) FROM series')[0][0], 'events': self.db.query('SELECT COUNT(*) FROM events')[0][0]}


def find_jumps(samples, events, threshold, now=None):
    """Every rise of >= threshold points in one sample step (session or week) with no event overlapping that step.
    A step overlaps an event when event.start < step_end and event_end > step_start (an open event runs to `now`).
    Steps longer than MAX_STEP_S (the add-on was down) are skipped. -> list of {ts, from_ts, metric, rise, attributed_to}."""
    now = now if now is not None else time.time()
    spans = [(e['start'], e['end'] if e['end'] is not None else min(now, e['start'] + OPEN_CAP_S)) for e in events]
    out = []
    for a, b in zip(samples, samples[1:]):
        if b['ts'] - a['ts'] > MAX_STEP_S:
            continue
        for metric in ('session', 'week'):
            if a[metric] is None or b[metric] is None:
                continue
            rise = round(b[metric] - a[metric], 2)
            if rise >= threshold and not any(s < b['ts'] and e >= a['ts'] for s, e in spans):
                out.append({'ts': b['ts'], 'from_ts': a['ts'], 'metric': metric, 'rise': rise, 'ts_iso': iso(b['ts'])})
    return out


def countdown(sample, now):
    """Hours until the week pace goes under 0 if no more Claude is used. Same maths as sensor.claude_hours_until_under_pace
    (hours = max(pace, 0) / (100 / 168), 1 decimal). -> {state: counting|under|no_data, pace, hours_until_under, target_ts, target_iso,
    sample_ts, sample_iso, age_s, server_ts}. no_data = no sample, no pace number, or a sample older than STALE_S.
    pace <= 0 (at or under the target) is 'under' with 0 hours; target_ts is the sample time + the exact hours."""
    now = int(now)
    out = {'state': 'no_data', 'pace': None, 'hours_until_under': None, 'target_ts': None, 'target_iso': None,
           'sample_ts': None, 'sample_iso': None, 'age_s': None, 'server_ts': now}
    if not sample:
        return out
    ts = sample['ts']
    out.update(sample_ts=ts, sample_iso=iso(ts), age_s=max(0, now - ts))
    pace = number(sample.get('pace'))
    if pace is None or now - ts > STALE_S:
        return out
    exact = max(pace, 0) / PACE_PER_HOUR
    target = ts + exact * 3600
    out.update(pace=pace, hours_until_under=round(exact, 1), target_ts=int(round(target)), target_iso=iso(round(target)))
    out['state'] = 'under' if pace <= 0 or target <= now else 'counting'
    return out
