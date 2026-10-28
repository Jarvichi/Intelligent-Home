"""Pollinations (OpenAI-compatible chat completions). The service works without a key, so no key is needed.
PUBLIC service: prompts are public, use it for generic non-private jobs only. Default plan is free."""
from .base import ChatProvider


class Pollinations(ChatProvider):
    NAME, LABEL, VERSION = 'pollinations', 'Pollinations', '0.1.2'
    API_URL = 'https://text.pollinations.ai/openai'     # UNVERIFIED endpoint (OpenAI-compatible chat completions, no key needed)
    DEFAULT_MODEL = 'openai'
    KEY_OPTION = 'pollinations_key'
    KEY_REQUIRED = False
    DEFAULT_FREE = True

    def headers(self):
        hdr = {'Content-Type': 'application/json'}
        if self.opts['key']:      # optional
            hdr['Authorization'] = 'Bearer ' + self.opts['key']
        return hdr

    def status(self):
        s = super().status()
        s.update({'configured': True, 'key_set': bool(self.opts['key']), 'public_prompts': True})
        return s
