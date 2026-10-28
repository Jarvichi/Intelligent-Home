"""Anthropic Claude (Messages API). Key in the x-api-key header."""
import json, urllib.request
from .base import Provider, SYSTEM


class Claude(Provider):
    NAME, LABEL, VERSION = 'claude', 'Claude', '0.2.2'
    API_URL = 'https://api.anthropic.com/v1/messages'
    DEFAULT_MODEL = 'claude-haiku-5-5'
    KEY_OPTION = 'anthropic_key'
    DEFAULT_DAILY_CAP = 20

    def generate(self, cap, text, timeout):
        """The ONLY outside call. Key goes in the x-api-key header here and nowhere else. -> reply text (may be empty)."""
        body = json.dumps({'model': self.opts['model'], 'max_tokens': 1024, 'system': SYSTEM[cap],
                           'messages': [{'role': 'user', 'content': text}]}).encode()
        req = urllib.request.Request(self.API_URL, data=body, method='POST',
                                     headers={'Content-Type': 'application/json', 'x-api-key': self.opts['key'], 'anthropic-version': '2023-06-01'})
        with self.opener(req, timeout=timeout) as x:
            data = json.loads(x.read(2000000))
        try:
            return ''.join(p.get('text', '') for p in data['content'] if isinstance(p, dict) and p.get('type', 'text') == 'text').strip()
        except (KeyError, TypeError, AttributeError):
            return ''
