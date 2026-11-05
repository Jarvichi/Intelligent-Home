"""Pure pace gate with two time-of-day bands and hysteresis. No clock, no files, no network: everything comes in as arguments.

pace = week used % minus week elapsed % (negative = under pace, same number as the HA pace sensor).
Each band has a START and a STOP level (start < stop):
  IDLE:     start taking work only when pace < START. Between START and STOP nothing starts.
  DRAINING: keep taking work one item at a time while pace < STOP. At STOP or higher, stop (a running item finishes) and go IDLE.
Day (default 06:00-23:00): start below -5.0, stop at -4.0.  Night (23:00-06:00): start below -1.0, stop at 0.0.
When the clock moves from one band to the other, DRAINING is dropped at once, so the next item starts only if the NEW band allows a start.
Other safeguards: 5-hour session < 85%, week < 90%, at most 3 claims an hour, STOP switch, pace unreadable = nothing runs."""
from collections import namedtuple

SESSION_STOP = 85
WEEK_STOP = 90
PER_HOUR = 3
PACE_PER_HOUR = 100 / 168     # pace falls this many points an hour with no more usage

Bands = namedtuple('Bands', 'day_start day_stop night_start night_stop night_from night_to')
DEFAULT_BANDS = Bands(-5.0, -4.0, -1.0, 0.0, '23:00', '06:00')
Decision = namedtuple('Decision', 'ok mode band reason start stop')


def minutes(hhmm, default):
    """'HH:MM' -> minutes since midnight (default on bad text)."""
    try:
        h, m = str(hhmm).strip().split(':')
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 60 + m
    except (ValueError, TypeError):
        pass
    return default


def band_of(local_dt, bands=DEFAULT_BANDS):
    """'night' or 'day' for a local (timezone-aware or naive) datetime."""
    now = local_dt.hour * 60 + local_dt.minute
    a, b = minutes(bands.night_from, 23 * 60), minutes(bands.night_to, 6 * 60)
    if a == b:
        return 'day'
    night = (a <= now < b) if a < b else (now >= a or now < b)
    return 'night' if night else 'day'


def levels(band, bands=DEFAULT_BANDS):
    """-> (start, stop) for a band. A stop that is not above the start is lifted to start + 1."""
    start, stop = (bands.night_start, bands.night_stop) if band == 'night' else (bands.day_start, bands.day_stop)
    return start, (stop if stop > start else start + 1.0)


def step(mode, pace, session, week, start, stop, band='day'):
    """-> (may_run, new_mode, reason). mode is 'idle' or 'draining'."""
    head = '%s band: pace %+.1f' % (band, pace)
    if pace >= stop:
        return False, 'idle', '%s, above %g, nothing runs' % (head, stop)
    if mode != 'draining':
        if pace >= start:
            return False, 'idle', '%s, waiting until it drops below %g' % (head, start)
        mode = 'draining'
    if session is not None and session >= SESSION_STOP:
        return False, mode, '%s, draining, paused: 5-hour session at %g%% (limit %d%%)' % (head, session, SESSION_STOP)
    if week >= WEEK_STOP:
        return False, mode, '%s, draining, paused: week at %g%% (stop %d%%)' % (head, week, WEEK_STOP)
    return True, mode, '%s, draining, stops at %g' % (head, stop)


def eta_hours(pace, start):
    """Hours until pace is below `start` with no more usage."""
    return 0.0 if pace < start else round((pace - start) / PACE_PER_HOUR + 0.1, 1)


def decide(mode, last_band, pace, session, week, recent_claims, stopped, local_dt, bands=DEFAULT_BANDS):
    """The whole gate. recent_claims = number of claims in the last hour. -> Decision(ok, mode, band, reason, start, stop)."""
    band = band_of(local_dt, bands)
    start, stop = levels(band, bands)
    mode = mode if mode in ('idle', 'draining') else 'idle'
    if stopped:
        return Decision(False, mode, band, 'STOP switch is on: nothing runs', start, stop)
    if pace is None or week is None:
        return Decision(False, mode, band, '%s band: pace cannot be read, nothing runs' % band, start, stop)
    if last_band and last_band != band and mode == 'draining':
        mode = 'idle'
    ok, new, why = step(mode, pace, session, week, start, stop, band)
    if not ok and new == 'idle' and pace >= start:
        why += '; pace below %g in about %s h if no more is used' % (start, eta_hours(pace, start))
    if ok and recent_claims >= PER_HOUR:
        return Decision(False, new, band, 'rate limit: %d items already in the last hour' % PER_HOUR, start, stop)
    return Decision(ok, new, band, why, start, stop)
