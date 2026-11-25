"""ih_tasks storage: the add-on's own SQLite file (/data/ih.db) through ihlib.db. Rules live in model.py; this file stores what they allow.
Every change writes one row to `events` in the same transaction, so the history of a task is complete.
No secrets are stored. Use one long-lived thread per caller (ihlib keeps one connection per thread)."""
import json
import os
import time
from datetime import datetime, timezone

import model as M
from ihlib import db as ihdb

MIGRATIONS = [
    (1, '''CREATE TABLE tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', plan TEXT NOT NULL DEFAULT '',
            stage TEXT NOT NULL, due TEXT NOT NULL DEFAULT '', verify_by TEXT NOT NULL DEFAULT 'check', revise INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '', closed_reason TEXT NOT NULL DEFAULT '', closed_detail TEXT NOT NULL DEFAULT '',
            duplicate_of INTEGER, created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)'''),
    (2, 'CREATE INDEX tasks_stage ON tasks(stage)'),
    (3, '''CREATE TABLE events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id), at TEXT NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL,
            from_stage TEXT NOT NULL DEFAULT '', to_stage TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '', data TEXT NOT NULL DEFAULT '')'''),
    (4, 'CREATE INDEX events_task ON events(task_id, id)'),
    (5, '''CREATE TABLE questions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id), status TEXT NOT NULL, asked_at TEXT NOT NULL, asked_by TEXT NOT NULL,
            questions TEXT NOT NULL, after TEXT NOT NULL, answers TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', answered_at TEXT NOT NULL DEFAULT '')'''),
    (6, "CREATE UNIQUE INDEX questions_one_open ON questions(task_id) WHERE status = 'open'"),
    (7, '''CREATE TABLE links(
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id), at TEXT NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '')'''),
    (8, 'CREATE TABLE legacy(kind TEXT NOT NULL, value TEXT NOT NULL, task_id INTEGER NOT NULL REFERENCES tasks(id), PRIMARY KEY(kind, value))'),
    (9, "CREATE TABLE mirror(task_id INTEGER PRIMARY KEY REFERENCES tasks(id), uid TEXT NOT NULL DEFAULT '', sent TEXT NOT NULL DEFAULT '')"),
    # pairs of tasks the owner said are NOT duplicates (a < b), so "Find duplicates" stops offering them
    (10, 'CREATE TABLE not_duplicates(a INTEGER NOT NULL REFERENCES tasks(id), b INTEGER NOT NULL REFERENCES tasks(id), at TEXT NOT NULL, PRIMARY KEY(a, b))'),
    (11, "ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'"),
    # optional free-text labels, comma separated (labels hold only letters, digits and dashes, so a comma never appears inside one)
    (12, "ALTER TABLE tasks ADD COLUMN labels TEXT NOT NULL DEFAULT ''"),
    # a child task points at its parent (one level). Children inherit the parent's approval; the parent cannot go to Verifying while one is open
    (13, 'ALTER TABLE tasks ADD COLUMN parent_id INTEGER REFERENCES tasks(id)'),
    (14, 'CREATE INDEX tasks_parent ON tasks(parent_id)'),
    # a task closed as 'Reminder set' comes back to To do on this date (YYYY-MM-DD)
    (15, "ALTER TABLE tasks ADD COLUMN remind_at TEXT NOT NULL DEFAULT ''"),
    # task-to-task links (0.8.0): src is blocked by / depends on / relates to dst. relates_to is stored once, with src < dst.
    (16, '''CREATE TABLE relations(
            id INTEGER PRIMARY KEY AUTOINCREMENT, src INTEGER NOT NULL REFERENCES tasks(id), dst INTEGER NOT NULL REFERENCES tasks(id),
            kind TEXT NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL, UNIQUE(src, dst, kind))'''),
    (17, 'CREATE INDEX relations_dst ON relations(dst)'),
]
RELATION_KINDS = ('blocked_by', 'depends_on', 'relates_to')
WAITS_ON = ('blocked_by', 'depends_on')      # both mean "src cannot finish before dst is done"
INVERSE = {'blocked_by': 'blocks', 'depends_on': 'needed_by', 'relates_to': 'relates_to'}
TASK_COLS = ('id', 'title', 'description', 'plan', 'stage', 'due', 'verify_by', 'revise', 'source', 'result', 'closed_reason', 'closed_detail',
             'duplicate_of', 'created_by', 'created_at', 'updated_at', 'priority', 'labels', 'parent_id', 'remind_at')
LIST_LIMIT = 500
IDENTITY_KINDS = ('todo_uid', 'queue_id')   # sending the same old item twice returns the task it already became; other old ids only label a task
COMMENT_MAX = 4000


def waiting_on(task):
    """Who has to act next: 'owner', 'worker', 'check' or '' (finished)."""
    s = task['stage']
    if s in ('done', 'closed'):
        return ''
    if task.get('needs_input'):
        return 'owner'
    if s in ('approved', 'implementing') and task.get('children_open'):
        return 'children'          # a parent waits for its child tasks; nothing to do until they are Done or Closed
    if s == 'planning':
        return 'worker' if task.get('revise') else 'owner'
    if s == 'verifying':
        return 'owner' if task.get('verify_by') == 'owner' else 'check'
    return 'worker'


class TaskStore:
    def __init__(self, data_dir, clock=time.time, path=None):
        self.clock = clock
        self.path = path or os.path.join(data_dir, ihdb.DB_FILE_NAME)
        self.db = ihdb.open(self.path, MIGRATIONS, snapshot_dir=os.path.join(data_dir, 'backup'), clock=clock)

    def close(self):
        self.db.close()

    def today(self):
        """Today's date in the add-on's own time zone (a reminder date is a calendar day)."""
        return datetime.fromtimestamp(self.clock()).date()

    def now(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    # ---- reading ----
    def _row(self, con, task_id):
        if isinstance(task_id, bool) or not isinstance(task_id, int):
            raise M.RuleError('bad', 'task number must be a whole number')
        r = con.execute('SELECT %s FROM tasks WHERE id = ?' % ', '.join(TASK_COLS), (task_id,)).fetchone()
        if not r:
            raise M.RuleError('missing', 'no task %d' % task_id)
        return dict(zip(TASK_COLS, r))

    def _open_question(self, con, task_id):
        r = con.execute("SELECT id, asked_at, asked_by, questions, after FROM questions WHERE task_id = ? AND status = 'open'", (task_id,)).fetchone()
        return None if not r else {'id': r[0], 'asked_at': r[1], 'asked_by': r[2], 'questions': json.loads(r[3]), 'after': r[4]}

    def _kids(self, con, ids=None):
        """{parent id: (open children, all children)}."""
        sql = "SELECT parent_id, SUM(CASE WHEN stage IN ('done','closed') THEN 0 ELSE 1 END), COUNT(*) FROM tasks WHERE parent_id IS NOT NULL"
        args = []
        if ids is not None:
            sql += ' AND parent_id IN (%s)' % ', '.join('?' * len(ids)); args = list(ids)
        return {r[0]: (r[1], r[2]) for r in con.execute(sql + ' GROUP BY parent_id', args)} if ids != [] else {}

    def _blockers(self, con, ids=None):
        """{task id: [numbers of the open tasks it is blocked by / depends on, lowest first]}."""
        sql = ("SELECT r.src, r.dst FROM relations r JOIN tasks o ON o.id = r.dst WHERE r.kind IN (?, ?) AND o.stage IN (%s)" % ', '.join('?' * len(M.OPEN_STAGES)))
        args = list(WAITS_ON) + list(M.OPEN_STAGES)
        if ids is not None:
            sql += ' AND r.src IN (%s)' % ', '.join('?' * len(ids)); args += list(ids)
        out = {}
        for src, dst in con.execute(sql + ' ORDER BY r.dst', args):
            out.setdefault(src, []).append(dst)
        return out

    def _view(self, con, t, open_ids=None, kids=None, blockers=None):
        t = dict(t)
        b = (blockers if blockers is not None else self._blockers(con, [t['id']])).get(t['id'], [])
        t['blocked'], t['blocked_by'] = bool(b), (b[0] if b else None)     # first open blocker, lowest number
        k = (kids if kids is not None else self._kids(con, [t['id']])).get(t['id'], (0, 0))
        t['children_open'], t['children_total'] = k
        t['revise'] = bool(t['revise'])
        t['labels'] = [x for x in (t.get('labels') or '').split(',') if x]
        t['needs_input'] = (t['id'] in open_ids) if open_ids is not None else self._open_question(con, t['id']) is not None
        t['stage_label'] = M.STAGE_LABELS[t['stage']]
        t['waiting_on'] = waiting_on(t)
        return t

    def get(self, task_id):
        """The whole task: fields, open question set, past question sets, links, old identifiers and the full history."""
        con = self.db.con
        t = self._view(con, self._row(con, task_id))
        t['question'] = self._open_question(con, task_id)
        t['past_questions'] = [{'id': r[0], 'status': r[1], 'asked_at': r[2], 'questions': json.loads(r[3]), 'after': r[4],
                                'answers': json.loads(r[5]) if r[5] else {}, 'note': r[6], 'answered_at': r[7]}
                               for r in con.execute("SELECT id, status, asked_at, questions, after, answers, note, answered_at FROM questions WHERE task_id = ? AND status != 'open' ORDER BY id", (task_id,))]
        t['links'] = [{'id': r[0], 'at': r[1], 'actor': r[2], 'kind': r[3], 'url': r[4], 'text': r[5]}
                      for r in con.execute('SELECT id, at, actor, kind, url, text FROM links WHERE task_id = ? ORDER BY id', (task_id,))]
        t['legacy'] = [{'kind': r[0], 'value': r[1]} for r in con.execute('SELECT kind, value FROM legacy WHERE task_id = ? ORDER BY kind, value', (task_id,))]
        t['relations'] = self.relations(task_id)
        t['blocked'] = any(r['waiting'] for r in t['relations'])
        t['history'] = self.history(task_id)
        t['comments'] = [e for e in t['history'] if e['kind'] == 'comment']
        t['children'] = [{'id': r[0], 'title': r[1], 'stage': r[2], 'stage_label': M.STAGE_LABELS[r[2]]}
                         for r in con.execute('SELECT id, title, stage FROM tasks WHERE parent_id = ? ORDER BY id', (task_id,))]
        pr = con.execute('SELECT id, title, stage FROM tasks WHERE id = ?', (t['parent_id'],)).fetchone() if t.get('parent_id') else None
        t['parent'] = {'id': pr[0], 'title': pr[1], 'stage': pr[2], 'stage_label': M.STAGE_LABELS[pr[2]]} if pr else None
        return t

    def history(self, task_id):
        con = self.db.con
        self._row(con, task_id)
        return [{'id': r[0], 'at': r[1], 'actor': r[2], 'kind': r[3], 'from': r[4], 'to': r[5], 'text': r[6], 'data': json.loads(r[7]) if r[7] else {}}
                for r in con.execute('SELECT id, at, actor, kind, from_stage, to_stage, text, data FROM events WHERE task_id = ? ORDER BY id', (task_id,))]

    def list(self, stages=None, needs_input=None, waiting=None, q='', limit=LIST_LIMIT):
        """Task summaries (no plan or description text), oldest number first. Filters: stages (list), needs_input (bool), waiting (owner|worker|check), q (text in the title or the number)."""
        con = self.db.con
        for s in stages or ():
            if s not in M.STAGES:
                raise M.RuleError('bad', 'stage must be from: ' + ', '.join(M.STAGES))
        if waiting not in (None, '', 'owner', 'worker', 'check', 'children'):
            raise M.RuleError('bad', 'waiting must be owner, worker, check or children')
        sql, args = 'SELECT %s FROM tasks' % ', '.join(TASK_COLS), []
        if stages:
            sql += ' WHERE stage IN (%s)' % ', '.join('?' * len(stages))
            args += list(stages)
        open_ids = {r[0] for r in con.execute("SELECT task_id FROM questions WHERE status = 'open'")}
        kids = self._kids(con)
        blockers = self._blockers(con)
        out, needle = [], (q or '').strip().lower().lstrip('#')
        for r in con.execute(sql + ' ORDER BY id', args):
            t = self._view(con, dict(zip(TASK_COLS, r)), open_ids, kids, blockers)
            if needs_input is not None and t['needs_input'] != bool(needs_input):
                continue
            if waiting and t['waiting_on'] != waiting:
                continue
            if needle and needle not in t['title'].lower() and needle != str(t['id']):
                continue
            t['has_plan'] = bool(t.pop('plan'))
            t.pop('description')
            out.append(t)
        return out[:max(1, min(int(limit), LIST_LIMIT))]

    def board(self):
        """{columns: [{stage, label, count, tasks}], needs_input, total} for the Kanban page."""
        items = self.list()
        return {'columns': [{'stage': s, 'label': M.STAGE_LABELS[s], 'tasks': [t for t in items if t['stage'] == s]} for s in M.STAGES],
                'needs_input': sum(1 for t in items if t['needs_input']), 'total': len(items)}

    def labels(self):
        """Labels in use on any task -> {labels: [{label, count}]}, most used first. For the page's suggestions and filter."""
        n = {}
        for (raw,) in self.db.con.execute("SELECT labels FROM tasks WHERE labels != ''"):
            for x in raw.split(','):
                if x:
                    n[x] = n.get(x, 0) + 1
        return {'labels': [{'label': k, 'count': v} for k, v in sorted(n.items(), key=lambda kv: (-kv[1], kv[0]))]}

    def by_legacy(self, kind, value):
        r = self.db.con.execute('SELECT task_id FROM legacy WHERE kind = ? AND value = ?', (kind, value)).fetchone()
        return self.get(r[0]) if r else None

    def counts(self):
        c = {s: 0 for s in M.STAGES}
        for stage, n in self.db.con.execute('SELECT stage, COUNT(*) FROM tasks GROUP BY stage'):
            c[stage] = n
        return c

    # ---- writing (each method is one transaction) ----
    def _event(self, con, task_id, actor, kind, frm='', to='', text='', data=None):
        at = self.now()
        con.execute('INSERT INTO events(task_id, at, actor, kind, from_stage, to_stage, text, data) VALUES(?,?,?,?,?,?,?,?)',
                    (task_id, at, actor, kind, frm, to, text, json.dumps(data, ensure_ascii=False) if data else ''))
        con.execute('UPDATE tasks SET updated_at = ? WHERE id = ?', (at, task_id))

    def _add_legacy(self, con, task_id, legacy):
        for x in legacy:
            r = con.execute('SELECT task_id FROM legacy WHERE kind = ? AND value = ?', (x['kind'], x['value'])).fetchone()
            if r and r[0] != task_id:
                raise M.RuleError('conflict', 'old identifier %s already belongs to task %d' % (x['kind'], r[0]))
            con.execute('INSERT OR IGNORE INTO legacy(kind, value, task_id) VALUES(?,?,?)', (x['kind'], x['value'], task_id))

    def _insert(self, con, title, description, plan, stage, due, verify_by, source, actor, priority='normal', labels=(), parent_id=None):
        at = self.now()
        cur = con.execute('INSERT INTO tasks(title, description, plan, stage, due, verify_by, source, created_by, created_at, updated_at, priority, labels, parent_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (title, description, plan, stage, due, verify_by, source, actor, at, at, priority, ','.join(labels), parent_id))
        return cur.lastrowid

    def _close(self, con, t, actor, c):
        if c['duplicate_of'] is not None:
            self._row(con, c['duplicate_of'])
        con.execute('UPDATE tasks SET stage = ?, closed_reason = ?, closed_detail = ?, duplicate_of = ? WHERE id = ?',
                    ('closed', c['reason_kind'], c['detail'], c['duplicate_of'], t['id']))
        open_q = self._open_question(con, t['id'])
        if open_q:
            con.execute("UPDATE questions SET status = 'withdrawn', answered_at = ? WHERE id = ?", (self.now(), open_q['id']))
        self._event(con, t['id'], actor, 'closed', t['stage'], 'closed', c['detail'], {'reason': c['reason_kind'], 'duplicate_of': c['duplicate_of']})

    def create(self, title, actor, description='', due='', source='', legacy=None, allow_duplicate=False, priority='', labels=None):
        """New task in To do. If an OPEN task has nearly the same title, the new task is still stored (so nothing is lost) but closed at once as a
        duplicate of it, with a link in its history. -> {task, duplicate_of, similar, existing}. A task that already owns the same old to-do uid or queue id is returned as-is (existing True)."""
        M.check_actor(actor)
        title = M.clean_text(title, M.TITLE_MAX, 'title', required=True)
        description = M.clean_text(description, M.TEXT_MAX, 'description')
        source = M.clean_text(source, 200, 'source')
        due = M.check_due(due)
        priority = M.check_priority(priority)
        labels = M.check_labels(labels)
        legacy = M.check_legacy(legacy)
        with self.db.tx() as con:
            for x in legacy:
                r = con.execute('SELECT task_id FROM legacy WHERE kind = ? AND value = ?', (x['kind'], x['value'])).fetchone() if x['kind'] in IDENTITY_KINDS else None
                if r:
                    return {'task': self.get(r[0]), 'duplicate_of': None, 'similar': [], 'existing': True}
            hits = [] if allow_duplicate else M.find_duplicates(title, con.execute(
                'SELECT id, title FROM tasks WHERE parent_id IS NULL AND stage IN (%s)' % ', '.join('?' * len(M.OPEN_STAGES)), M.OPEN_STAGES).fetchall())
            tid = self._insert(con, title, description, '', 'todo', due, 'check', source, actor, priority, labels)
            self._add_legacy(con, tid, legacy)
            self._event(con, tid, actor, 'created', '', 'todo', '', {'source': source} if source else None)
            dup = None
            if hits:
                dup = hits[0][0]
                c = {'reason_kind': 'duplicate', 'detail': 'Same as task %d (title match %d%%). Closed when it was created.' % (dup, round(hits[0][2] * 100)), 'duplicate_of': dup}
                self._close(con, {'id': tid, 'stage': 'todo'}, 'system', c)
                self._event(con, dup, 'system', 'duplicate_linked', text='Task %d was created with nearly the same title and closed as a duplicate of this one.' % tid, data={'task': tid})
        return {'task': self.get(tid), 'duplicate_of': dup, 'similar': [{'id': h[0], 'title': h[1], 'score': h[2]} for h in hits], 'existing': False}

    def transition(self, task_id, to, actor, reason='', plan='', result='', verify_by=None):
        with self.db.tx() as con:
            t = self._row(con, task_id)
            c = M.check_transition(t, to, actor, reason, plan, result, self._open_question(con, task_id) is not None)
            if to in ('verifying', 'done'):
                self._need_children_finished(con, t)
            if verify_by is not None:
                if verify_by not in M.VERIFY_BY:
                    raise M.RuleError('bad', 'verify_by must be check or owner')
                if actor not in ('worker', 'owner') or to not in ('planning', 'verifying'):
                    raise M.RuleError('bad', 'verify_by is set by the worker with the plan or when the work goes to Verifying')
            sets, args = ['stage = ?'], [to]
            if c['plan']:
                if to != 'planning':
                    raise M.RuleError('bad', 'a plan is only sent with a move to Planning')
                sets.append('plan = ?'); args.append(c['plan'])
            if to == 'planning':
                # back in Planning after approval: the plan must be looked at again, so flag it for the worker unless a new plan came with the move
                sets.append('revise = ?'); args.append(0 if (t['stage'] == 'todo' or c['plan']) else 1)
            if c['result']:
                sets.append('result = ?'); args.append(c['result'])
            if verify_by is not None:
                sets.append('verify_by = ?'); args.append(verify_by)
            if to == 'todo':
                sets += ["closed_reason = ''", "closed_detail = ''", 'duplicate_of = NULL', 'revise = 0', "remind_at = ''"]
            con.execute('UPDATE tasks SET %s WHERE id = ?' % ', '.join(sets), args + [task_id])
            data = {k: v for k, v in (('result', c['result']), ('verify_by', verify_by), ('plan_sent', bool(c['plan']))) if v}
            self._event(con, task_id, actor, 'moved', t['stage'], to, c['reason'], data or None)
            if to == 'done':
                self._child_finished(con, t)
        return self.get(task_id)

    def set_plan(self, task_id, actor, plan, verify_by=None):
        """A worker rewrites the plan while the task is in Planning (after a rejection or an answer). Clears the 'revise' flag. An approved plan cannot be changed: move the task back to Planning first."""
        M.check_actor(actor)
        if actor != 'worker':
            raise M.RuleError('forbidden', 'only a worker writes the plan')
        plan = M.clean_text(plan, M.TEXT_MAX, 'plan', required=True)
        if verify_by is not None and verify_by not in M.VERIFY_BY:
            raise M.RuleError('bad', 'verify_by must be check or owner')
        with self.db.tx() as con:
            t = self._row(con, task_id)
            if t['stage'] != 'planning':
                raise M.RuleError('conflict', 'the plan can only be changed in Planning (the task is in %s)' % M.STAGE_LABELS[t['stage']])
            con.execute('UPDATE tasks SET plan = ?, revise = 0, verify_by = COALESCE(?, verify_by) WHERE id = ?', (plan, verify_by, task_id))
            self._event(con, task_id, actor, 'plan', text='Plan rewritten.')
        return self.get(task_id)

    def reject(self, task_id, actor, comment):
        """The owner sends a plan back: the task stays in Planning, flagged for the worker to revise, with the owner's comment in the history."""
        M.check_actor(actor)
        if actor != 'owner':
            raise M.RuleError('forbidden', 'only the owner rejects a plan')
        comment = M.clean_text(comment, COMMENT_MAX, 'comment', required=True)
        with self.db.tx() as con:
            t = self._row(con, task_id)
            if t['stage'] != 'planning':
                raise M.RuleError('conflict', 'only a task in Planning can be rejected (to stop another task, close it with a reason)')
            con.execute('UPDATE tasks SET revise = 1 WHERE id = ?', (task_id,))
            self._event(con, task_id, actor, 'rejected', 'planning', 'planning', comment)
        return self.get(task_id)

    def close_task(self, task_id, actor, reason_kind, detail='', duplicate_of=None):
        with self.db.tx() as con:
            t = self._row(con, task_id)
            self._close(con, t, actor, M.check_close(t, actor, reason_kind, detail, duplicate_of))
            self._child_finished(con, t)
        return self.get(task_id)

    # ---- reminders: a task closed as 'Reminder set' reopens in To do (high priority) on its date
    def set_reminder(self, task_id, actor, date, note=''):
        """Set a reminder for `date` and close the task with the reason 'Reminder set'. The task comes back to To do on that date (see due_reminders / fire_reminder)."""
        M.check_actor(actor)
        if actor not in ('owner', 'worker'):
            raise M.RuleError('forbidden', 'only the owner or a worker can set a reminder')
        note = M.clean_text(note, M.NOTE_MAX, 'note')
        date = M.check_remind_date(date, self.today())
        with self.db.tx() as con:
            t = self._row(con, task_id)
            if t['stage'] not in M.OPEN_STAGES:
                raise M.RuleError('conflict', 'the task is %s; only an open task can get a reminder' % M.STAGE_LABELS[t['stage']])
            self._need_children_finished(con, t)
            self._close(con, t, actor, {'reason_kind': 'reminder', 'detail': ('Reminder on %s.' % date) + (' ' + note if note else ''), 'duplicate_of': None})
            con.execute('UPDATE tasks SET remind_at = ? WHERE id = ?', (date, task_id))
            self._event(con, task_id, actor, 'reminder_set', text=note, data={'date': date, 'note': note, 'was': t['stage']})
            self._child_finished(con, t)
        return self.get(task_id)

    def due_reminders(self, today=None):
        """Closed 'Reminder set' tasks whose date has come: [{id, title, remind_at, note, set_at}] (oldest date first)."""
        today = (today or self.today()).isoformat()
        con = self.db.con
        out = []
        for r in con.execute("SELECT id, title, remind_at FROM tasks WHERE stage = 'closed' AND closed_reason = 'reminder' AND remind_at != '' AND remind_at <= ? ORDER BY remind_at, id", (today,)):
            e = con.execute("SELECT at, data FROM events WHERE task_id = ? AND kind = 'reminder_set' ORDER BY id DESC LIMIT 1", (r[0],)).fetchone()
            data = json.loads(e[1]) if e and e[1] else {}
            out.append({'id': r[0], 'title': r[1], 'remind_at': r[2], 'note': data.get('note', ''), 'set_at': e[0] if e else ''})
        return out

    def fire_reminder(self, task_id):
        """The date has come: reopen the task in To do with high priority and say so in its history."""
        with self.db.tx() as con:
            t = self._row(con, task_id)
            if t['stage'] != 'closed' or t['closed_reason'] != 'reminder':
                raise M.RuleError('conflict', 'task %d has no reminder waiting' % task_id)
            con.execute("UPDATE tasks SET stage = 'todo', closed_reason = '', closed_detail = '', duplicate_of = NULL, revise = 0, remind_at = '', priority = 'high' WHERE id = ?", (task_id,))
            self._event(con, task_id, 'system', 'reminder_fired', 'closed', 'todo', 'The reminder date (%s) came: the task is back in To do, high priority.' % t['remind_at'])
        return self.get(task_id)

    def reminders(self):
        """The waiting reminders: [{id, title, remind_at}] soonest first."""
        return [{'id': r[0], 'title': r[1], 'remind_at': r[2]} for r in self.db.con.execute(
            "SELECT id, title, remind_at FROM tasks WHERE stage = 'closed' AND closed_reason = 'reminder' AND remind_at != '' ORDER BY remind_at, id")]

    # ---- child tasks (a parent's approval covers its children; the parent cannot go to Verifying while one is open)
    def _need_children_finished(self, con, t):
        open_kids = [r[0] for r in con.execute("SELECT id FROM tasks WHERE parent_id = ? AND stage NOT IN ('done','closed') ORDER BY id", (t['id'],))]
        if open_kids:
            raise M.RuleError('conflict', '%d child task(s) are still open (%s): the parent can go to Verifying when all of them are Done or Closed'
                              % (len(open_kids), ', '.join('#%d' % i for i in open_kids[:6])))

    def _child_finished(self, con, t):
        """A child reached Done or Closed: when it was the last open one, say so on the parent (the worker then picks the parent up again)."""
        pid = t.get('parent_id')
        if not pid:
            return
        left = con.execute("SELECT COUNT(*) FROM tasks WHERE parent_id = ? AND stage NOT IN ('done','closed')", (pid,)).fetchone()[0]
        if left == 0:
            self._event(con, pid, 'system', 'children_finished', text='All child tasks are Done or Closed; the parent can go to Verifying.')

    def create_child(self, parent_id, title, actor, description=''):
        """A child task under an Approved or Implementing parent, created straight in Approved (it inherits the parent's approval), with the
        parent's priority, due date, labels and verify_by. A parent still in Approved moves to Implementing. -> the child task."""
        M.check_actor(actor)
        title = M.clean_text(title, M.TITLE_MAX, 'title', required=True)
        description = M.clean_text(description, M.TEXT_MAX, 'description')
        with self.db.tx() as con:
            p = self._row(con, parent_id)
            M.check_child(p, actor)
            n = con.execute('SELECT COUNT(*) FROM tasks WHERE parent_id = ?', (parent_id,)).fetchone()[0]
            if n >= M.MAX_CHILDREN:
                raise M.RuleError('conflict', 'a task can have at most %d child tasks' % M.MAX_CHILDREN)
            plan = ('Part of task #%d (%s), approved with it. ' % (parent_id, p['title'][:80])) + (description or title)
            cid = self._insert(con, title, description, plan[:M.TEXT_MAX], 'approved', p['due'], p['verify_by'], p['source'], actor, p['priority'],
                               [x for x in (p.get('labels') or '').split(',') if x], parent_id)
            self._event(con, cid, actor, 'created', '', 'approved', 'Child of task %d; approved with it.' % parent_id, {'parent': parent_id})
            self._event(con, parent_id, actor, 'child_added', text='Child task #%d: %s' % (cid, title[:120]), data={'child': cid})
            if p['stage'] == 'approved':
                con.execute("UPDATE tasks SET stage = 'implementing' WHERE id = ?", (parent_id,))
                self._event(con, parent_id, 'system', 'moved', 'approved', 'implementing', 'Child tasks were added.')
        return self.get(cid)

    def comment(self, task_id, actor, text):
        """A note on the task. It never creates another task and never changes the stage."""
        M.check_actor(actor)
        text = M.clean_text(text, COMMENT_MAX, 'comment', required=True)
        with self.db.tx() as con:
            self._row(con, task_id)
            self._event(con, task_id, actor, 'comment', text=text)
        return self.get(task_id)

    def edit(self, task_id, actor, title=None, description=None, due=None, priority=None, labels=None):
        M.check_actor(actor)
        if actor not in ('owner', 'worker'):
            raise M.RuleError('forbidden', 'only the owner or a worker edits a task')
        sets, args, changed = [], [], []
        if title is not None:
            sets.append('title = ?'); args.append(M.clean_text(title, M.TITLE_MAX, 'title', required=True)); changed.append('title')
        if description is not None:
            sets.append('description = ?'); args.append(M.clean_text(description, M.TEXT_MAX, 'description')); changed.append('description')
        if due is not None:
            sets.append('due = ?'); args.append(M.check_due(due)); changed.append('due')
        if priority is not None:
            sets.append('priority = ?'); args.append(M.check_priority(priority)); changed.append('priority')
        if labels is not None:
            sets.append('labels = ?'); args.append(','.join(M.check_labels(labels))); changed.append('labels')
        if not sets:
            raise M.RuleError('bad', 'nothing to change')
        with self.db.tx() as con:
            self._row(con, task_id)
            con.execute('UPDATE tasks SET %s WHERE id = ?' % ', '.join(sets), args + [task_id])
            self._event(con, task_id, actor, 'edited', text='Changed: ' + ', '.join(changed))
        return self.get(task_id)

    def ask(self, task_id, actor, questions, after):
        """Open THE question set of a task (at most one open). `after` says in plain words what happens once it is answered."""
        M.check_actor(actor)
        if actor not in ('worker', 'system'):
            raise M.RuleError('forbidden', 'questions come from a worker')
        qs = M.clean_questions(questions)
        after = M.clean_text(after, 1000, 'after (what happens after the answer)', required=True)
        with self.db.tx() as con:
            t = self._row(con, task_id)
            if t['stage'] in ('done', 'closed'):
                raise M.RuleError('conflict', 'the task is finished')
            if self._open_question(con, task_id):
                raise M.RuleError('conflict', 'this task already has open questions; wait for the answer or withdraw them')
            con.execute("INSERT INTO questions(task_id, status, asked_at, asked_by, questions, after) VALUES(?, 'open', ?, ?, ?, ?)",
                        (task_id, self.now(), actor, json.dumps(qs, ensure_ascii=False), after))
            self._event(con, task_id, actor, 'asked', text=after, data={'questions': len(qs)})
        return self.get(task_id)

    def withdraw_question(self, task_id, actor, reason):
        M.check_actor(actor)
        if actor not in ('worker', 'owner'):
            raise M.RuleError('forbidden', 'only the owner or a worker withdraws questions')
        reason = M.clean_text(reason, M.REASON_MAX, 'reason', required=True)
        with self.db.tx() as con:
            self._row(con, task_id)
            q = self._open_question(con, task_id)
            if not q:
                raise M.RuleError('conflict', 'this task has no open questions')
            con.execute("UPDATE questions SET status = 'withdrawn', note = ?, answered_at = ? WHERE id = ?", (reason, self.now(), q['id']))
            self._event(con, task_id, actor, 'question_withdrawn', text=reason)
        return self.get(task_id)

    def answer(self, task_id, actor, answers, note=''):
        """The owner answers the open question set. The SAME task carries on (same stage); in Planning it is flagged for the worker to update the plan."""
        M.check_actor(actor)
        if actor != 'owner':
            raise M.RuleError('forbidden', 'only the owner answers')
        note = M.clean_text(note, COMMENT_MAX, 'note')
        with self.db.tx() as con:
            t = self._row(con, task_id)
            q = self._open_question(con, task_id)
            if not q:
                raise M.RuleError('conflict', 'this task has no open questions')
            clean = M.clean_answers(q['questions'], answers)
            con.execute("UPDATE questions SET status = 'answered', answers = ?, note = ?, answered_at = ? WHERE id = ?",
                        (json.dumps(clean, ensure_ascii=False), note, self.now(), q['id']))
            if t['stage'] == 'planning':
                con.execute('UPDATE tasks SET revise = 1 WHERE id = ?', (task_id,))
            self._event(con, task_id, actor, 'answered', text=note, data={'answers': clean})
        return self.get(task_id)

    def add_link(self, task_id, actor, kind, url='', text=''):
        """Attach a result or a pull request link."""
        M.check_actor(actor)
        if kind not in M.LINK_KINDS:
            raise M.RuleError('bad', 'kind must be one of: ' + ', '.join(M.LINK_KINDS))
        url = M.clean_text(url, 500, 'url')
        text = M.clean_text(text, COMMENT_MAX, 'text')
        if url and not (url.startswith('https://') or url.startswith('http://')):
            raise M.RuleError('bad', 'url must start with http:// or https://')
        if not url and not text:
            raise M.RuleError('bad', 'give a url or a text')
        with self.db.tx() as con:
            self._row(con, task_id)
            con.execute('INSERT INTO links(task_id, at, actor, kind, url, text) VALUES(?,?,?,?,?,?)', (task_id, self.now(), actor, kind, url, text))
            self._event(con, task_id, actor, 'link', text=text, data={'kind': kind, 'url': url})
        return self.get(task_id)

    # ---- links between tasks ----
    def relations(self, task_id):
        """Links of one task seen from that task: [{kind, task, title, stage, open, waiting}]. kind is blocked_by, depends_on, relates_to,
        or the inverse seen from the other end: blocks (that task is blocked by this one), needed_by (that task depends on this one).
        `waiting` is true for a blocked_by / depends_on link whose other task is still open: this task cannot be finished yet."""
        out = []
        for src, dst, kind, mine in self.db.con.execute(
                'SELECT r.src, r.dst, r.kind, CASE WHEN r.src = ? THEN 1 ELSE 0 END FROM relations r WHERE r.src = ? OR r.dst = ? ORDER BY r.id', (task_id,) * 3).fetchall():
            other = dst if mine else src
            title, stage = self.db.con.execute('SELECT title, stage FROM tasks WHERE id = ?', (other,)).fetchone()
            is_open = stage in M.OPEN_STAGES
            out.append({'kind': kind if (mine or kind == 'relates_to') else INVERSE[kind], 'task': other, 'title': title, 'stage': stage,
                        'open': is_open, 'waiting': bool(mine and kind in WAITS_ON and is_open)})
        return out

    def _waits_on_path(self, con, start, goal):
        """True when `start` already waits (directly or through other tasks) on `goal`."""
        seen, todo = set(), [start]
        while todo:
            n = todo.pop()
            if n == goal:
                return True
            if n in seen:
                continue
            seen.add(n)
            todo += [r[0] for r in con.execute('SELECT dst FROM relations WHERE src = ? AND kind IN (?, ?)', (n,) + WAITS_ON)]
        return False

    def relate(self, task_id, actor, kind, other):
        """Link task_id to another task: blocked_by, depends_on or relates_to (symmetric). Refused: itself, a missing task, a loop of
        blocked_by / depends_on links (A waits on B waits on A), the same link twice. A history line goes on BOTH tasks."""
        M.check_actor(actor)
        if kind not in RELATION_KINDS:
            raise M.RuleError('bad', 'kind must be one of: ' + ', '.join(RELATION_KINDS))
        if isinstance(other, bool) or not isinstance(other, int):
            raise M.RuleError('bad', 'the other task must be a task number')
        if other == task_id:
            raise M.RuleError('bad', 'a task cannot be linked to itself')
        with self.db.tx() as con:
            self._row(con, task_id)
            self._row(con, other)
            src, dst = (min(task_id, other), max(task_id, other)) if kind == 'relates_to' else (task_id, other)
            if con.execute('SELECT 1 FROM relations WHERE src = ? AND dst = ? AND kind = ?', (src, dst, kind)).fetchone():
                raise M.RuleError('conflict', 'that link already exists')
            if kind in WAITS_ON and self._waits_on_path(con, other, task_id):
                raise M.RuleError('conflict', 'that would make a loop: task %d already waits on task %d' % (other, task_id))
            con.execute('INSERT INTO relations(src, dst, kind, at, actor) VALUES(?,?,?,?,?)', (src, dst, kind, self.now(), actor))
            self._event(con, task_id, actor, 'related', text='%s task %d' % (kind.replace('_', ' '), other), data={'kind': kind, 'task': other})
            self._event(con, other, actor, 'related', text='task %d %s this task' % (task_id, INVERSE[kind].replace('_', ' ') if kind != 'relates_to' else 'relates to'),
                        data={'kind': INVERSE[kind], 'task': task_id})
        return self.get(task_id)

    def unrelate(self, task_id, actor, kind, other):
        """Remove a link made with relate (give the same kind and the same two tasks, either way round for relates_to)."""
        M.check_actor(actor)
        if kind not in RELATION_KINDS:
            raise M.RuleError('bad', 'kind must be one of: ' + ', '.join(RELATION_KINDS))
        if isinstance(other, bool) or not isinstance(other, int):
            raise M.RuleError('bad', 'the other task must be a task number')
        with self.db.tx() as con:
            self._row(con, task_id)
            self._row(con, other)
            src, dst = (min(task_id, other), max(task_id, other)) if kind == 'relates_to' else (task_id, other)
            if not con.execute('DELETE FROM relations WHERE src = ? AND dst = ? AND kind = ?', (src, dst, kind)).rowcount:
                raise M.RuleError('missing', 'no such link')
            self._event(con, task_id, actor, 'unrelated', text='removed: %s task %d' % (kind.replace('_', ' '), other), data={'kind': kind, 'task': other})
            self._event(con, other, actor, 'unrelated', text='removed: link with task %d' % task_id, data={'kind': INVERSE[kind], 'task': task_id})
        return self.get(task_id)

    def add_legacy(self, task_id, actor, legacy):
        M.check_actor(actor)
        legacy = M.check_legacy(legacy)
        with self.db.tx() as con:
            self._row(con, task_id)
            self._add_legacy(con, task_id, legacy)
        return self.get(task_id)

    # ---- merge and likely duplicates ----
    def _ids(self, ids, name, least):
        if not isinstance(ids, list) or not least <= len(ids) <= M.MERGE_MAX or any(isinstance(x, bool) or not isinstance(x, int) for x in ids):
            raise M.RuleError('bad', '%s must be a list of %d to %d task numbers' % (name, least, M.MERGE_MAX))
        if len(set(ids)) != len(ids):
            raise M.RuleError('bad', '%s names a task twice' % name)
        return ids

    def merge(self, keep, merge, actor):
        """Merge tasks `merge` INTO task `keep`, in ONE transaction (all of it or none of it). For each merged task:
        its comments are copied to the keeper as "From #N: ..."; its open question set moves to the keeper when the keeper has none
        (otherwise the question text becomes a comment); its links are copied; its old ids move, so they now find the keeper;
        the keeper takes the earliest due date, the highest priority and the labels of all of them; then the task is closed as a duplicate of the keeper. Both histories get a line.
        The plan and description of a merged task stay on that (closed) task. -> {task: keeper, merged: [ids]}."""
        self._ids(merge, 'merge', 1)
        with self.db.tx() as con:
            k = self._row(con, keep)
            others = [self._row(con, i) for i in merge]
            for t in [k] + others:
                if t.get('parent_id') or con.execute('SELECT 1 FROM tasks WHERE parent_id = ? LIMIT 1', (t['id'],)).fetchone():
                    raise M.RuleError('conflict', 'task %d is part of a parent/child group; those tasks are not merged' % t['id'])
            M.check_merge(k, others, actor)
            due = min([t['due'] for t in [k] + others if t['due']] or [''])
            prio = min([t['priority'] for t in [k] + others], key=M.PRIORITIES.index)   # the most urgent one
            labels = []     # the keeper's labels first, then any new ones from the merged tasks
            for t in [k] + others:
                labels += [x for x in t['labels'].split(',') if x and x not in labels]
            labels = labels[:M.LABEL_MAX_COUNT]
            for t in [k] + others:      # pin each task to its own mirrored list item before old ids move (see mirror.py)
                uid = con.execute("SELECT MIN(value) FROM legacy WHERE task_id = ? AND kind = 'todo_uid'", (t['id'],)).fetchone()[0]
                if uid:
                    con.execute("INSERT INTO mirror(task_id, uid, sent) VALUES(?, ?, '') ON CONFLICT(task_id) DO UPDATE SET uid = excluded.uid WHERE mirror.uid = ''", (t['id'], uid))
            for t in others:
                n = t['id']
                comments = con.execute("SELECT at, actor, text FROM events WHERE task_id = ? AND kind = 'comment' ORDER BY id", (n,)).fetchall()
                for at, who, text in comments:
                    self._event(con, keep, who, 'comment', text='From #%d: %s' % (n, text), data={'merged_from': n, 'at': at})
                q = self._open_question(con, n)
                moved_q = False
                if q and not self._open_question(con, keep):
                    con.execute('UPDATE questions SET task_id = ? WHERE id = ?', (keep, q['id']))
                    self._event(con, keep, q['asked_by'], 'asked', text=q['after'], data={'questions': len(q['questions']), 'merged_from': n})
                    moved_q = True
                elif q:
                    lines = ['%s%s' % (x['text'], ' (%s)' % ' / '.join(x['options']) if x['options'] else '') for x in q['questions']]
                    self._event(con, keep, q['asked_by'], 'comment', text='From #%d: open question: %s' % (n, ' | '.join(lines)), data={'merged_from': n, 'question': True})
                have = set(con.execute('SELECT kind, url, text FROM links WHERE task_id = ?', (keep,)).fetchall())
                links = 0
                for at, who, kind, url, text in con.execute('SELECT at, actor, kind, url, text FROM links WHERE task_id = ? ORDER BY id', (n,)).fetchall():
                    if (kind, url, text) not in have:
                        have.add((kind, url, text))
                        con.execute('INSERT INTO links(task_id, at, actor, kind, url, text) VALUES(?,?,?,?,?,?)', (keep, at, who, kind, url, text))
                        links += 1
                old = [{'kind': r[0], 'value': r[1]} for r in con.execute('SELECT kind, value FROM legacy WHERE task_id = ? ORDER BY kind, value', (n,))]
                con.execute('UPDATE legacy SET task_id = ? WHERE task_id = ?', (keep, n))
                for rid, src, dst, kind, at, who in con.execute('SELECT id, src, dst, kind, at, actor FROM relations WHERE src = ? OR dst = ?', (n, n)).fetchall():
                    src, dst = (keep if src == n else src), (keep if dst == n else dst)
                    if kind == 'relates_to':
                        src, dst = min(src, dst), max(src, dst)
                    if src != dst:      # a link between the two merged tasks disappears; a link that the keeper already has is not doubled
                        con.execute('INSERT OR IGNORE INTO relations(src, dst, kind, at, actor) VALUES(?,?,?,?,?)', (src, dst, kind, at, who))
                    con.execute('DELETE FROM relations WHERE id = ?', (rid,))
                moved = {'comments': len(comments), 'links': links, 'old_ids': old, 'question': 'moved' if moved_q else 'comment' if q else ''}
                self._close(con, t, actor, M.check_close(t, actor, 'duplicate', 'Merged into task %d.' % keep, keep))
                self._event(con, n, actor, 'merged_into', text='Merged into task %d. Its comments, links and old ids are on that task now.' % keep, data=dict(moved, task=keep))
                self._event(con, keep, actor, 'merged', text='Task %d (%s) was merged into this one.' % (n, t['title']), data=dict(moved, task=n, stage=t['stage']))
            if due != k['due']:
                con.execute('UPDATE tasks SET due = ? WHERE id = ?', (due, keep))
                self._event(con, keep, actor, 'edited', text='Changed: due (the earliest date of the merged tasks)')
            if ','.join(labels) != k['labels']:
                con.execute('UPDATE tasks SET labels = ? WHERE id = ?', (','.join(labels), keep))
                self._event(con, keep, actor, 'edited', text='Changed: labels (all the labels of the merged tasks)')
            if prio != k['priority']:
                con.execute('UPDATE tasks SET priority = ? WHERE id = ?', (prio, keep))
                self._event(con, keep, actor, 'edited', text='Changed: priority (the most urgent of the merged tasks)')
        return {'task': self.get(keep), 'merged': list(merge)}

    def duplicates(self):
        """Groups of OPEN tasks whose titles nearly match (labels such as "Handoff from <name>:" are ignored), without the pairs marked as not duplicates.
        -> {groups: [{ids, score, keep (the task a merge would keep by default), tasks: [summaries]}]}"""
        con = self.db.con
        by_id = {t['id']: t for t in self.list(stages=list(M.OPEN_STAGES)) if not t.get('parent_id')}   # child tasks of one parent share a title stem on purpose
        dismissed = con.execute('SELECT a, b FROM not_duplicates').fetchall()
        groups = M.duplicate_groups([(i, t['title']) for i, t in by_id.items()], dismissed)
        for g in groups:
            g['tasks'] = [by_id[i] for i in g['ids']]
            g['keep'] = M.default_keeper(g['tasks'])
        return {'groups': groups}

    def not_duplicates(self, ids, actor):
        """Remember that these tasks are NOT duplicates of each other (every pair), so they are not offered again."""
        M.check_actor(actor)
        if actor == 'check':
            raise M.RuleError('forbidden', 'a check cannot decide this')
        ids = sorted(self._ids(ids, 'ids', 2))
        with self.db.tx() as con:
            for i in ids:
                self._row(con, i)
            at = self.now()
            for x, a in enumerate(ids):
                for b in ids[x + 1:]:
                    con.execute('INSERT OR IGNORE INTO not_duplicates(a, b, at) VALUES(?,?,?)', (a, b, at))
            for i in ids:
                self._event(con, i, actor, 'not_duplicate', text='Marked as not a duplicate of %s.' % ', '.join('#%d' % o for o in ids if o != i))
        return {'ids': ids}

    def import_task(self, item):
        """Migration only (owner token). item: {title, stage, description?, plan?, due?, verify_by?, legacy?, question?: {questions, after},
        close?: {reason_kind, detail}, notes?: [text], why?: text}. Stored in the given stage with ONE 'imported' event that says why.
        A task that already owns the same old to-do uid or queue id is left alone. -> {task, existing}."""
        if not isinstance(item, dict):
            raise M.RuleError('bad', 'item must be an object')
        extra = set(item) - {'title', 'stage', 'description', 'plan', 'due', 'verify_by', 'legacy', 'question', 'close', 'notes', 'why'}
        if extra:
            raise M.RuleError('bad', 'unknown field: %s' % sorted(extra)[0][:30])
        title = M.clean_text(item.get('title'), M.TITLE_MAX, 'title', required=True)
        stage = item.get('stage')
        if stage not in M.STAGES or stage in ('implementing', 'verifying', 'done'):
            raise M.RuleError('bad', 'an imported task starts in todo, planning, approved or closed')
        description = M.clean_text(item.get('description'), M.TEXT_MAX, 'description')
        plan = M.clean_text(item.get('plan'), M.TEXT_MAX, 'plan')
        if stage in ('planning', 'approved') and not plan:
            raise M.RuleError('bad', 'a task imported into Planning or Approved needs a plan')
        due = M.check_due(item.get('due'))
        verify_by = item.get('verify_by', 'check')
        if verify_by not in M.VERIFY_BY:
            raise M.RuleError('bad', 'verify_by must be check or owner')
        legacy = M.check_legacy(item.get('legacy'))
        why = M.clean_text(item.get('why'), M.REASON_MAX, 'why')
        notes = item.get('notes') or []
        if not isinstance(notes, list) or len(notes) > 100:
            raise M.RuleError('bad', 'notes must be a short list of texts')
        notes = [M.clean_text(n, COMMENT_MAX, 'note', required=True) for n in notes]
        question = item.get('question')
        if question is not None:
            if not isinstance(question, dict) or stage == 'closed':
                raise M.RuleError('bad', 'question must be {questions, after} on an open task')
            qs = M.clean_questions(question.get('questions'))
            after = M.clean_text(question.get('after'), 1000, 'after', required=True)
        close = item.get('close')
        if (stage == 'closed') != (close is not None):
            raise M.RuleError('bad', 'a closed task needs close {reason_kind, detail}; an open one must not have it')
        with self.db.tx() as con:
            for x in legacy:
                r = con.execute('SELECT task_id FROM legacy WHERE kind = ? AND value = ?', (x['kind'], x['value'])).fetchone() if x['kind'] in IDENTITY_KINDS else None
                if r:
                    return {'task': self.get(r[0]), 'existing': True}
            first = 'todo' if stage == 'closed' else stage
            tid = self._insert(con, title, description, plan, first, due, verify_by, 'import', 'system')
            self._add_legacy(con, tid, legacy)
            self._event(con, tid, 'system', 'imported', '', first, why)
            for n in notes:
                self._event(con, tid, 'system', 'comment', text=n, data={'imported': True})
            if question is not None:
                con.execute("INSERT INTO questions(task_id, status, asked_at, asked_by, questions, after) VALUES(?, 'open', ?, 'system', ?, ?)",
                            (tid, self.now(), json.dumps(qs, ensure_ascii=False), after))
                self._event(con, tid, 'system', 'asked', text=after, data={'questions': len(qs)})
            if close is not None:
                if not isinstance(close, dict):
                    raise M.RuleError('bad', 'close must be {reason_kind, detail}')
                t = {'id': tid, 'stage': 'todo'}
                self._close(con, t, 'system', M.check_close(t, 'system', close.get('reason_kind'), close.get('detail', ''), close.get('duplicate_of')))
        return {'task': self.get(tid), 'existing': False}

    # ---- mirror bookkeeping (see mirror.py) ----
    def mirror_rows(self):
        return {r[0]: {'uid': r[1], 'sent': r[2]} for r in self.db.con.execute('SELECT task_id, uid, sent FROM mirror')}

    def mirror_set(self, task_id, uid, sent):
        self.db.execute('INSERT INTO mirror(task_id, uid, sent) VALUES(?,?,?) ON CONFLICT(task_id) DO UPDATE SET uid = excluded.uid, sent = excluded.sent', (task_id, uid, sent))

    def legacy_uids(self):
        return {r[0]: r[1] for r in self.db.con.execute("SELECT task_id, value FROM legacy WHERE kind = 'todo_uid'")}
