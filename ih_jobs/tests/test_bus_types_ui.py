"""Bus types card: the ingress server's payload cleaning and its save path (the add-on's own endpoint is faked)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import ui_server


class Payload(unittest.TestCase):
    def test_unknown_list_and_answers(self):
        out = ui_server.bus_types_payload({'ok': True, 'overrides': {'5': 'single', '6': 'junk'}, 'db': {'db': True},
                                           'unknown': [{'code': '12', 'count': 2, 'last_seen': '2026-11-12', 'vehicle_type': None}, {'code': 'x'}, 'bad']})
        self.assertEqual(out['unknown'], [{'code': '12', 'count': 2, 'last_seen': '2026-11-12', 'vehicle_type': None}])
        self.assertEqual(out['answers'], {'5': 'single'})
        self.assertTrue(out['available'] and out['db_ok'])

    def test_failed_read_is_not_empty(self):
        self.assertEqual(ui_server.bus_types_payload(None), {'available': False})
        self.assertEqual(ui_server.bus_types_payload({'ok': False}), {'available': False})


class Save(unittest.TestCase):
    def post(self, body, call):
        h = ui_server.Handler.__new__(ui_server.Handler)
        h.client_address = (ui_server.PEER, 1); h.path = '/api/bus-types'
        raw = body.encode()
        h.headers = {'Content-Length': str(len(raw))}
        h.rfile = mock.Mock(read=lambda n: raw)
        sent = []
        h.reply = lambda code, b, t: sent.append((code, b))
        with mock.patch.object(ui_server, 'bus_call', call):
            h.do_POST()
        return sent[0]

    def test_saves_through_the_endpoint(self):
        seen = []
        code, _ = self.post('{"code":"123","deck":"double"}', lambda m, b=None: (seen.append((m, b)), (200, {'ok': True, 'msg': 'Saved 123 as double'}))[1])
        self.assertEqual(code, 200)
        self.assertEqual(seen[0][1]['code'], '123')

    def test_database_down_is_503(self):
        code, body = self.post('{"code":"123","deck":"single"}', lambda m, b=None: (503, {'ok': False, 'msg': 'The add-on database is unavailable. Nothing was saved.'}))
        self.assertEqual(code, 503)
        self.assertIn(b'Nothing was saved', body)

    def test_bad_input_is_400_and_never_sent(self):
        for body in ('{"code":"abc","deck":"single"}', '{"code":"1","deck":"triple"}', 'nope'):
            code, _ = self.post(body, lambda m, b=None: self.fail('must not call'))
            self.assertEqual(code, 400)


if __name__ == '__main__':
    unittest.main()
