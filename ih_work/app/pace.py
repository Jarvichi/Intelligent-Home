"""Weekly usage pace, the same number as the HA pace sensor: pace = week used % minus week elapsed %.
Week start = weekly reset time minus 7 days. Reads three Home Assistant sensors (week used %, 5-hour session used %, weekly reset time).
An empty entity option means: find the one sensor whose id starts with sensor.claude_usage_ and ends with _week_usage / _session_usage / _weekly_reset_time."""
from datetime import datetime, timedelta, timezone

WEEK = timedelta(days=7)
SUFFIX = {'week': '_week_usage', 'session': '_session_usage', 'reset': '_weekly_reset_time'}
BAD = (None, 'unknown', 'unavailable', '')


def compute(used, reset, now):
    """-> (pace, elapsed_percent). reset and now are timezone-aware datetimes."""
    elapsed = max(0.0, min(100.0, (now - (reset - WEEK)) / WEEK * 100))
    return round(used - elapsed, 2), round(elapsed, 2)


def _num(v):
    try:
        return float(v) if v not in BAD else None
    except (TypeError, ValueError):
        return None


class PaceReader:
    def __init__(self, ha, week_entity='', session_entity='', reset_entity=''):
        self.ha = ha
        self.ids = {'week': week_entity, 'session': session_entity, 'reset': reset_entity}

    def _discover(self):
        states = self.ha.get('states') or []
        names = [s.get('entity_id', '') for s in states if isinstance(s, dict)]
        for key, suffix in SUFFIX.items():
            if not self.ids[key]:
                found = [e for e in names if e.startswith('sensor.claude_usage_') and e.endswith(suffix) and not e.endswith('_limit_usage')]
                if len(found) == 1:
                    self.ids[key] = found[0]
        return states

    def read(self, now=None):
        """-> dict(pace, elapsed, week, session, reset). Raises on unreadable week or reset."""
        now = now or datetime.now(timezone.utc)
        if not all(self.ids.values()):
            self._discover()
        if not self.ids['week'] or not self.ids['reset']:
            raise ValueError('usage sensors not found')
        get = lambda k: (self.ha.get('states/' + self.ids[k]) or {}).get('state') if self.ids[k] else None
        week, reset, session = _num(get('week')), get('reset'), _num(get('session'))
        if week is None or reset in BAD:
            raise ValueError('usage sensors not readable')
        reset_dt = datetime.fromisoformat(str(reset).replace('Z', '+00:00'))
        if reset_dt.tzinfo is None:
            reset_dt = reset_dt.replace(tzinfo=timezone.utc)
        pace, elapsed = compute(week, reset_dt, now)
        return dict(pace=pace, elapsed=elapsed, week=week, session=session, reset=reset_dt.isoformat(timespec='seconds'))
