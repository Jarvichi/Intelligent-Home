"""Backlog feeder rules (pure): which open item of the Claude to-do list is queued next. Replaces the nightly worker's picking.
Rules: never an item fed in the last 7 days; never an item tagged waiting / needs OK / plan / no-overnight; only items whose title starts with an
allowed work prefix (option feed_prefixes) or that carry the tag [claude] or [approved #N]; approved items first, then earliest due date
(no date last), then the item worked on least (fewest '[overnight ' lines), then list order."""
import re
from datetime import datetime, timedelta, timezone

FEED_AGAIN_DAYS = 7
SKIP_TAG = re.compile(r'\[(needs-ok[^\]]*|no-overnight|waiting[^\]]*|plan[^\]]*|needs ok[^\]]*)\]', re.I)
APPROVED = re.compile(r'\[approved #\d+\]', re.I)
WORK_TAG = re.compile(r'\[claude\]', re.I)


def _due(item):
    d = str(item.get('due') or '')[:10]
    return d if re.match(r'^\d{4}-\d{2}-\d{2}$', d) else '9999-99-99'


def _recent(fed, uid, now):
    try:
        t = datetime.fromisoformat(fed[uid])
    except (KeyError, TypeError, ValueError):
        return False
    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return now - t < timedelta(days=FEED_AGAIN_DAYS)


def pick(items, prefixes, fed, now, any_item=False):
    """-> (item, approved) or (None, False). items = raw HA to-do items (uid, summary, description, due).
    any_item=True (runs, 0.5.1): the prefix/tag gate is off, like the old 1am worker: any open item except the skip tags and ones fed in the last 7 days."""
    pre = tuple(p.strip().lower() for p in prefixes if isinstance(p, str) and p.strip())
    appr, rest = [], []
    for n, i in enumerate(items):
        if not isinstance(i, dict) or not i.get('uid') or not i.get('summary'):
            continue
        summary, desc = str(i['summary']), str(i.get('description') or '')
        if _recent(fed, i['uid'], now) or SKIP_TAG.search(summary + '\n' + desc):
            continue
        approved = bool(APPROVED.search(desc))
        if not (any_item or approved or WORK_TAG.search(desc) or (pre and summary.strip().lower().startswith(pre))):
            continue
        (appr if approved else rest).append((_due(i), desc.count('[overnight '), n, i))
    for group, flag in ((appr, True), (rest, False)):
        if group:
            return min(group, key=lambda g: g[:3])[3], flag
    return None, False


def instruction(item):
    return ('Work on this item of the Claude to-do list. Read-only unless the item is marked approved.\nTitle: %s\nDue: %s\nDescription:\n%s'
            % (str(item.get('summary'))[:200], str(item.get('due') or 'none'), str(item.get('description') or '')[:3000]))[:4000]
