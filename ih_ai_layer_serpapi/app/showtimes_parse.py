"""Parsers for SerpApi Google results. Kept apart so they can be fixed without touching anything else.
Layout CHECKED against one real response (27 Oct 2026, query "showtimes <cinema>", gl=uk): 'showtimes' is a list of days
{day, movies: [{name, link, showing: [{time: [..], type}]}]}; the cinema is the one the query names (knowledge_graph title), not a field of each row.
organic_results (title/snippet/link) are not yet checked against a real response. Every function tolerates missing fields and returns [] rather than raising."""


def parse_showtimes(data, film=None):
    """-> list of dicts {day, movie, type, times} for every film whose name contains `film` (case-insensitive; None = all films)."""
    out = []
    if not isinstance(data, dict):
        return out
    for d in data.get('showtimes') or []:
        if not isinstance(d, dict):
            continue
        for m in d.get('movies') or []:
            if not isinstance(m, dict):
                continue
            name = str(m.get('name') or '')
            if film and film.lower() not in name.lower():
                continue
            for s in m.get('showing') or []:
                if isinstance(s, dict):
                    out.append({'day': d.get('day'), 'movie': name, 'type': s.get('type'), 'times': [str(x) for x in (s.get('time') or [])]})
    return out


def format_showtimes(rows, limit=12):
    if not rows:
        return None
    lines = ['%s: %s%s: %s' % (r['day'] or '', r['movie'], ' (%s)' % r['type'] if r['type'] and r['type'] != 'Standard' else '', ', '.join(r['times']) or 'no times')
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
