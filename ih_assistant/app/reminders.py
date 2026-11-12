"""One-off and repeating reminders for one persona. Pure functions, no I/O. 0.14.0.
Times are local wall-clock, text 'YYYY-MM-DD HH:MM' in, naive datetimes inside (the caller compares with local now)."""
import calendar, datetime, re

REPEATS = ('none', 'daily', 'weekly', 'monthly', 'yearly')
MAX_PENDING = 50           # pending reminders per persona
MAX_TEXT = 500             # characters of reminder text
MIN_LEAD_S = 60            # a new reminder must be at least this far ahead
LATE_FLAG_S = 300          # overdue by more than this: the event is flagged late
LATE_MAX_S = 24 * 3600     # overdue by no more than this: still fires once, flagged late
DONE_KEEP_S = 7 * 86400    # finished reminders are kept this long, then pruned
WHEN_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})$')
ISO_FMT = '%Y-%m-%dT%H:%M'
SUGGESTED_MAX = 900
TRIGGERS = ('arrive_home',)              # 0.18.0: reminders that fire on an event instead of a time
ARRIVE_EXPIRY_S = 24 * 3600              # default: an arrival reminder expires after 24 h (no stale fire days later)
MAX_ARRIVE_EXPIRY_H = 168                # longest expiry a caller may ask for (hours)
PRESENCE_ON = ('on', 'home')             # presence states that mean "at home"
PRESENCE_OFF = ('off', 'not_home', 'away')   # presence states that mean "away"


def parse_local(text):
    """'YYYY-MM-DD HH:MM' (or with T) -> naive datetime, or None."""
    m = WHEN_RE.match(str(text or '').strip())
    if not m:
        return None
    try:
        return datetime.datetime(*[int(x) for x in m.groups()])
    except ValueError:
        return None


def _add_months(d, months, anchor_day):
    n = d.year * 12 + (d.month - 1) + months
    y, mo = divmod(n, 12)
    mo += 1
    return d.replace(year=y, month=mo, day=min(anchor_day, calendar.monthrange(y, mo)[1]))


def next_occurrence(due, repeat, anchor_day=None):
    """The occurrence after `due`. Month and year steps clamp to the end of a short month; anchor_day keeps the
    original day of month so 31 Jan -> 28 Feb -> 31 Mar. -> datetime, or None for repeat 'none'."""
    day = anchor_day or due.day
    if repeat == 'daily':
        return due + datetime.timedelta(days=1)
    if repeat == 'weekly':
        return due + datetime.timedelta(days=7)
    if repeat == 'monthly':
        return _add_months(due, 1, day)
    if repeat == 'yearly':
        return _add_months(due, 12, day)
    return None


def advance_past(item, now):
    """Move a repeating item's due to the first occurrence after `now`. -> True if it still has one."""
    due = parse_local(item['due'])
    anchor = int(item.get('anchor') or due.day)
    while due <= now:
        due = next_occurrence(due, item['repeat'], anchor)
        if due is None:
            return False
    item['due'] = due.strftime(ISO_FMT)
    return True


def due_items(items, now):
    """Pending items whose time has come, oldest first."""
    out = [i for i in items if i.get('status') == 'pending' and not i.get('trigger') and parse_local(i.get('due')) and parse_local(i['due']) <= now]
    return sorted(out, key=lambda i: i['due'])


def arrival_items(items, clock_now, person=None, own=None):
    """0.18.0: pending, unexpired items that wait for a person to arrive home (expires_at = epoch seconds), oldest first.
    0.19.0: with `person`, only items for that person (an item without a person is for `own`, the persona's own person)."""
    out = [i for i in items if i.get('status') == 'pending' and i.get('trigger') == 'arrive_home'
           and float(i.get('expires_at') or 0) > clock_now
           and (person is None or (i.get('person') or own) == person)]
    return sorted(out, key=lambda i: i['created'])


def presence_transition(prev, state):
    """-> (new remembered state or None, arrived). Only 'on'/'off' style states count; unknown/unavailable keep the old memory.
    arrived is True only for a known away -> home change."""
    st = str(state or '').strip().lower()
    cur = 'on' if st in PRESENCE_ON else 'off' if st in PRESENCE_OFF else None
    if cur is None:
        return prev, False
    return cur, (prev == 'off' and cur == 'on')


def overdue_s(item, now):
    return (now - parse_local(item['due'])).total_seconds()


def prune(items, clock_now):
    """Drop done items older than DONE_KEEP_S (clock_now = epoch seconds)."""
    keep = [i for i in items if not (i.get('status') == 'done' and clock_now - float(i.get('fired_at') or 0) > DONE_KEEP_S)]
    # 0.18.0: an arrival reminder that expired before the person arrived is dropped
    return [i for i in keep if not (i.get('trigger') and i.get('status') == 'pending' and float(i.get('expires_at') or 0) <= clock_now)]


def suggested(item):
    if item.get('trigger') == 'arrive_home':
        if item.get('person'):   # 0.19.0: someone else's arrival
            return ('⏰ Reminder you set (for when %s gets home): %s' % (item['person'], item['text']))[:SUGGESTED_MAX]
        return ('⏰ Reminder you set (for when you get home): %s' % item['text'])[:SUGGESTED_MAX]
    due = parse_local(item['due'])
    return ('⏰ Reminder you set (%s): %s' % (due.strftime('%a %d %b %H:%M'), item['text']))[:SUGGESTED_MAX]
