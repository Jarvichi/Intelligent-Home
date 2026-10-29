"""iPhone shopping Shortcut endpoint (port of the old HA webhook, same request and reply formats).
POST /shortcut/<token> on the add-on port. Body: JSON {"items": [...] or "a\\nb"}, form items=a\\nb, or plain text one item per line.
Reply 200 {"status":"ok","added":[...],"duplicates_skipped":N,"total_items":N}; 400 {"error":...}. The Shortcut ticks its Reminders only when it
gets this reply, so the shape must not change. In DRY mode (default) nothing is written to the list; the reply says what would be added and has "dry": true.
Logs counts only, never item text. stdlib only. The HA object needs get_items(entity) and call(domain, service, data)."""
import hmac, json, logging
from urllib.parse import parse_qs

MAX_BODY = 65536
LG = logging.getLogger('ihshopping')


def parse_items(content_type, raw):
    """Return (items, error). error is None or an error text for a 400."""
    ct = content_type or ''
    text = raw.decode('utf-8', errors='replace')
    if 'json' in ct:
        try:
            v = json.loads(text).get('items')
        except Exception:
            return [], 'invalid JSON'
        items = v if isinstance(v, list) else str(v or '').splitlines()
    elif 'form' in ct:
        q = parse_qs(text)
        items = (q.get('items') or next(iter(q.values()), ['']))[0].splitlines()
    else:
        items = text.splitlines()
    new, seen = [], set()
    for i in items:
        i = str(i).strip()
        if i and i.lower() not in seen:
            seen.add(i.lower()); new.append(i)
    if not new:
        return [], 'no non-empty items provided'
    return new, None


def token_ok(configured, given):
    return bool(configured) and bool(given) and hmac.compare_digest(configured.encode(), given.encode())


def handle(content_type, raw, ha, entity, dry):
    """Do the work. Returns (http_status, reply_dict)."""
    new, err = parse_items(content_type, raw)
    if err:
        return 400, {'error': err}
    try:
        have = {x['summary'].lower() for x in ha.get_items(entity)}
        added = [i for i in new if i.lower() not in have]
        if not dry:
            for i in added:
                ha.call('todo', 'add_item', {'entity_id': entity, 'item': i})
    except Exception as e:
        LG.info('shortcut: Home Assistant call failed (%s)', type(e).__name__)
        return 502, {'error': 'home assistant unavailable'}
    LG.info('shortcut%s: %d item(s) received, %d added', ' [DRY, list not changed]' if dry else '', len(new), len(added))
    reply = {'status': 'ok', 'added': added, 'duplicates_skipped': len(new) - len(added), 'total_items': len(have) + len(added)}
    if dry:
        reply['dry'] = True
    return 200, reply
