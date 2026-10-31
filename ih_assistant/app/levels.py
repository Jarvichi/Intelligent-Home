"""Behaviour levels (0..10) per persona: pure functions, no I/O.

One number per setting. 0 = never / off, 5 = middle, 10 = as much as possible.
  emoji_level      emoji in replies          (hard cap in code: strip_emoji)
  gif_level        sending GIFs              (hard gate in code: gif_allowed)
  checkin_level    proactive check-ins       (hard scale in code: checkin_scale)
  humour_level     jokes and playfulness     (prompt only)
  formality_level  casual -> formal          (prompt only)
  verbosity_level  short -> long replies     (prompt only)
  banter_level     teasing / cheeky tone     (prompt only)
  reminders_level  how often it reminds      (prompt only)
  funny_level, sarcastic_level, warm_level, blunt_level   personality traits (prompt only; added 31 Oct 2026)
No numeric caps. A persona may have one fixed prompt line (option fixed_rule); persona-specific defaults come from options, not code.
"""
import math, re

NAMES = ('emoji_level', 'gif_level', 'checkin_level', 'humour_level', 'formality_level', 'verbosity_level', 'banter_level', 'reminders_level',
         'funny_level', 'sarcastic_level', 'warm_level', 'blunt_level')

GENERIC = {'emoji_level': 2, 'gif_level': 5, 'checkin_level': 5, 'humour_level': 5, 'formality_level': 5, 'verbosity_level': 5, 'banter_level': 5, 'reminders_level': 5,
           'funny_level': 5, 'sarcastic_level': 5, 'warm_level': 5, 'blunt_level': 5}
DEFAULTS = {}      # persona-specific defaults are set through the per-persona options (no persona names in code)
CAPS = {}
FIXED_RULES = {}   # per-persona fixed prompt line comes from the persona option fixed_rule


def clamp(v, default):
    """-> int 0..10. Anything that is not a number (or a bool) gives the default."""
    if isinstance(v, bool) or v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f:
        return default
    return int(min(10, max(0, round(f))))


def defaults_for(key):
    return dict(DEFAULTS.get(str(key).lower(), GENERIC))


def resolve(key, raw):
    """raw = the persona option dict. -> dict of all NAMES, each an int 0..10 (missing or bad = that persona's default)."""
    d = defaults_for(key)
    raw = raw if isinstance(raw, dict) else {}
    return apply_caps(key, {n: clamp(raw.get(n), d[n]) for n in NAMES})


def apply_caps(key, levels):
    """Lower any level above this persona's ceiling in CAPS. Returns a new dict."""
    out = dict(levels)
    for n, c in CAPS.get(str(key).lower(), {}).items():
        if n in out:
            out[n] = min(out[n], c)
    return out


# ---- hard gates ----
def gif_allowed(level, roll):
    """roll = a random number in [0, 1). 0 = never, 10 = always, otherwise chance is level/10."""
    level = clamp(level, 0)
    if level <= 0:
        return False
    if level >= 10:
        return True
    return roll < level / 10.0


def checkin_scale(level):
    """-> factor 0.0..1.0 applied to the daily check-in cap. 0 = never, 10 = unchanged."""
    return clamp(level, 0) / 10.0


def checkin_cap(max_per_day, level):
    """Check-ins allowed per day: 0 when level is 0; otherwise at least 1 and at most max_per_day, scaled by level."""
    lv = clamp(level, 0)
    if lv <= 0 or max_per_day <= 0:
        return 0
    return min(max_per_day, max(1, int(math.ceil(max_per_day * lv / 10.0))))


def checkin_gap_factor(level):
    """Multiplier for the minimum gap between planned check-ins (level 10 = x1, level 5 = x2, level 1 = x10; 0 = never so unused)."""
    lv = clamp(level, 10)
    return 1.0 if lv >= 10 else (10.0 / lv if lv > 0 else 10.0)


_BASE = '[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF]'
# one emoji = base, optional variation selector / skin tone, optional joined extras (family, flags are two regional indicators)
EMOJI_RE = re.compile('[\U0001F1E6-\U0001F1FF]{2}|' + _BASE + '[\uFE0F\U0001F3FB-\U0001F3FF]*(?:\u200D' + _BASE + '[\uFE0F\U0001F3FB-\U0001F3FF]*)*')


def emoji_cap(level):
    """Most emoji allowed in ONE reply: 0 at level 0, then 1 (1-3), 2 (4-6), 3 (7-9), 5 (10)."""
    lv = clamp(level, 0)
    return 0 if lv == 0 else 1 if lv <= 3 else 2 if lv <= 6 else 3 if lv <= 9 else 5


def strip_emoji(text, level):
    """Keep at most emoji_cap(level) emoji in a reply (all removed at level 0); the extra ones are removed from the END. Text within the cap is returned unchanged."""
    cap = emoji_cap(level)
    ms = list(EMOJI_RE.finditer(text))
    if len(ms) <= cap:
        return text
    out = text
    for m in reversed(ms[cap:]):
        out = out[:m.start()] + out[m.end():]
    return re.sub(r'[ \t]{2,}', ' ', out).strip()


# ---- prompt block ----
_LINES = {
    'emoji_level': ((0, 'Do not use emoji.'), (3, 'Use an emoji only rarely (at most one, and not in most messages).'), (6, 'Use a few emoji, at most two per message.'), (9, 'Use emoji freely, up to three per message.'), (10, 'Use lots of emoji and make them part of your style.')),
    'gif_level': ((0, 'Never send a GIF.'), (3, 'Send a GIF only rarely, for a really good moment.'), (6, 'Send a GIF sometimes when it fits the mood.'), (9, 'Send a GIF often when it fits.'), (10, 'A GIF is welcome in almost every chat.')),
    'checkin_level': ((0, 'Never start a conversation yourself.'), (3, 'Start a conversation yourself only now and then.'), (6, 'Check in on a normal rhythm, but back off when they are busy.'), (9, 'Check in often and follow up on things they said.'), (10, 'Check in as often as the limits allow.')),
    'humour_level': ((0, 'Be serious. No jokes.'), (3, 'Allow a light joke only now and then.'), (6, 'Use humour naturally when it fits.'), (9, 'Be funny often.'), (10, 'Make humour a main part of your style.')),
    'formality_level': ((0, 'Write very casual, like a friend texting.'), (3, 'Write casual and relaxed.'), (6, 'Write in a neutral, polite way.'), (9, 'Write in a formal, careful way.'), (10, 'Write in a fully formal way.')),
    'verbosity_level': ((0, 'Answer in one short line.'), (3, 'Keep replies short: one to three sentences.'), (6, 'Give a short paragraph with the needed detail.'), (9, 'Give full, detailed answers.'), (10, 'Be thorough and explain everything.')),
    'banter_level': ((0, 'Do not tease.'), (3, 'Tease very gently, and only when they started it.'), (6, 'Tease in a friendly way.'), (9, 'Tease and joke a lot, always kindly.'), (10, 'Constant cheeky banter, always kind underneath.')),
    'reminders_level': ((0, 'Do not remind them of things unless they ask.'), (3, 'Remind only about important or time-bound things.'), (6, 'Remind about things that matter, once.'), (9, 'Remind about most things and follow up once.'), (10, 'Remind and follow up on everything open.')),
    'funny_level': ((0, 'Do not try to be funny.'), (3, 'Be funny only by accident.'), (6, 'Be funny when the moment is easy.'), (9, 'Be properly funny: jokes and wordplay are welcome.'), (10, 'Be as funny as you can, in almost every message.')),
    'sarcastic_level': ((0, 'Never be sarcastic. Say what you mean.'), (3, 'Allow a dry, gentle sarcastic remark now and then, never at their expense.'), (6, 'Use light sarcasm often, always clearly playful.'), (9, 'Be sarcastic a lot, but never unkind.'), (10, 'Sarcasm is your default voice, but never cruel.')),
    'warm_level': ((0, 'Stay neutral and businesslike.'), (3, 'Be polite, with only a little warmth.'), (6, 'Be friendly and kind.'), (9, 'Be very warm and encouraging.'), (10, 'Be as warm and caring as you can.')),
    'blunt_level': ((0, 'Soften everything and cushion bad news.'), (3, 'Be gentle but clear.'), (6, 'Be direct: say it plainly, kindly.'), (9, 'Be very blunt: no padding, no hedging.'), (10, 'Be brutally honest, never rude.')),
}


def describe(name, level):
    """One sentence for one setting: the entry with the highest threshold that is <= level (level 0 = the first)."""
    lv = clamp(level, 0)
    for upto, txt in _LINES[name]:   # (highest level this line covers, text)
        if lv <= upto:
            return txt
    return _LINES[name][-1][1]


def prompt_block(levels, key=None, fixed_rule=None):
    """Short instruction block for the persona prompt."""
    lv = apply_caps(key, {n: clamp((levels or {}).get(n), GENERIC[n]) for n in NAMES}) if key else {n: clamp((levels or {}).get(n), GENERIC[n]) for n in NAMES}
    lines = ['Behaviour settings (scale 0 to 10, set by the owner; follow them):']
    for n in NAMES:
        lines.append('- %s %d/10: %s' % (n[:-6], lv[n], describe(n, lv[n])))
    rule = fixed_rule if isinstance(fixed_rule, str) and fixed_rule.strip() else (FIXED_RULES.get(str(key).lower()) if key else None)
    if rule:
        lines.append('- Fixed rule: ' + rule.strip())
    return '\n'.join(lines)
