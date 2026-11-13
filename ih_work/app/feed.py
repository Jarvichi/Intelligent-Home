"""Backlog feeder rules (pure): which open item of the Claude to-do list is queued next. Replaces the nightly worker's picking.
Rules: never an item fed in the last 7 days; never an item tagged waiting / needs OK / plan / no-overnight / with an open [needs-you #N] (cleared by [done-you #N]); only items whose title starts with an
allowed work prefix (option feed_prefixes) or that carry the tag [claude] or [approved #N]; approved items first, then earliest due date
(no date last), then the item worked on least (fewest '[overnight ' lines), then list order."""
import re
from datetime import datetime, timedelta, timezone

FEED_AGAIN_DAYS = 7
SKIP_TAG = re.compile(r'\[(needs-ok[^\]]*|no-overnight|waiting[^\]]*|plan[^\]]*|needs ok[^\]]*)\]', re.I)
NEEDS_YOU = re.compile(r'\[needs-you #(\d+)\]', re.I)   # 0.5.3: only the owner can do it; cleared by a matching [done-you #N]
APPROVED = re.compile(r'\[approved #\d+\]', re.I)
WORK_TAG = re.compile(r'\[claude\]', re.I)


def open_needs_you(text):
    """True if text has a [needs-you #N] marker without a matching [done-you #N ...] (the owner has not done it yet)."""
    text = text or ''
    return any(not re.search(r'\[done-you #%s[ \]]' % n, text, re.I) for n in NEEDS_YOU.findall(text))


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
        if _recent(fed, i['uid'], now) or SKIP_TAG.search(summary + '\n' + desc) or open_needs_you(desc):
            continue
        approved = bool(APPROVED.search(desc))
        if not (any_item or approved or WORK_TAG.search(desc) or (pre and summary.strip().lower().startswith(pre))):
            continue
        (appr if approved else rest).append((_due(i), desc.count('[overnight '), n, i))
    for group, flag in ((appr, True), (rest, False)):
        if group:
            return min(group, key=lambda g: g[:3])[3], flag
    return None, False


HAS_PLAN = re.compile(r'PLAN|AI job|BUILT', re.I)   # same markers the free-AI planning step looks for


REPLAN = re.compile(r'\[replan\]', re.I)   # 0.6.1: a reviewer graded the outside-AI plan wrong; Claude must write a corrected one (the line is replaced by "PLAN (Claude, replanned): <path>")


def has_plan(desc):
    """True if the description already carries a usable plan. An open [replan] marker means NO usable plan (its 'old plan' text must not count)."""
    desc = str(desc or '')
    return not REPLAN.search(desc) and bool(HAS_PLAN.search(desc))


def instruction(item):
    desc = str(item.get('description') or '')
    if REPLAN.search(desc):
        plan = ('\nREPLAN: an earlier plan for this item was graded wrong by a reviewer (reason in the [replan] line). First run the already-done check; '
                'if the work already exists, report already_done with evidence. Otherwise write a corrected plan note in the brain (projects/<slug>/plan, overwrite or version the old one, '
                'keep it short: goal, steps, risks, test, effort), replace the [replan] line with a line "PLAN (Claude, replanned): <note path>", '
                'then carry on with the item as usual (read-only unless approved).\n')
    else:
        plan = _no_plan(desc)
    return ('Work on this item of the Claude to-do list. Read-only unless the item is marked approved.\nTitle: %s\nDue: %s\n%sDescription:\n%s'
            % (str(item.get('summary'))[:200], str(item.get('due') or 'none'), plan, desc[:3000]))[:4000]


def _no_plan(desc):
    plan = ('' if has_plan(desc) else
            '\nNO PLAN YET: the free outside AIs did not give this item a plan (not asked, refused, or out of quota). Before anything else, write one: '
            'a short plan note in the brain (projects/<slug>/plan, with goal, steps, risks, test, effort) and add a line "PLAN (Claude): <note path>" to the item. '
            'Then carry on with the item as usual (read-only unless approved). Private items (money, health, people) are planned here, never sent out.\n')
    return plan
