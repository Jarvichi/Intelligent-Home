"""Shared pieces of the game_art_search capability (SerpApi, Serper, Steam Store).
Request context from the caller: {title, platform, domains}. `domains` is the list of sites the caller accepts art from; providers keep only
results whose source page is on one of them, and fail with why='empty' when nothing is left so the router can try the next provider."""
import hashlib, re, urllib.parse

ART_SUFFIX = ' official video game box art cover'     # added to the search text so image search favours box art
MAX_CANDIDATES = 10          # image candidates one provider returns
MAX_TITLE_CHARS = 160        # game title kept in a query
MAX_PLATFORM_CHARS = 20      # platform kept in a query
MAX_PLAIN_QUERY_CHARS = 220  # plain request text used when no title is given
MAX_DOMAINS = 60             # accepted-site entries taken from the caller
MAX_DOMAIN_CHARS = 80
MAX_ROW_TITLE_CHARS = 180
MAX_URL_CHARS = 1200
MAX_SOURCE_NAME_CHARS = 100
KEY_HASH_CHARS = 24          # length of the cache key
DOMAIN_RE = re.compile(r'[a-z0-9][a-z0-9.-]*')
URL_PREFIXES = ('http://', 'https://')


def art_query(text, ctx):
    ctx = ctx or {}
    if not ctx.get('title'):
        return text[:MAX_PLAIN_QUERY_CHARS]
    return ('%s %s' % (str(ctx['title'])[:MAX_TITLE_CHARS].strip(), str(ctx.get('platform') or '').strip()[:MAX_PLATFORM_CHARS])).strip()


def clean_context(raw):
    """Context a scoped (Games) caller may pass: plain title and platform strings and a list of accepted domains. Anything else is dropped."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key, limit in (('title', MAX_TITLE_CHARS), ('platform', MAX_PLATFORM_CHARS)):
        if isinstance(raw.get(key), str) and raw[key].strip():
            out[key] = raw[key].strip()[:limit]
    doms = raw.get('domains')
    if isinstance(doms, list):
        keep = [d.strip().lower() for d in doms[:MAX_DOMAINS] if isinstance(d, str) and len(d.strip()) <= MAX_DOMAIN_CHARS and DOMAIN_RE.fullmatch(d.strip().lower())]
        if keep:
            out['domains'] = keep
    return out


def make_row(title, image_url, source_url, source=''):
    return {'title': str(title or '')[:MAX_ROW_TITLE_CHARS], 'image_url': str(image_url)[:MAX_URL_CHARS],
            'source_url': str(source_url)[:MAX_URL_CHARS], 'source': str(source or '')[:MAX_SOURCE_NAME_CHARS]}


def keep_accepted(rows, ctx):
    """Rows whose source page is on an accepted domain (https). No domains in the context = no filtering."""
    doms = (ctx or {}).get('domains')
    if not doms:
        return rows
    out = []
    for r in rows:
        try:
            u = urllib.parse.urlsplit(r['source_url'])
        except ValueError:
            continue
        host = (u.hostname or '').rstrip('.').lower()
        if u.scheme == 'https' and any(host == d or host.endswith('.' + d) for d in doms):
            out.append(r)
    return out


def cache_key(cap, query, ctx=None):
    parts = [cap, query.lower(), ','.join((ctx or {}).get('domains') or [])]
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:KEY_HASH_CHARS]
