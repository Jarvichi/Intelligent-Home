"""Builds the read-only 'Today' card for the ingress status page from the backup sensors this add-on sets (sensor.backup_storage_local / _drive)
and Home Assistant's own last/next automatic backup sensors.

Only fixed fields are copied out (numbers and short fixed text): nothing from a sensor is passed through, so no tokens, URLs or names can leak.
"""
import time
from datetime import datetime, timezone

TITLE = 'Today'
SUBTITLE = 'Backup space and the last automatic backup'
CACHE_S = 5
STALE_S = 3 * 3600          # a storage figure older than this is shown as 'old'
LATE_S = 48 * 3600          # no automatic backup for this long is shown as 'late'
LOCAL, DRIVE = 'sensor.backup_storage_local', 'sensor.backup_storage_drive'
LAST, NEXT = 'sensor.backup_last_successful_automatic_backup', 'sensor.backup_next_scheduled_automatic_backup'
_cache = {'t': 0.0, 'v': None}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v >= 0 else None


def _when(text):
    """ISO text -> aware datetime, or None."""
    try:
        d = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def span(seconds):
    s = int(abs(seconds))
    if s < 90:
        return 'under 2 min'
    if s < 5400:
        return '%d min' % round(s / 60)
    if s < 2 * 86400:
        return '%d h' % round(s / 3600)
    return '%d days' % round(s / 86400)


def _gb(v):
    return ('%.1f' % v)


def _storage(name, st, now):
    """One disk row from a storage sensor state dict (or None)."""
    if not isinstance(st, dict):
        return None
    a = st.get('attributes') if isinstance(st.get('attributes'), dict) else {}
    used, total, pct = _num(_float(st.get("state"))), _num(a.get('total_gb')), _num(a.get('used_pct'))
    back, count = _num(a.get('backups_gb')), _num(a.get('backups_count'))
    row = {'name': name}
    if back is not None:
        row['model'] = ('%d backups, ' % count if count is not None else '') + _gb(back) + ' GB'
    if used is not None and total and a.get('disk_known') is not False:
        row['value'] = '%d%%' % round(pct if pct is not None else 100 * used / total)
        row['pct'] = min(100.0, pct if pct is not None else 100 * used / total)
        row['note'] = '%s of %s GB used' % (_gb(used), _gb(total))
        row['state'] = 'nearly full' if row['pct'] >= 90 else 'ok'
    elif back is not None:
        row['plain'] = True
        row['note'] = 'Disk size not known'
    else:
        return None
    upd = _when(a.get('updated'))
    if upd and (now - upd.timestamp()) > STALE_S:
        row['state'] = 'old'
    return row


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build(states, now=None):
    """states = {entity: HA state dict or None} -> payload dict for api/today, or None when there is nothing to show."""
    if not isinstance(states, dict):
        return None
    now = time.time() if now is None else now
    rows = []
    local = _storage('Local disk', states.get(LOCAL), now)
    drive = _storage('Google Drive', states.get(DRIVE), now)
    if local:
        rows.append(local)
    if drive:
        rows.append(drive)
    elif local:
        rows.append({'name': 'Google Drive', 'plain': True, 'note': 'Not set up', 'state': 'off'})
    last = states.get(LAST)
    last_t = _when(last.get('state')) if isinstance(last, dict) else None
    if last_t:
        nxt = states.get(NEXT)
        nxt_t = _when(nxt.get('state')) if isinstance(nxt, dict) else None
        age = now - last_t.timestamp()
        row = {'name': 'Last automatic backup', 'value': span(age) + ' ago' if age >= 0 else 'just now', 'state': 'late' if age > LATE_S else 'ok'}
        if nxt_t and nxt_t.timestamp() > now:
            row['note'] = 'Next: in ' + span(nxt_t.timestamp() - now)
        rows.append(row)
    elif local or drive:
        rows.append({'name': 'Last automatic backup', 'plain': True, 'note': 'Not reported by Home Assistant'})
    if not rows:
        return None
    return {'title': TITLE, 'subtitle': SUBTITLE, 'rows': rows}


def fetch_states(ha=None):
    """Reads the four sensors with the add-on's own Supervisor token (never output). A sensor that cannot be read is None."""
    if ha is None:
        from ihlib.ha import HA
        ha = HA()
    out = {}
    for e in (LOCAL, DRIVE, LAST, NEXT):
        try:
            out[e] = ha.get('states/' + e, 3)
        except Exception:
            out[e] = None
    return out


def today(fetch=fetch_states, clock=time.time):
    """Cached (<= 5 s) payload, or None when nothing can be read."""
    now = clock()
    if _cache['v'] is not None and now - _cache['t'] < CACHE_S:
        return _cache['v']
    try:
        v = build(fetch(), now)
    except Exception:
        v = None
    _cache['t'], _cache['v'] = now, v
    return v
