"""Groq (OpenAI-compatible chat completions). Key in the Authorization header."""
from .base import ChatProvider


class Groq(ChatProvider):
    NAME, LABEL, VERSION = 'groq', 'Groq', '0.2.2'
    API_URL = 'https://api.groq.com/openai/v1/chat/completions'
    DEFAULT_MODEL = 'llama-3.3-70b-versatile'
    KEY_OPTION = 'groq_key'
