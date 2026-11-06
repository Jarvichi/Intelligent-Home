"""Dated reminders (birthdays and the like) for one persona. Pure functions, no I/O. 0.13.0.
A date line is text:  'YYYY-MM-DD|Name|family'  (a birth date: the age is shown)  or  'MM-DD|Name|other'  (no year).
kind is 'family' or anything else (shown as 'Other')."""
import datetime, re

LINE = re.compile(r'^(?:(\d{4})-)?(\d{2})-(\d{2})\|([^|]{1,60})\|([a-z]{1,20})$')


def parse(lines):
    """-> [(name, month, day, year or None, kind)], ignoring bad lines."""
    out = []
    for ln in lines or []:
        m = LINE.match(str(ln).strip())
        if not m:
            continue
        y, mo, d = (int(m.group(1)) if m.group(1) else None), int(m.group(2)), int(m.group(3))
        try:
            datetime.date(2024, mo, d)
        except ValueError:
            continue
        out.append((m.group(4).strip(), mo, d, y, m.group(5)))
    return out


def leads(text, default=(7, 3)):
    try:
        v = tuple(sorted({int(x) for x in str(text).split(',') if x.strip() and 0 <= int(x) <= 60}, reverse=True))
        return v or default
    except ValueError:
        return default


def on(day, people):
    res = []
    for name, mo, d, y, kind in people:
        if (mo, d) == (day.month, day.day) or ((mo, d) == (2, 29) and (day.month, day.day) == (2, 28) and day.year % 4):
            res.append((name, y, kind))
    return res


def due(today, people, lead_days):
    """-> (suggested_text, facts) for reminders to hand over on `today`, or None."""
    parts, names, got = [], [], []
    for lead in lead_days:
        target = today + datetime.timedelta(days=lead)
        items = on(target, people)
        if not items:
            continue
        items.sort(key=lambda i: (i[2] != 'family', i[0]))
        fam = ['%s (turning %d)' % (n, target.year - y) if y else n for n, y, k in items if k == 'family']
        oth = [n for n, y, k in items if k != 'family']
        dn = target.strftime('%A %-d %B')
        when = ('today' if lead == 0 else 'in a week, on %s' % dn if lead == 7 else 'in %d days, on %s' % (lead, dn))
        lines = ['Heads up: %s %s.' % ('a birthday' if len(items) == 1 else 'birthdays', when)]
        if fam:
            lines.append('Family: ' + ', '.join(fam))
        if oth:
            lines.append('Other: ' + ', '.join(oth))
        parts.append('\n'.join(lines))
        names += fam + oth
        got.append(lead)
    if not parts:
        return None
    return '\n\n'.join(parts), {'kind': 'birthday', 'names': ', '.join(names)[:280], 'days_ahead': ','.join(str(g) for g in sorted(set(got)))}
