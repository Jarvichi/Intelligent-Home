"""Google Gemini (generateContent). Key in the x-goog-api-key header."""
import json, urllib.request
from .base import Provider, SYSTEM, CAPS_QA, image_mime


class Gemini(Provider):
    NAME, LABEL, VERSION = 'gemini', 'Gemini', '0.3.0'
    CAPABILITIES = CAPS_QA + ['vision']   # 0.3.0: one picture (JPEG/PNG) + a question; the picture leaves the house for Google
    API_BASE = 'https://generativelanguage.googleapis.com/v1beta/models/'
    DEFAULT_MODEL = 'gemini-3.5-flash'
    MODEL_PATTERN = r'[A-Za-z0-9._-]{1,60}'
    KEY_OPTION = 'gemini_key'

    def generate(self, cap, text, timeout, image=None):
        """The ONLY outside call. Key goes in the header here and nowhere else. -> reply text (may be empty)."""
        body = json.dumps({'systemInstruction': {'parts': [{'text': SYSTEM[cap]}]},
                           'contents': [{'role': 'user', 'parts': ([{'inline_data': {'mime_type': image_mime(image), 'data': ''.join(image.split())}}] if cap == 'vision' else []) + [{'text': text}]}],
                           'generationConfig': {'maxOutputTokens': self.opts['max_tokens']}}).encode()
        req = urllib.request.Request(self.API_BASE + self.opts['model'] + ':generateContent', data=body, method='POST',
                                     headers={'Content-Type': 'application/json', 'x-goog-api-key': self.opts['key']})
        with self.opener(req, timeout=timeout) as x:
            data = json.loads(x.read(2000000))
        try:
            parts = data['candidates'][0]['content']['parts']
            return ''.join(p.get('text', '') for p in parts if isinstance(p, dict)).strip()
        except (KeyError, IndexError, TypeError, AttributeError):
            return ''
