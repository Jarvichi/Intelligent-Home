"""Mistral (Agents conversations endpoint). Key in the Authorization header. Needs the setting agent_id (an agent created in the Mistral console)."""
import json, re, urllib.request
from .base import Provider, SYSTEM


class Mistral(Provider):
    NAME, LABEL, VERSION = 'mistral', 'Mistral', '0.1.2'
    API_URL = 'https://api.mistral.ai/v1/conversations'
    DEFAULT_MODEL = 'mistral-medium-latest'
    KEY_OPTION = 'mistral_key'
    DEFAULT_FREE = True

    @classmethod
    def extra_opts(cls, s, num):
        aid = str(s.get('agent_id') or '').strip()
        return {'agent_id': aid if re.fullmatch(r'[A-Za-z0-9_\-]{1,80}', aid) else ''}

    def config_error(self):
        return None if self.opts['agent_id'] else 'Mistral is not configured (no agent_id setting).'

    def generate(self, cap, text, timeout):
        """The ONLY outside call. Key goes in the Authorization header here and nowhere else. -> reply text (may be empty)."""
        # Free tier blocks plain chat completions (429 code 1300); the Agents conversations endpoint works. The agent holds the model;
        # the instruction is put in front of the text because a conversation takes no system message here.
        body = json.dumps({'agent_id': self.opts['agent_id'], 'agent_version': 0,
                           'inputs': [{'role': 'user', 'content': SYSTEM[cap] + '\n\n' + text}]}).encode()
        req = urllib.request.Request(self.API_URL, data=body, method='POST',
                                     headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.opts['key']})
        with self.opener(req, timeout=timeout) as x:
            data = json.loads(x.read(2000000))
        try:
            return ''.join(o['content'] for o in data.get('outputs', []) if o.get('type') == 'message.output' and isinstance(o.get('content'), str)).strip()
        except (KeyError, TypeError, AttributeError):
            return ''

    def status(self):
        s = super().status()
        s['agent_id_set'] = bool(self.opts['agent_id'])
        return s
