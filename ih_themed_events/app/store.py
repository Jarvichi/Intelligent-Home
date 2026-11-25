"""ih_themed_events storage: the add-on's own SQLite file (/data/ih.db) through ihlib.db. Rules live in model.py.
Tables: events (one JSON record per event), checklist (tasks per event), reviews ("what worked / what to change" after a run), log (every change).
No secrets are stored. Use one long-lived thread per caller (ihlib keeps one connection per thread)."""
import json
import os
import time
from datetime import date, datetime, timezone

import model as M
from ihlib import db as ihdb

MIGRATIONS = [
    (1, 'CREATE TABLE events(slug TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)'),
    (2, '''CREATE TABLE checklist(id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL REFERENCES events(slug) ON DELETE CASCADE,
            text TEXT NOT NULL, days_before INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0, done_at TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL)'''),
    (3, 'CREATE INDEX checklist_slug ON checklist(slug, position)'),
    (4, '''CREATE TABLE reviews(id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL REFERENCES events(slug) ON DELETE CASCADE,
            occurrence TEXT NOT NULL, worked TEXT NOT NULL DEFAULT '', change TEXT NOT NULL DEFAULT '', at TEXT NOT NULL, UNIQUE(slug, occurrence))'''),
    (5, 'CREATE TABLE log(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL, slug TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL DEFAULT \'\')'),
    (6, 'CREATE TABLE installations(slug TEXT PRIMARY KEY REFERENCES events(slug) ON DELETE RESTRICT, status TEXT NOT NULL, journal TEXT NOT NULL)'),
]
DEFAULT_CHECKLIST = [{'text': 'Check helpers, scripts and automations are installed and the test run worked', 'days_before': 7},
                     {'text': 'Check the sounds are in the sounds folder', 'days_before': 7},
                     {'text': 'Switch the mode on for one minute and off again; confirm the lights come back', 'days_before': 3},
                     {'text': 'Fill in the review (what worked, what to change)', 'days_before': -1}]


class Store:
    def __init__(self, data_dir, clock=time.time, path=None):
        self.clock = clock
        self.path = path or os.path.join(data_dir, ihdb.DB_FILE_NAME)
        self.db = ihdb.open(self.path, MIGRATIONS, snapshot_dir=os.path.join(data_dir, 'backup'), clock=clock)

    def close(self):
        self.db.close()

    def now(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    def today(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).date()

    def _log(self, con, actor, slug, kind, text=''):
        con.execute('INSERT INTO log(at, actor, slug, kind, text) VALUES (?,?,?,?,?)', (self.now(), actor, slug, kind, text[:300]))

    # ---- events ----
    def _event(self, con, slug):
        r = con.execute('SELECT data FROM events WHERE slug = ?', (slug,)).fetchone()
        if not r:
            raise M.RuleError('missing', 'no event "%s"' % str(slug)[:20])
        return json.loads(r[0])

    def _view(self, con, ev):
        occ = M.next_occurrence(ev['when'], self.today())
        items = self._checklist(con, ev['slug'], occ)
        out = dict(ev)
        out['next'] = occ
        out['checklist'] = items
        out['open_items'] = sum(1 for i in items if not i['done'])
        out['review'] = self._review(con, ev['slug'], occ['start'] if occ else '')
        return out

    def _checklist(self, con, slug, occ):
        rows = con.execute('SELECT id, text, days_before, done, done_at FROM checklist WHERE slug = ? ORDER BY position, id', (slug,)).fetchall()
        return [{'id': r[0], 'text': r[1], 'days_before': r[2], 'done': bool(r[3]), 'done_at': r[4], 'due': M.checklist_due({'days_before': r[2]}, occ)} for r in rows]

    def _review(self, con, slug, occurrence):
        r = con.execute('SELECT worked, change, at FROM reviews WHERE slug = ? AND occurrence = ?', (slug, occurrence)).fetchone()
        return {'occurrence': occurrence, 'worked': r[0], 'change': r[1], 'at': r[2]} if r else {'occurrence': occurrence, 'worked': '', 'change': '', 'at': ''}

    def list(self):
        """Every event with its next date and open checklist count, soonest first (events without a date last)."""
        con = self.db.con
        out = [self._view(con, json.loads(r[0])) for r in con.execute('SELECT data FROM events')]
        out.sort(key=lambda e: (e['next'] is None, e['next']['start'] if e['next'] else '', e['slug']))
        for e in out:
            e.pop('checklist')       # the list view does not need every item
        return out

    def get(self, slug):
        con = self.db.con
        ev = self._view(con, self._event(con, slug))
        ev['log'] = [{'at': r[0], 'actor': r[1], 'kind': r[2], 'text': r[3]}
                     for r in con.execute('SELECT at, actor, kind, text FROM log WHERE slug = ? ORDER BY id DESC LIMIT 30', (slug,))]
        return ev

    def create(self, raw, actor='owner', checklist=None):
        ev = M.clean_event(raw)
        items = [M.clean_checklist_item(i) for i in (DEFAULT_CHECKLIST if checklist is None else checklist)]
        with self.db.tx() as con:
            if con.execute('SELECT 1 FROM events WHERE slug = ?', (ev['slug'],)).fetchone():
                raise M.RuleError('conflict', 'event "%s" already exists' % ev['slug'])
            con.execute('INSERT INTO events(slug, data, created_at, updated_at) VALUES (?,?,?,?)', (ev['slug'], json.dumps(ev), self.now(), self.now()))
            for n, i in enumerate(items):
                con.execute('INSERT INTO checklist(slug, text, days_before, position) VALUES (?,?,?,?)', (ev['slug'], i['text'], i['days_before'], n))
            self._log(con, actor, ev['slug'], 'created', ev['name'])
        return self.get(ev['slug'])

    def update(self, slug, raw, actor='owner'):
        """Replace the record (the slug stays). Send the whole record."""
        with self.db.tx() as con:
            row = con.execute('SELECT status FROM installations WHERE slug = ?', (slug,)).fetchone()
            if row and row[0] == 'installing':
                raise M.RuleError('conflict', 'Wait until installation finishes before editing this event.')
            self._event(con, slug)
            ev = M.clean_event(raw, slug)
            con.execute('UPDATE events SET data = ?, updated_at = ? WHERE slug = ?', (json.dumps(ev), self.now(), slug))
            self._log(con, actor, slug, 'edited')
        return self.get(slug)

    def delete(self, slug, actor='owner'):
        if self.installation(slug):
            raise M.RuleError('conflict', 'This event has an installation record and cannot be deleted here.')
        with self.db.tx() as con:
            self._event(con, slug)
            con.execute('DELETE FROM events WHERE slug = ?', (slug,))
            self._log(con, actor, slug, 'deleted')
        return {'deleted': slug}

    # ---- checklist and review ----
    def add_item(self, slug, raw, actor='owner'):
        i = M.clean_checklist_item(raw)
        with self.db.tx() as con:
            self._event(con, slug)
            pos = con.execute('SELECT COALESCE(MAX(position), -1) + 1 FROM checklist WHERE slug = ?', (slug,)).fetchone()[0]
            if pos >= 100:
                raise M.RuleError('bad', 'at most 100 checklist items')
            con.execute('INSERT INTO checklist(slug, text, days_before, position) VALUES (?,?,?,?)', (slug, i['text'], i['days_before'], pos))
            self._log(con, actor, slug, 'item_added', i['text'])
        return self.get(slug)

    def set_item(self, slug, item_id, done, actor='owner'):
        if not isinstance(done, bool):
            raise M.RuleError('bad', 'done must be true or false')
        with self.db.tx() as con:
            self._event(con, slug)
            n = con.execute('UPDATE checklist SET done = ?, done_at = ? WHERE id = ? AND slug = ?', (int(done), self.now() if done else '', item_id, slug)).rowcount
            if not n:
                raise M.RuleError('missing', 'no such checklist item')
            self._log(con, actor, slug, 'item_done' if done else 'item_reopened', str(item_id))
        return self.get(slug)

    def delete_item(self, slug, item_id, actor='owner'):
        with self.db.tx() as con:
            self._event(con, slug)
            if not con.execute('DELETE FROM checklist WHERE id = ? AND slug = ?', (item_id, slug)).rowcount:
                raise M.RuleError('missing', 'no such checklist item')
            self._log(con, actor, slug, 'item_removed', str(item_id))
        return self.get(slug)

    def save_review(self, slug, occurrence, worked, change, actor='owner'):
        """The 'what worked / what to change' note for one run of the event (occurrence = its start date)."""
        try:
            date.fromisoformat(occurrence)
        except (TypeError, ValueError):
            raise M.RuleError('bad', 'occurrence must be a date like 2026-12-31')
        worked, change = M._text(worked, 'worked'), M._text(change, 'change')
        with self.db.tx() as con:
            self._event(con, slug)
            con.execute('INSERT INTO reviews(slug, occurrence, worked, change, at) VALUES (?,?,?,?,?) '
                        'ON CONFLICT(slug, occurrence) DO UPDATE SET worked = excluded.worked, change = excluded.change, at = excluded.at',
                        (slug, occurrence, worked, change, self.now()))
            self._log(con, actor, slug, 'reviewed', occurrence)
        return self.get(slug)

    def reviews(self, slug):
        con = self.db.con
        self._event(con, slug)
        return [{'occurrence': r[0], 'worked': r[1], 'change': r[2], 'at': r[3]}
                for r in con.execute('SELECT occurrence, worked, change, at FROM reviews WHERE slug = ? ORDER BY occurrence DESC', (slug,))]

    def counts(self):
        return {'events': self.db.con.execute('SELECT COUNT(*) FROM events').fetchone()[0]}

    def record(self, slug):
        """The stored record without extras: the generator's input."""
        return self._event(self.db.con, slug)

    def installation(self, slug):
        row = self.db.con.execute('SELECT status, journal FROM installations WHERE slug = ?', (slug,)).fetchone()
        return {'status': row[0], 'journal': json.loads(row[1])} if row else None

    def save_installation(self, slug, status, journal):
        with self.db.tx() as con:
            con.execute('INSERT INTO installations(slug, status, journal) VALUES (?, ?, ?) ON CONFLICT(slug) DO UPDATE SET status=excluded.status, journal=excluded.journal',
                        (slug, status, json.dumps(journal)))
