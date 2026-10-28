"""OpenRouter (OpenAI-compatible chat completions). Key in the Authorization header."""
from .base import ChatProvider


class OpenRouter(ChatProvider):
    NAME, LABEL, VERSION = 'openrouter', 'OpenRouter', '0.2.2'
    API_URL = 'https://openrouter.ai/api/v1/chat/completions'
    DEFAULT_MODEL = 'openrouter/auto'
    KEY_OPTION = 'openrouter_key'
