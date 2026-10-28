"""xAI Grok (OpenAI-compatible chat completions). Key in the Authorization header."""
from .base import ChatProvider


class Grok(ChatProvider):
    NAME, LABEL, VERSION = 'grok', 'Grok', '0.2.2'
    API_URL = 'https://api.x.ai/v1/chat/completions'
    DEFAULT_MODEL = 'grok-4'
    KEY_OPTION = 'xai_key'
