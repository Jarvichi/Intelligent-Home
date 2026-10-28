"""Google Gemini (generateContent). Key in the x-goog-api-key header."""
import json, urllib.request
from .base import Provider, SYSTEM


class Gemini(Provider):
    NAME, LABEL, VERSION = 'gemini', 'Gemini', '0.2.2'
    API_BASE = 'https://generativelanguage.googleapis.com/v1beta/models/'
    DEFAULT_MODEL = 'gemini-2.5-flash'
    MODEL_PATTERN = r'[A-Za-z0-9._-]{1,60}'
    KEY_OPTION = 'gemini_key'

    def generate(self, cap, text, timeout):
        """The ONLY outside call. Key goes in the header here and nowhere else. -> reply text (may be empty)."""
        body = json.dumps({'systemInstruction': {'parts': [{'text': SYSTEM[cap]}]},
                           'contents': [{'role': 'user', 'parts': [{'text': text}]}],
                           'generationConfig': {'maxOutputTokens': 1024}}).encode()
        req = urllib.request.Request(self.API_BASE + self.opts['model'] + ':generateContent', data=body, method='POST',
                                     headers={'Content-Type': 'application/json', 'x-goog-api-key': self.opts['key']})
        with self.opener(req, timeout=timeout) as x:
            data = json.loads(x.read(2000000))
        try:
            parts = data['candidates'][0]['content']['parts']
            return ''.join(p.get('text', '') for p in parts if isinstance(p, dict)).strip()
        except (KeyError, IndexError, TypeError, AttributeError):
            return ''
