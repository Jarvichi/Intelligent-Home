"""Groq (OpenAI-compatible chat completions). Key in the Authorization header."""
from .base import ChatProvider


class Groq(ChatProvider):
    NAME, LABEL, VERSION = 'groq', 'Groq', '0.2.3'
    API_URL = 'https://api.groq.com/openai/v1/chat/completions'
    DEFAULT_MODEL = 'openai/gpt-oss-120b'
    KEY_OPTION = 'groq_key'
