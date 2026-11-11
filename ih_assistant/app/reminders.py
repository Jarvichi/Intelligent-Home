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
    out = [i for i in items if i.get('status') == 'pending' and parse_local(i['due']) and parse_local(i['due']) <= now]
    return sorted(out, key=lambda i: i['due'])


def overdue_s(item, now):
    return (now - parse_local(item['due'])).total_seconds()


def prune(items, clock_now):
    """Drop done items older than DONE_KEEP_S (clock_now = epoch seconds)."""
    return [i for i in items if not (i.get('status') == 'done' and clock_now - float(i.get('fired_at') or 0) > DONE_KEEP_S)]


def suggested(item):
    due = parse_local(item['due'])
    return ('⏰ Reminder you set (%s): %s' % (due.strftime('%a %d %b %H:%M'), item['text']))[:SUGGESTED_MAX]
