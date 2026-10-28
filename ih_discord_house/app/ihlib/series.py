"""Turn Home Assistant REST history rows into plain time series, and slice them. Pure functions, stdlib only.
A series is a list of (unix_ts, value) sorted by time. Rows with state unknown/unavailable (or a missing attribute) are skipped."""
import bisect
from datetime import datetime, timezone

BAD = (None, 'unknown', 'unavailable')


def ts_of(text):
    """Unix seconds of an ISO text (naive = UTC)."""
    t = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()


def series(rows, attr=None):
    """rows = ctx.ha.history(...) result. value = the state text, or attribute `attr` when given."""
    out = []
    for r in rows or []:
        v = (r.get('attributes') or {}).get(attr) if attr else r.get('state')
        if v in BAD:
            continue
        try:
            out.append((ts_of(r.get('last_updated') or r.get('last_changed')), v))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x[0])
    return out


def num(rs):
    """Numeric series; values that are not numbers are dropped."""
    out = []
    for t, v in rs:
        try:
            out.append((t, float(v)))
        except (TypeError, ValueError):
            pass
    return out


def window(rs, t0, t1):
    """Rows with t0 <= ts < t1, plus the value in force at t0 as (t0, value) at the front (like a recorder query would give)."""
    ts = [x[0] for x in rs]
    i0, i1 = bisect.bisect_left(ts, t0), bisect.bisect_left(ts, t1)
    out = list(rs[i0:i1])
    if i0 > 0:
        out.insert(0, (t0, rs[i0 - 1][1]))
    return out


def value_at(rs, t):
    """Value in force at time t (last row with ts <= t), or None."""
    v = None
    for ts, x in rs:
        if ts <= t:
            v = x
        else:
            break
    return v
