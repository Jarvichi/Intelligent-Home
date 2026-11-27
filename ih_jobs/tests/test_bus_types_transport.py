"""Bus types on the Transport page: unknown-code attributes (all codes), the card config, the AI question, and the save path (incl. database down = 503).
Fictional values, temp folders only."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import jobsdb
from plugins import bus_watch as B
from plugins import transport_page as P


class Base(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        self.plugin = os.path.join(self.data, 'bus_watch')
        os.makedirs(self.plugin)
        self.addCleanup(jobsdb.close_all)

    def log(self, rows):
        with open(os.path.join(self.plugin, 'promise_log.jsonl'), 'w') as f:
            for r in rows:
                f.write(json.dumps(r) + '\n')


class UnknownAttrs(Base):
    def test_lists_all_codes_not_just_a_few(self):
        self.log([{'vehicle': 'OP-%d' % (100 + i), 'deck': 'unknown', 'date': '2026-11-%02d' % (i % 28 + 1)} for i in range(60)])
        a = B.unknown_attrs(self.plugin)
        self.assertEqual(a['unknown_count'], 60)
        self.assertEqual(len(a['unknown_codes']), 60)

    def test_answered_codes_are_left_out(self):
        self.log([{'vehicle': 'OP-1', 'deck': 'unknown', 'date': '2026-11-01'}, {'vehicle': 'OP-2', 'deck': 'unknown', 'date': '2026-11-02'}])
        self.assertEqual(B.http_overrides(self.plugin, 'POST', {'code': 'OP-1', 'deck': 'double'})[0], 200)
        self.assertEqual(B.unknown_attrs(self.plugin)['unknown_codes'], ['2'])

    def test_missing_log_is_empty(self):
        self.assertEqual(B.unknown_attrs(self.plugin), {'unknown_codes': [], 'unknown_count': 0})


class Card(unittest.TestCase):
    def s(self, **o):
        s = dict(P.DEFAULTS)
        s.update({'morning_entity': 'sensor.m', 'promise_entity': 'sensor.p'})
        s.update(o)
        return s

    def test_off_by_default(self):
        self.assertIsNone(P.bus_types_card(self.s()))
        titles = [c.get('title') for c in P.build_config(self.s())['views'][1]['sections'][0]['cards']]
        self.assertNotIn('Bus types', titles)

    def test_card_has_dropdown_choice_save_and_admin_link(self):
        s = self.s(type_code_entity='input_select.code', type_deck_entity='input_select.deck', type_save_entity='script.save', type_admin_url='/x')
        cards = P.build_config(s)['views'][1]['sections'][0]['cards']
        ent = [c for c in cards if c.get('title') == 'Bus types'][0]['entities']
        self.assertEqual([e['entity'] for e in ent], ['input_select.code', 'input_select.deck', 'script.save'])
        self.assertEqual(ent[2]['type'], 'button')
        self.assertIn('unknown_codes', cards[-3]['content'])
        self.assertIn('(/x)', cards[-1]['content'])


class Question(Base):
    def test_ai_question_asks_make_model_and_registration(self):
        # the question text is a fixed template: read it from the source rather than faking the whole AI layer
        src = open(B.__file__).read()
        self.assertIn('registration plate', src)
        self.assertIn('make and model', src)
        self.assertIn('single or double deck', src)


class SaveAndDown(Base):
    def test_save_ok(self):
        code, out = B.http_overrides(self.plugin, 'POST', {'code': 'OP-5', 'deck': 'single', 'by': 'transport'})
        self.assertEqual((code, out['ok']), (200, True))

    def test_database_down_is_503_never_file_not_found(self):
        with open(os.path.join(self.data, jobsdb.DB_NAME), 'wb') as f:
            f.write(b'this is not a database' * 50)
        code, out = B.http_overrides(self.plugin, 'POST', {'code': 'OP-5', 'deck': 'single'})
        self.assertEqual(code, 503)
        self.assertFalse(out['ok'])
        self.assertIn('Nothing was saved', out['msg'])
        self.assertNotIn('File not found', out['msg'])


if __name__ == '__main__':
    unittest.main()
