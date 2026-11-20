"""Pure logic for ih_room_insights: warm-up learning, frozen-valve check, mould risk. No network, no HA calls.
A history sample is a dict: {'t': datetime (UTC), 'cur': float|None, 'target': float|None, 'action': str|None, 'state': str}."""
import statistics
from datetime import datetime, timedelta, timezone

MIN_RUN_MIN = 20          # a heating run shorter than this says little
MIN_RISE = 0.5            # degrees: a run that rose less is ignored
MAX_GAP_MIN = 30          # a hole in the readings this long ends a run
MIN_PER_DEG_RANGE = (3.0, 400.0)   # outside this a run is not a real warm-up (open window, bad reading)
FROZEN_HOURS = 6
MOULD_PCT = 70.0
MOULD_HOURS = 2.0


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _t(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v).replace('Z', '+00:00'))


def samples_from_history(rows):
    """Raw HA history rows (REST) -> samples. Rows without a time are dropped."""
    out = []
    for r in rows or []:
        ts = r.get('last_updated') or r.get('last_changed')
        if not ts:
            continue
        a = r.get('attributes') or {}
        out.append({'t': _t(ts), 'cur': _f(a.get('current_temperature')), 'target': _f(a.get('temperature')),
                    'action': a.get('hvac_action'), 'state': r.get('state')})
    out.sort(key=lambda s: s['t'])
    return out


def warmup_runs(samples):
    """Runs where the valve was heating and the room rose -> list of {start, minutes, rise, min_per_deg}.
    A run = consecutive samples with hvac_action 'heating'. Its rise is the highest reading minus the first reading."""
    runs, cur_run = [], []

    def close():
        if len(cur_run) >= 2:
            first, last = cur_run[0], cur_run[-1]
            top = max(s['cur'] for s in cur_run)
            mins = (last['t'] - first['t']).total_seconds() / 60.0
            rise = top - first['cur']
            if mins >= MIN_RUN_MIN and rise >= MIN_RISE:
                mpd = mins / rise
                if MIN_PER_DEG_RANGE[0] <= mpd <= MIN_PER_DEG_RANGE[1]:
                    runs.append({'start': first['t'].isoformat(), 'minutes': round(mins, 1), 'rise': round(rise, 2),
                                 'min_per_deg': round(mpd, 1)})
        cur_run.clear()

    prev = None
    for s in samples:
        if s['cur'] is None or s['state'] in ('unavailable', 'unknown'):
            continue
        if prev is not None and (s['t'] - prev['t']).total_seconds() > MAX_GAP_MIN * 60 and cur_run:
            close()
        if s['action'] == 'heating':
            cur_run.append(s)
        else:
            close()
        prev = s
    close()
    return runs


def median_min_per_deg(values, keep=20):
    vals = [v for v in values if v]
    return round(statistics.median(vals[-keep:]), 1) if vals else None


def minutes_to_target(cur, target, mpd):
    """Minutes from now to reach target. 0 when already there, None when nothing is known."""
    if cur is None or target is None or not mpd:
        return None
    return 0 if cur >= target else int(round((target - cur) * mpd))


def frozen_check(samples, now, hours=FROZEN_HOURS):
    """-> (suspect bool, reason). Suspect = the reading has not changed for `hours` while the valve is calling for heat
    (or is well below its target), or the valve has been unavailable that long."""
    real = [s for s in samples if s['cur'] is not None]
    if samples and samples[-1]['state'] in ('unavailable', 'unknown'):
        gone = samples[-1]['t']
        for s in reversed(samples):
            if s['state'] in ('unavailable', 'unknown'):
                gone = s['t']
            else:
                break
        if now - gone >= timedelta(hours=hours):
            return True, 'unavailable for %.0f h' % ((now - gone).total_seconds() / 3600)
        return False, ''
    if not real:
        return False, 'no readings'
    last = real[-1]
    since = last['t']
    for s in reversed(real):
        if s['cur'] == last['cur']:
            since = s['t']
        else:
            break
    # a quiet room that is not heating is normal: only suspect while heat is wanted
    wants = last['action'] == 'heating' or (last['target'] is not None and last['target'] - last['cur'] >= 1.0)
    first_seen = real[0]['t']
    covered = now - first_seen >= timedelta(hours=hours)
    if wants and covered and now - since >= timedelta(hours=hours):
        return True, 'reading stuck at %.1f for %.1f h while heat is wanted' % (last['cur'], (now - since).total_seconds() / 3600)
    return False, ''


def mould_check(samples, now, pct=MOULD_PCT, hours=MOULD_HOURS):
    """Humidity samples ({'t','value'}) -> {'level': ok|watch|high, 'minutes_above', 'now'}.
    high = above `pct` for `hours` without a break up to now; watch = above now but not that long."""
    vals = [s for s in samples if s.get('value') is not None]
    if not vals:
        return {'level': 'unknown', 'minutes_above': 0, 'now': None}
    last = vals[-1]['value']
    if last <= pct:
        return {'level': 'ok', 'minutes_above': 0, 'now': last}
    start = vals[-1]['t']
    for s in reversed(vals):
        if s['value'] > pct:
            start = s['t']
        else:
            break
    mins = (now - start).total_seconds() / 60.0
    return {'level': 'high' if mins >= hours * 60 else 'watch', 'minutes_above': int(mins), 'now': last}


def humidity_samples(rows):
    out = []
    for r in rows or []:
        ts = r.get('last_updated') or r.get('last_changed')
        v = _f(r.get('state'))
        if ts and v is not None:
            out.append({'t': _t(ts), 'value': v})
    out.sort(key=lambda s: s['t'])
    return out


def slug(entity):
    s = entity.split('.', 1)[-1]
    for cut in ('_thermostat_thermostat', '_thermostat', '_radiator_thermostat', '_humidity'):
        if s.endswith(cut) and len(s) > len(cut):
            s = s[:-len(cut)]
            break
    return s
