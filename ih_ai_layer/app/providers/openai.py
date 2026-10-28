"""OpenAI (Responses API). Key in the Authorization header."""
import json, urllib.request
from .base import Provider, SYSTEM


class OpenAI(Provider):
    NAME, LABEL, VERSION = 'openai', 'OpenAI', '0.1.2'
    API_URL = 'https://api.openai.com/v1/responses'
    DEFAULT_MODEL = 'gpt-6-luna'
    KEY_OPTION = 'openai_key'

    def generate(self, cap, text, timeout):
        """The ONLY outside call. Key goes in the Authorization header here and nowhere else. -> reply text (may be empty)."""
        body = json.dumps({'model': self.opts['model'], 'instructions': SYSTEM[cap], 'input': text, 'max_output_tokens': 1024, 'store': False}).encode()
        req = urllib.request.Request(self.API_URL, data=body, method='POST',
                                     headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.opts['key']})
        with self.opener(req, timeout=timeout) as x:
            data = json.loads(x.read(2000000))
        try:
            parts = [c.get('text', '') for o in data.get('output', []) if o.get('type') == 'message' for c in o.get('content', [])
                     if isinstance(c.get('text', ''), str)]
            return ''.join(parts).strip()
        except (TypeError, AttributeError):
            return ''
