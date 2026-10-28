"""In-process AI providers of ih_ai_layer (Phase 4). One module per provider; the registry maps the `name` used in the `providers` option to its class.
claude_cli is NOT here: it needs the Claude CLI and stays a separate add-on."""
import os
from .gemini import Gemini
from .groq import Groq
from .openrouter import OpenRouter
from .mistral import Mistral
from .openai import OpenAI
from .pollinations import Pollinations
from .serpapi import SerpApi
from .grok import Grok
from .claude import Claude

REGISTRY = {c.NAME: c for c in (Gemini, Groq, OpenRouter, Mistral, OpenAI, Pollinations, SerpApi, Grok, Claude)}
KEY_OPTIONS = {c.NAME: c.KEY_OPTION for c in REGISTRY.values()}      # provider -> flat option name that can hold its key (the settings hub fills these)


def build(entries, flat_keys, data, log, opener, clock, today=None):
    """entries: sanitised `providers` option. flat_keys: {provider: key from the flat option}. -> {agent name: Provider}.
    Disabled or unknown entries are skipped; a name listed twice: the first wins."""
    out = {}
    for e in entries or []:
        cls = REGISTRY.get(e.get('name'))
        if cls is None or not e.get('enabled') or cls.agent_name() in out:
            continue
        kw = {'today': today} if today else {}
        out[cls.agent_name()] = cls(cls.make_opts(e, flat_keys.get(cls.NAME, '')), os.path.join(data, 'providers', cls.NAME), log=log, opener=opener, clock=clock, **kw)
    return out
