"""Check-in planner (pure functions, no network, no files). Ported from the host planner and made generic per persona.
Times are minutes after midnight in the add-on time zone. The Assistant class does the reading (calendars, meal sensor) and the storing."""
import random, re

GAP_DEFAULT_H = 3
PAD = 10              # minutes of padding around a calendar event
MEAL_DEFAULT = [(12 * 60 + 30, 13 * 60 + 30), (18 * 60, 19 * 60 + 15)]
MIN_FREE = 20         # a free window shorter than this is not used


def hm(m):
    return '%02d:%02d' % (m // 60, m % 60)


def mins(s):
    return int(s[:2]) * 60 + int(s[3:])


def meal_windows(eat_min=None):
    """Default meal windows; with a planned eat time: that time -60..+45 min replaces any default window within 90 min of it."""
    meals = list(MEAL_DEFAULT)
    if eat_min is not None:
        meals = [m for m in meals if not (m[0] <= eat_min <= m[1] or abs(m[0] - eat_min) < 90)] + [(max(0, eat_min - 60), min(24 * 60, eat_min + 45))]
    return meals


def event_window(a, b):
    return (max(0, a - PAD), min(24 * 60, b + PAD))


def free_windows(wake, busy, in_quiet=None):
    """[(a, b)] inside the wake window with busy spans cut out; windows under MIN_FREE dropped."""
    cur = [wake]
    for x, y in busy:
        nxt = []
        for p, q in cur:
            if y <= p or x >= q:
                nxt.append((p, q))
            else:
                if x > p:
                    nxt.append((p, x))
                if y < q:
                    nxt.append((y, q))
        cur = nxt
    return [w for w in cur if w[1] - w[0] >= MIN_FREE]


DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']


def parse_window(text):
    """'mon-fri 09:00-17:00' / 'sat,sun 10:00-12:00' / 'daily 12:00-13:00' -> (set of weekday numbers, start_min, end_min) or None."""
    try:
        d, t = str(text).strip().lower().split()
        a, b = t.split('-')
        if not (re.match(r'^\d{1,2}:\d{2}$', a) and re.match(r'^\d{1,2}:\d{2}$', b)):
            return None
        a, b = mins(a.zfill(5)), mins(b.zfill(5))
        if not (0 <= a < b <= 24 * 60):
            return None
        days = set()
        if d == 'daily':
            days = set(range(7))
        else:
            for part in d.split(','):
                if '-' in part:
                    x, y = part.split('-')
                    i, j = DAYS.index(x), DAYS.index(y)
                    days |= set(range(i, j + 1)) if i <= j else set(range(i, 7)) | set(range(0, j + 1))
                else:
                    days.add(DAYS.index(part))
        return days, a, b
    except (ValueError, TypeError):
        return None


def windows_for(texts, weekday):
    return [(a, b) for w in (parse_window(t) for t in texts) if w for d, a, b in [w] if weekday in d]


def subtract(spans, cuts):
    """Remove the `cuts` spans from `spans`."""
    cur = list(spans)
    for x, y in cuts:
        nxt = []
        for p, q in cur:
            if y <= p or x >= q:
                nxt.append((p, q))
            else:
                if x > p:
                    nxt.append((p, x))
                if y < q:
                    nxt.append((y, q))
        cur = nxt
    return cur


def parse_perday(v):
    """2 / '2' / '2-3' -> (lo, hi) clamped to 0..4, or None."""
    m = re.match(r'^\s*([0-4])\s*(?:-\s*([0-4]))?\s*$', str(v))
    if not m:
        return None
    lo = int(m.group(1)); hi = int(m.group(2)) if m.group(2) else lo
    return (min(lo, hi), max(lo, hi))


def perday_option(v):
    """Normalised option value: int, or 'lo-hi' text; default 2."""
    r = parse_perday(v if v is not None else 2)
    return 2 if r is None else (r[0] if r[0] == r[1] else '%d-%d' % r)


def pick_target(v, seed):
    lo, hi = parse_perday(v) or (2, 2)
    return random.Random(seed).randint(lo, hi)


def make_plan(seed, wake, busy, agreed, target, gap_min, floor, quiet_fn=None, keep=()):
    """Plan for one day. agreed: list of 'HH:MM' (kept first, always). Then random slots up to `target` in total, inside the
    free windows, >= gap_min apart from every other slot, not before `floor`, not in quiet hours. Deterministic for a seed."""
    rng = random.Random(seed)
    slots = [dict(k) for k in keep]
    slots += [{'time': t, 'kind': 'agreed', 'status': 'pending'} for t in sorted(set(agreed)) if not any(k['time'] == t for k in slots)]
    free = free_windows(wake, busy)
    tries = 0
    while len(slots) < target and tries < 300:
        tries += 1
        cand = [(max(a, floor), b) for a, b in free if b - max(a, floor) >= 15]
        if not cand:
            break
        a, b = rng.choice(cand)
        t = rng.randrange(a, b - 5)
        t -= t % 5
        if quiet_fn and quiet_fn(t):
            continue
        if all(abs(t - mins(o['time'])) >= gap_min for o in slots) and wake[0] <= t <= wake[1]:
            slots.append({'time': hm(t), 'kind': 'random', 'status': 'pending'})
    slots.sort(key=lambda x: x['time'])
    return slots
