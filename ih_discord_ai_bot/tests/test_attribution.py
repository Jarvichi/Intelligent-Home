"""Caller name, 'answered by' line and the 2000-character limit. No network."""
import io
import json
import os
import sys
import types
import unittest

os.environ.setdefault('IH_TEST', '1')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import main

OPTS = {'ai_layer_url': 'http://layer:8801', 'api_token': 't', 'capability': '', 'ai_timeout_s': 5}


class Resp:
    def __init__(self, obj):
        self.b = json.dumps(obj).encode()

    def read(self, n=-1):
        return self.b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def run(resp):
    seen = {}

    def opener(req, timeout=None):
        seen['body'] = json.loads(req.data)
        return Resp(resp)
    return main.ask_ai(OPTS, 'hi', 'rid1', opener=opener, trace_id='a' * 32, parent_span_id='b' * 16), seen['body']


class Attribution(unittest.TestCase):
    def test_sends_caller(self):
        _, body = run({'ok': True, 'answer': 'x', 'agent': 'ih_ai_layer_groq'})
        self.assertEqual(body['caller'], 'ih_discord_ai_bot')

    def test_friendly_names(self):
        for a, n in (('ih_ai_layer_groq', 'Groq'), ('ih_ai_layer_gemini', 'Gemini'), ('ih_ai_layer_publicapis', 'PublicAPIs.io'), ('ih_ai_layer_serpapi', 'SerpApi'),
                     ('ih_ai_layer_openrouter', 'OpenRouter'), ('ih_ai_layer_claude_cli', 'claude_cli'), ('ih_ai_layer_newthing', 'newthing')):
            self.assertEqual(main.friendly_agent(a), n)
        self.assertEqual(main.friendly_agent(None), '')
        self.assertEqual(main.friendly_agent('bad name!'), '')

    def test_line_with_and_without_model(self):
        self.assertEqual(main.attribution('ih_ai_layer_groq', 'openai/gpt-oss-120b'), '-# answered by Groq · openai/gpt-oss-120b')
        self.assertEqual(main.attribution('ih_ai_layer_groq'), '-# answered by Groq')
        self.assertEqual(main.attribution('ih_ai_layer_groq', 'bad model text'), '-# answered by Groq')

    def test_ask_ai_credit_only_for_answers(self):
        r, _ = run({'ok': True, 'answer': 'hello', 'agent': 'ih_ai_layer_groq', 'model': 'm-1'})
        self.assertEqual((r[0], r[1], r[4]), ('answer', 'hello', '-# answered by Groq · m-1'))
        r, _ = run({'ok': False, 'answer': 'sorry', 'agent': 'ih_ai_layer_groq'})
        self.assertEqual((r[0], r[4]), ('error', ''))
        r, _ = run({'ok': True, 'answer': '[dry] x', 'agent': 'ih_ai_layer_groq', 'mode': 'dry'})
        self.assertEqual(r[4], '')


class Reply(unittest.TestCase):
    def mod(self):
        m = main.Module.__new__(main.Module)
        posted = []
        m.client = types.SimpleNamespace(post=lambda ck, text, reply_to=None, tag=None: posted.append(text) or {'ok': True})
        m.dry_now = lambda: False
        m.lg = types.SimpleNamespace(info=lambda *a, **k: None)
        m.own = main.collections.OrderedDict()
        m.out_failed = m.errors_posted = m.answered = 0
        return m, posted

    def test_credit_appended(self):
        m, posted = self.mod()
        m.reply('ck', '1', 'hello', credit='-# answered by Groq')
        self.assertEqual(posted[0], 'hello\n\n-# answered by Groq')

    def test_long_answer_truncated_not_credit(self):
        m, posted = self.mod()
        credit = main.attribution('ih_ai_layer_openrouter', 'meta-llama/llama-3.3-70b-instruct:free')
        m.reply('ck', '1', 'x' * 5000, credit=credit)
        self.assertLessEqual(len(posted[0]), 2000)
        self.assertTrue(posted[0].endswith(credit))
        self.assertIn('...\n\n-# answered', posted[0])

    def test_error_reply_has_no_credit(self):
        m, posted = self.mod()
        m.reply('ck', '1', main.MSG_NONE, error=True)
        self.assertEqual(posted[0], main.MSG_NONE)


if __name__ == '__main__':
    unittest.main()
