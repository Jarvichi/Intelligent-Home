"""birthday_reminder: once a day at a set time, DM a Discord user about birthdays coming up N days ahead (default 7, then 3).
All the birthdays of one send day go into one message. Every house value comes from settings (JSON in plugins[].settings):
  user_id       Discord user id to DM
  at            time of day HH:MM (default 18:00)
  leads         days ahead to remind (default [7, 3])
  birthdays     list of {"name": "...", "date": "YYYY-MM-DD" (age shown) or "MM-DD", "group": "family" | "work" | ...}
  group_labels  {"family": "Family", "work": "Work"}: the line label per group, in this order
  token_option  name of the add-on option holding the bot token (default discord_token)
Dry: logs the message it would send; no DM, no state change."""
import datetime as dt

NAME = 'birthday_reminder'
SCHEDULE = {'kind': 'daily', 'at': '18:00'}
DEFAULTS = {'user_id': '', 'leads': [7, 3], 'birthdays': [], 'group_labels': {'family': 'Family', 'work': 'Work'},
            'token_option': 'discord_token'}


def parse(birthdays):
    """-> [(name, month, day, year or None, group)]. Bad rows dropped."""
    out = []
    for b in birthdays or []:
        try:
            name, parts = str(b['name']).strip(), str(b['date']).strip().split('-')
            y = int(parts[0]) if len(parts) == 3 else None
            mo, d = int(parts[-2]), int(parts[-1])
            dt.date(2000, mo, d)
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        if name:
            out.append((name, mo, d, y, str(b.get('group') or 'family')))
    return out


def on(day, ppl):
    res = []
    for name, mo, d, y, grp in ppl:
        if (mo, d) == (day.month, day.day):
            res.append((name, y, grp))
        elif (mo, d) == (2, 29) and (day.month, day.day) == (2, 28) and day.year % 4:
            res.append((name, y, grp))
    return res


def message_for(send_day, ppl, leads=(7, 3), labels=None):
    labels = labels or DEFAULTS['group_labels']
    order = list(labels)
    parts = []
    for lead in leads:
        target = send_day + dt.timedelta(days=int(lead))
        items = on(target, ppl)
        if not items:
            continue
        dn = target.strftime('%A %-d %B')
        when = 'in a week, on %s' % dn if int(lead) == 7 else 'in %d days, on %s' % (int(lead), dn)
        lines = ['Heads up: a birthday %s.' % when if len(items) == 1 else 'Heads up: birthdays %s.' % when]
        groups = sorted({g for _, _, g in items}, key=lambda g: (order.index(g) if g in order else len(order), g))
        for g in groups:
            names = sorted((n, y) for n, y, gg in items if gg == g)
            txt = ['%s (turning %d)' % (n, target.year - y) if y else n for n, y in names]
            lines.append('%s: %s' % (labels.get(g, g.capitalize()), ', '.join(txt)))
        parts.append('\n'.join(lines))
    return '\n\n'.join(parts) or None


def run(ctx):
    s = ctx.settings
    if not s['user_id'] or not s['birthdays']:
        ctx.log('not configured (needs user_id and birthdays in settings)')
        return False
    today = ctx.now.date()
    st = ctx.state.load('state')
    if st.get('last_day') == today.isoformat():
        ctx.log('already done today, skipped')
        return True
    msg = message_for(today, parse(s['birthdays']), s['leads'], s['group_labels'])
    if not msg:
        ctx.log('no birthdays %s days ahead' % '/'.join(str(x) for x in s['leads']))
        if not ctx.dry:
            ctx.state.save('state', {'last_day': today.isoformat()})
        return True
    if not ctx.discord.dm(s['user_id'], msg, token_option=s['token_option']):
        return True   # dry run (logged by the helper)
    ctx.state.save('state', {'last_day': today.isoformat()})
    ctx.log('sent birthday reminder')
    return True
