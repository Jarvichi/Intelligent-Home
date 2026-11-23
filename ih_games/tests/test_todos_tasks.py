import datetime
import io
import json
import os
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import todos

TODAY = datetime.date(2026, 11, 23)


class Lib:
    def __init__(self, entries):
        self.entries = entries   # {gid: entry}
        self.marked = {}

    def games_without_todo(self):
        return [{'title': e['title'], 'players': ['p1'], 'ids': {'p1': g}} for g, e in self.entries.items() if not e.get('todo')]

    def get(self, player, gid): return self.entries.get(gid)
    def set_research(self, player, gid, tid, nxt=None):
        self.marked[gid] = (tid, nxt); self.entries[gid]['todo'] = True; self.entries[gid]['task'] = tid
        if nxt: self.entries[gid]['next_recheck'] = nxt
    def bump_recheck(self, player, gid, nxt):
        e = self.entries[gid]; e['rechecks'] = int(e.get('rechecks') or 0) + 1; e['next_recheck'] = nxt
    def research_entries(self): return [('p1', dict(e)) for e in self.entries.values() if e.get('task')]
    def find(self, player, title): return next((e for e in self.entries.values() if e['title'] == title), None)


def game(gid, title, last_seen, first_seen='2026-01-01', **kw):
    return dict({'id': gid, 'title': title, 'platform': 'psn', 'first_seen': first_seen + 'T10:00:00+00:00',
                 'last_seen': last_seen + 'T10:00:00+00:00', 'tools': [], 'todo': False}, **kw)


class Ctx:
    """Scripted IH Tasks: GET /legacy -> 404 unless a key is listed in `have`; POST /tasks -> a new id."""
    def __init__(self, have=None, dry=False, token='t'):
        self.dry, self.have, self.calls, self.next = dry, have or {}, [], 100
        self.opts = {'tasks_api_url': 'http://tasks', 'tasks_api_token': token}
    def log(self, text): pass
    def open(self, req, timeout=10):
        body = json.loads(req.data) if req.data else None
        self.calls.append((req.get_method(), req.full_url, body))
        if req.get_method() == 'GET':
            key = req.full_url.split('value=')[1].replace('%3A', ':')
            if key in self.have: return io.BytesIO(json.dumps({'id': self.have[key]}).encode())
            raise urllib.error.HTTPError(req.full_url, 404, 'missing', {}, None)
        self.next += 1
        return io.BytesIO(json.dumps({'task': {'id': self.next, 'stage': 'todo'}}).encode())
    def posts(self): return [c[2] for c in self.calls if c[0] == 'POST']


class PerGame(unittest.TestCase):
    def test_one_task_per_game_priority_by_recent_play(self):
        lib = Lib({'a': game('a', 'Playing Now', '2026-11-20'), 'b': game('b', 'Backlog', '2026-02-01')})
        ctx = Ctx()
        made, _ = todos.sync(lib, ctx, today=TODAY)
        posts = ctx.posts()
        self.assertEqual(sorted(made), ['Backlog', 'Playing Now'])
        by = {p['title']: p for p in posts}
        self.assertEqual(by['Games: research a tracker for Playing Now']['priority'], 'high')
        self.assertEqual(by['Games: research a tracker for Backlog']['priority'], 'low')
        self.assertEqual(by['Games: research a tracker for Playing Now']['due'], '2026-11-30')
        self.assertEqual(by['Games: research a tracker for Backlog']['due'], '2027-01-22')
        self.assertEqual(set(lib.marked), {'a', 'b'})

    def test_similar_titles_are_not_treated_as_duplicates(self):
        lib = Lib({'a': game('a', 'Jack Vol. 1', '2026-11-20'), 'b': game('b', 'Jack Vol. 2', '2026-11-20')})
        ctx = Ctx(); todos.sync(lib, ctx, today=TODAY)
        self.assertTrue(all(p.get('allow_duplicate') is True for p in ctx.posts()))

    def test_existing_task_is_reused_not_duplicated(self):
        lib = Lib({'a': game('a', 'Known', '2026-11-20')})
        ctx = Ctx(have={'ihgames:p1:a': 55})
        todos.sync(lib, ctx, today=TODAY)
        self.assertEqual(ctx.posts(), [])
        self.assertEqual(lib.marked['a'][0], 55)

    def test_marked_only_after_tasks_confirms(self):
        lib = Lib({'a': game('a', 'A', '2026-11-20')})
        with self.assertRaises(Exception): todos.sync(lib, Ctx(token=''), today=TODAY)
        self.assertEqual(lib.marked, {})

    def test_dry_changes_nothing(self):
        lib, ctx = Lib({'a': game('a', 'A', '2026-11-20')}), Ctx(dry=True)
        self.assertEqual(todos.sync(lib, ctx, today=TODAY), ([], []))
        self.assertEqual((lib.marked, ctx.calls), ({}, []))

    def test_new_and_played_game_gets_a_recheck_date(self):
        lib = Lib({'a': game('a', 'Fresh', '2026-11-22', first_seen='2026-11-19'), 'b': game('b', 'Old', '2026-11-22')})
        todos.sync(lib, Ctx(), today=TODAY)
        self.assertEqual(lib.marked['a'][1], '2026-12-14')
        self.assertIsNone(lib.marked['b'][1])


class Recheck(unittest.TestCase):
    def fresh(self, **kw):
        return Lib({'a': game('a', 'Fresh', '2026-12-10', first_seen='2026-11-19', todo=True, task=7, rechecks=0,
                              next_recheck='2026-12-14', **kw)})

    def test_due_recheck_creates_a_follow_up_task(self):
        lib, ctx = self.fresh(), Ctx()
        out = todos.recheck(lib, ctx, today=datetime.date(2026, 12, 14))
        self.assertEqual(out, ['Fresh'])
        p = ctx.posts()[0]
        self.assertIn('re-check Fresh', p['title']); self.assertEqual(p['priority'], 'high')
        self.assertIn('task #7', p['description'])
        self.assertEqual(lib.entries['a']['rechecks'], 1)
        self.assertEqual(lib.entries['a']['next_recheck'], '2027-01-04')

    def test_not_due_capped_or_stopped_playing_is_skipped(self):
        ctx = Ctx()
        self.assertEqual(todos.recheck(self.fresh(), ctx, today=datetime.date(2026, 12, 13)), [])
        capped = self.fresh(); capped.entries['a']['rechecks'] = todos.MAX_RECHECKS
        self.assertEqual(todos.recheck(capped, ctx, today=datetime.date(2026, 12, 14)), [])
        stopped = self.fresh(); stopped.entries['a']['last_seen'] = '2026-10-01T10:00:00+00:00'
        self.assertEqual(todos.recheck(stopped, ctx, today=datetime.date(2026, 12, 14)), [])
        self.assertEqual(ctx.calls, [])

    def test_last_recheck_clears_the_schedule(self):
        lib = self.fresh(); lib.entries['a']['rechecks'] = todos.MAX_RECHECKS - 1
        todos.recheck(lib, Ctx(), today=datetime.date(2026, 12, 14))
        self.assertIsNone(lib.entries['a']['next_recheck'])

    def test_recheck_now_on_demand(self):
        lib, ctx = self.fresh(), Ctx()
        tid = todos.recheck_now(lib, ctx, 'p1', 'Fresh', today=datetime.date(2026, 11, 23))
        self.assertEqual(tid, 101); self.assertEqual(lib.entries['a']['rechecks'], 1)
        with self.assertRaises(RuntimeError): todos.recheck_now(lib, ctx, 'p1', 'Nope')


if __name__ == '__main__':
    unittest.main()
