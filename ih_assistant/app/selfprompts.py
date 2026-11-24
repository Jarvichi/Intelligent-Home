"""Self prompts (0.25.0): pure helpers for the private maintenance prompts a persona is asked now and then.
No I/O except load(). The add-on decides WHEN (tick_self in main.py); this file decides WHICH prompt and how many a week.
autonomy_level 0..10: 0 = off. Prompts per week is about 1 at level 3, 3 at level 5, 7 at level 10."""
import json, os

WEEK_CAP = {0: 0, 1: 1, 2: 1, 3: 1, 4: 2, 5: 3, 6: 4, 7: 5, 8: 6, 9: 7, 10: 7}
DEFAULT_LEVEL = 3
DAY_S = 86400
MIN_GAP_S = 12 * 3600          # never two prompts closer than this
QUIET_AFTER_CONTACT_S = 2 * 3600   # not within 2 h of a check-in or reminder
ACTIONS = ('none', 'setting', 'memory', 'message', 'identity', 'other')
LOG_KEEP = 200
OUTCOME_MAX = 160
CHANGE_WINDOW_S = 30 * DAY_S    # nickname and picture: one change a month each
SELF_WINDOW_S = 6 * 3600        # an open self prompt: identity changes inside it count against the monthly cap


def load(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'self_prompts.json')
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    rules = d.get('rules') if isinstance(d.get('rules'), str) else ''
    prompts = [p for p in d.get('prompts', []) if isinstance(p, dict) and isinstance(p.get('id'), str) and isinstance(p.get('text'), str)]
    return rules, prompts


def week_cap(level):
    try:
        lv = int(level)
    except (TypeError, ValueError):
        return 0
    return WEEK_CAP.get(max(0, min(10, lv)), 0)


def daily_chance(level):
    """Chance that today is a prompt day: cap/7. One roll per day, so a missed day is simply dropped."""
    return week_cap(level) / 7.0


def fired_in(log, now, span_s):
    return [e for e in log if isinstance(e, dict) and isinstance(e.get('at'), (int, float)) and now - e['at'] < span_s]


def eligible(prompts, level, log, now):
    """Prompts allowed at this level whose cooldown has passed (oldest-asked first, then catalogue order)."""
    last = {}
    for e in log:
        if isinstance(e, dict) and isinstance(e.get('at'), (int, float)) and isinstance(e.get('prompt_id'), str):
            last[e['prompt_id']] = max(last.get(e['prompt_id'], 0), e['at'])
    out = []
    for i, p in enumerate(prompts):
        if int(p.get('min_level', 1)) > level:
            continue
        if now - last.get(p['id'], 0) < int(p.get('cooldown_days', 7)) * DAY_S:
            continue
        out.append((last.get(p['id'], 0), i, p))
    out.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in out]


def render(text, user, persona):
    return text.replace('{user}', user).replace('{persona}', persona)


def clean_outcome(v):
    return ' '.join(str(v or '').split())[:OUTCOME_MAX] or 'nothing needed'
