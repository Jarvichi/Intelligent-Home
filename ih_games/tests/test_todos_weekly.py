import datetime
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import todos


class Lib:
    def __init__(self, rows): self.rows, self.marked = rows, []
    def games_without_todo(self): return self.rows
    def mark_todo(self, player, gid): self.marked.append((player, gid))


def row(title):
    return {'title': title, 'players': ['p1'], 'ids': {'p1': 'id-' + title}}


class Ctx:
    def __init__(self, responses=(), dry=False):
        self.dry, self.opts, self.responses, self.calls = dry, {'tasks_api_url': 'http://tasks', 'tasks_api_token': 'test'}, list(responses), []
    def log(self, text): pass
    def open(self, req, timeout=10):
        self.calls.append((req.get_method(), req.full_url, json.loads(req.data) if req.data else None))
        value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return io.BytesIO(json.dumps(value).encode())


class TasksQueue(unittest.TestCase):
    def test_batch_creates_normal_priority_task(self):
        lib, ctx = Lib([row('A'), row('B')]), Ctx([{'tasks': []}, {'task': {'id': 1, 'stage': 'todo'}}])
        made, _ = todos.sync(lib, ctx, today=datetime.date(2026, 11, 11))
        data = ctx.calls[1][2]
        self.assertEqual(data['priority'], 'normal')
        self.assertEqual(data['due'], '2026-11-18')
        self.assertIn('- B (p1)', data['description'])
        self.assertEqual(made, ['A', 'B'])
        self.assertEqual(len(lib.marked), 2)

    def test_append_to_existing_todo(self):
        item = {'id': 2, 'title': todos.SUMMARY, 'source': 'ih_games'}
        ctx = Ctx([{'tasks': [item]}, {'id': 2, 'stage': 'todo', 'description': 'Plan\n- A (p1)'}, {'id': 2}])
        lib = Lib([row('A'), row('B')]); todos.sync(lib, ctx)
        self.assertEqual(ctx.calls[-1][2]['description'], 'Plan\n- A (p1)\n- B (p1)')
        self.assertEqual(len(lib.marked), 2)

    def test_failure_and_dry_do_not_mark(self):
        for ctx in (Ctx(dry=True), Ctx([OSError('unavailable')]), Ctx()):
            lib = Lib([row('A')])
            if not ctx.dry and not ctx.responses: ctx.opts['tasks_api_token'] = ''
            if ctx.dry: todos.sync(lib, ctx)
            else:
                with self.assertRaises((OSError, RuntimeError)): todos.sync(lib, ctx)
            self.assertEqual(lib.marked, [])

    def test_changed_stage_is_not_edited(self):
        item = {'id': 2, 'title': todos.SUMMARY, 'source': 'ih_games'}
        ctx = Ctx([{'tasks': [item]}, {'id': 2, 'stage': 'approved'}]); lib = Lib([row('A')])
        with self.assertRaises(RuntimeError): todos.sync(lib, ctx)
        self.assertEqual(lib.marked, [])
        self.assertEqual(len(ctx.calls), 2)


if __name__ == '__main__': unittest.main()
