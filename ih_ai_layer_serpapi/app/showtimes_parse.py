"""Parsers for SerpApi Google results. Kept apart so they can be fixed without touching anything else.
UNVERIFIED LAYOUT: the field names below ('showtimes' -> 'theaters' -> 'showing' -> 'time', and 'organic_results' title/snippet/link) come from an
older prototype and from memory, NOT from a checked live response. The tests use SAMPLE JSON written by hand. When a real response is seen,
adjust parse_showtimes and its tests. Every function tolerates missing fields and returns [] rather than raising."""


def parse_showtimes(data, cinema=None):
    """-> list of dicts {day, date, theater, type, times} for theaters whose name contains `cinema` (case-insensitive; None = all)."""
    out = []
    if not isinstance(data, dict):
        return out
    for d in data.get('showtimes') or []:
        if not isinstance(d, dict):
            continue
        for t in d.get('theaters') or []:
            if not isinstance(t, dict):
                continue
            name = str(t.get('name') or '')
            if cinema and cinema.lower() not in name.lower():
                continue
            for s in t.get('showing') or []:
                if isinstance(s, dict):
                    out.append({'day': d.get('day'), 'date': d.get('date'), 'theater': name, 'type': s.get('type'),
                                'times': [str(x) for x in (s.get('time') or [])]})
    return out


def format_showtimes(rows, limit=12):
    if not rows:
        return None
    lines = ['%s %s, %s%s: %s' % (r['day'] or '', r['date'] or '', r['theater'], ' (%s)' % r['type'] if r['type'] else '', ', '.join(r['times']) or 'no times')
             for r in rows[:limit]]
    return '\n'.join(l.strip() for l in lines)


def parse_organic(data, limit=3):
    """-> list of dicts {title, snippet, link} from data['organic_results']."""
    out = []
    if not isinstance(data, dict):
        return out
    for r in data.get('organic_results') or []:
        if isinstance(r, dict) and r.get('title'):
            out.append({'title': str(r['title']), 'snippet': str(r.get('snippet') or ''), 'link': str(r.get('link') or '')})
        if len(out) >= limit:
            break
    return out


def format_organic(rows):
    if not rows:
        return None
    return '\n'.join('%d. %s - %s %s' % (i + 1, r['title'], r['snippet'], r['link']) for i, r in enumerate(rows)).replace('  ', ' ')
