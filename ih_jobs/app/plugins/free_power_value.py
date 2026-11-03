"""free_power_value: after each supplier free electricity session (and Happy Hour), work out how much electricity the house used in the session window and what
it would have cost at the normal rate, then keep a running winter total. Hourly timer plugin.
For every session that ended more than `settle_h` (2) hours ago and is not recorded yet:
  kWh   = sum over the half-hour slots of the session of the usage in that slot, from Home Assistant long-term statistics of the live total-consumption sensor (5-minute
          buckets of `change`). A slot counts only when all six buckets are there; if any slot has no data, nothing is recorded and the next pass tries again.
  worth = sum over slots of (slot kWh x the normal unit rate of that slot, value_inc_vat in GBP/kWh, from the day-rate entities).
  extra = kWh minus the session baseline (usual use for that time), read from the history of the baseline sensor (its state is the baseline of the current slot).
          No baseline in the history = no "more than usual" part in the message.
  meter = the same window from the slow official half-hourly sensor, when it already covers the window (logged beside the live figure and kept in the line; the live figure is used).
Sessions that touch each other (same kind, one ends when the next starts) are one block: they are recorded together once the last one has settled, with one message.
A session still without full data `give_up_days` (3) after it ended is recorded as skipped (one log line) and never retried.
Sessions that ended before `season_start` or started after `season_end` are ignored. Happy Hours are listed beside free sessions; the same session in both lists is counted once.
Every house value comes from settings (JSON in plugins[].settings); defaults in brackets:
  events_free_entity   event entity with the free sessions (attribute events: id, start, end)  (empty = not configured)
  events_happy_entity  event entity with Happy Hours, same shape ('' = none)
  consumption_entity   total_increasing kWh sensor kept in long-term statistics (empty = not configured)
  meter_entity         slow official consumption sensor with attribute charges [{start,end,consumption}] ('' = no cross-check)
  rates_entities       list of day-rate event entities (attribute rates: start, end, value_inc_vat), e.g. current day and previous day
  baseline_entity      session baseline sensor ('' = no baseline)
  channel              Discord channel name or number for the message (alerts); token_option names the bot token option (discord_token)
  season_start / season_end   YYYY-MM-DD (2026-11-01 / 2027-02-14)
  settle_h (2), give_up_days (3), min_extra_kwh (0.05: smaller differences are not mentioned), minutes (60)
State (plugin state 'ledger'): {"sessions": {id: {line}}, "total_gbp", "total_kwh", "skipped": {id: reason}}. Dry keeps its own 'ledger_dry' so going live posts every session
not yet posted; dry posts nothing. The facts store is not written (a new facts key needs approval); the totals live in this file only."""
import datetime as dt

from ihlib import series as S
from plugins import brain_inbox

NAME = 'free_power_value'
SCHEDULE = {'kind': 'every', 'minutes': 60}
DEFAULTS = {'events_free_entity': '', 'events_happy_entity': '', 'consumption_entity': '', 'meter_entity': '', 'rates_entities': [], 'baseline_entity': '',
            'channel': 'alerts', 'token_option': 'discord_token', 'season_start': '2026-11-01', 'season_end': '2027-02-14', 'settle_h': 2, 'give_up_days': 3,
            'min_extra_kwh': 0.05, 'minutes': 60}
SLOT = 1800
BUCKET = 300


def parse_events(rows, kind):
    """-> [{id, kind, start, end}] (unix seconds). Bad rows dropped."""
    out = []
    for r in rows or []:
        try:
            out.append({'id': str(r['id']), 'kind': kind, 'start': S.ts_of(r['start']), 'end': S.ts_of(r['end'])})
        except (KeyError, TypeError, ValueError):
            pass
    return [e for e in out if e['end'] > e['start']]


def merge_events(free, happy):
    """One list, sorted by start. The same window in both lists counts once (the free list wins)."""
    seen = {(e['start'], e['end']) for e in free}
    return sorted(free + [e for e in happy if (e['start'], e['end']) not in seen], key=lambda e: (e['start'], e['id']))


def chains(events):
    """Group sessions of the same kind that touch (one ends when the next starts). -> list of lists, each sorted."""
    out = []
    for e in sorted(events, key=lambda e: e['start']):
        for c in out:
            if c[0]['kind'] == e['kind'] and c[-1]['end'] == e['start']:
                c.append(e)
                break
        else:
            out.append([e])
    return out


def slots_of(start, end):
    t = int(start // SLOT * SLOT)
    out = []
    while t < end:
        out.append(t)
        t += SLOT
    return out


def bucket_changes(stats_rows):
    """Statistics rows [{start (ms or ISO), change}] -> {bucket start (unix s): change}."""
    out = {}
    for r in stats_rows or []:
        try:
            st = r['start']
            st = st / 1000.0 if isinstance(st, (int, float)) else S.ts_of(st)
            if r.get('change') is not None:
                out[int(round(st))] = float(r['change'])
        except (KeyError, TypeError, ValueError):
            pass
    return out


def slot_kwh(changes, slots):
    """({slot: kWh}, missing slots). A slot is complete only with all its 5-minute buckets."""
    use, missing = {}, []
    per = SLOT // BUCKET
    for t in slots:
        parts = [changes.get(t + i * BUCKET) for i in range(per)]
        if any(p is None for p in parts):
            missing.append(t)
        else:
            use[t] = sum(parts)
    return use, missing


def rate_map(rate_lists):
    out = {}
    for rows in rate_lists:
        for r in rows or []:
            try:
                out[int(S.ts_of(r['start']))] = float(r['value_inc_vat'])
            except (KeyError, TypeError, ValueError):
                pass
    return out


def meter_map(charges):
    out = {}
    for r in charges or []:
        try:
            out[int(S.ts_of(r['start']))] = float(r['consumption'])
        except (KeyError, TypeError, ValueError):
            pass
    return out


def baseline_for(rs, slots):
    """Sum of the baseline over the slots (each = sensor value one minute into the slot), or None when any slot has no value."""
    tot = 0.0
    for t in slots:
        v = S.value_at(rs, t + 60)
        if v is None:
            return None
        tot += v
    return tot


def evaluate(sess, use, rates, base_rs, meter):
    """sess = one session; use {slot: kWh}, rates {slot: GBP/kWh}, base_rs numeric series or None, meter {slot: kWh}.
    -> (line dict or None, why not). Pure."""
    slots = slots_of(sess['start'], sess['end'])
    if any(t not in use for t in slots):
        return None, 'usage missing for %d of %d slots' % (sum(1 for t in slots if t not in use), len(slots))
    if any(t not in rates for t in slots):
        return None, 'rate missing for %d of %d slots' % (sum(1 for t in slots if t not in rates), len(slots))
    kwh = sum(use[t] for t in slots)
    worth = sum(use[t] * rates[t] for t in slots)
    base = baseline_for(base_rs, slots) if base_rs else None
    mk = sum(meter[t] for t in slots) if meter and all(t in meter for t in slots) else None
    return {'id': sess['id'], 'kind': sess['kind'], 'start': sess['start'], 'end': sess['end'], 'kwh': round(kwh, 3), 'gbp': round(worth, 4),
            'baseline_kwh': None if base is None else round(base, 3), 'meter_kwh': None if mk is None else round(mk, 3)}, ''


def when_text(start, end, tz):
    a, b = dt.datetime.fromtimestamp(start, tz), dt.datetime.fromtimestamp(end, tz)
    return '%s %s-%s' % (a.strftime('%a %-d %b'), a.strftime('%H:%M'), b.strftime('%H:%M'))


def message(lines, total_gbp, tz, min_extra=0.05):
    """lines = recorded lines of one block. One message text."""
    kwh = sum(x['kwh'] for x in lines)
    gbp = sum(x['gbp'] for x in lines)
    label = 'Free power' if lines[0]['kind'] == 'free' else 'Happy Hour'
    text = '⚡ %s, %s: %.1f kWh, worth £%.2f' % (label, when_text(lines[0]['start'], lines[-1]['end'], tz), kwh, gbp)
    if all(x['baseline_kwh'] is not None for x in lines):
        extra = kwh - sum(x['baseline_kwh'] for x in lines)
        if abs(extra) >= min_extra:
            text += ' (%.1f kWh %s than usual)' % (abs(extra), 'more' if extra > 0 else 'less')
    return text + '. Winter total so far: £%.2f.' % total_gbp


def _load(ctx):
    name = 'ledger_dry' if ctx.dry else 'ledger'
    led = ctx.state.load(name) or {}
    led.setdefault('sessions', {})
    led.setdefault('skipped', {})
    led.setdefault('total_gbp', 0.0)
    led.setdefault('total_kwh', 0.0)
    return name, led


def _day(text, tz):
    return dt.datetime.strptime(text, '%Y-%m-%d').replace(tzinfo=tz).timestamp()


def run(ctx):
    s = ctx.settings
    if not (s['events_free_entity'] and s['consumption_entity'] and s['rates_entities']):
        ctx.log('not configured (needs events_free_entity, consumption_entity and rates_entities in settings)')
        return False
    now = ctx.now.timestamp()
    tz = ctx.now.tzinfo or dt.timezone.utc
    t_start, t_end = _day(s['season_start'], tz), _day(s['season_end'], tz) + 86400
    free = parse_events((ctx.ha.get('states/' + s['events_free_entity']) or {}).get('attributes', {}).get('events'), 'free')
    happy = []
    if s['events_happy_entity']:
        happy = parse_events((ctx.ha.get('states/' + s['events_happy_entity']) or {}).get('attributes', {}).get('events'), 'happy')
    events = [e for e in merge_events(free, happy) if e['end'] > t_start and e['start'] < t_end]
    name, led = _load(ctx)
    todo = []
    for c in chains(events):
        if c[-1]['end'] + float(s['settle_h']) * 3600 > now:
            continue   # not settled yet
        c = [e for e in c if e['id'] not in led['sessions'] and e['id'] not in led['skipped']]
        if c:
            todo.append(c)
    if not todo:
        ctx.log('nothing to record (%d sessions this season, %d recorded, total GBP %.2f)' % (len(events), len(led['sessions']), led['total_gbp']))
        return True
    rates = rate_map([(ctx.ha.get('states/' + e) or {}).get('attributes', {}).get('rates') for e in s['rates_entities']])
    meter = {}
    if s['meter_entity']:
        try:
            meter = meter_map((ctx.ha.get('states/' + s['meter_entity']) or {}).get('attributes', {}).get('charges'))
        except Exception:
            meter = {}
    for c in todo:
        t0, t1 = c[0]['start'], c[-1]['end']
        iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        res = ctx.ha.ws({'type': 'recorder/statistics_during_period', 'start_time': iso(t0), 'end_time': iso(t1), 'statistic_ids': [s['consumption_entity']],
                         'period': '5minute', 'types': ['change']}) or {}
        use, _ = slot_kwh(bucket_changes(res.get(s['consumption_entity'])), slots_of(t0, t1))
        base_rs = None
        if s['baseline_entity']:
            try:
                base_rs = S.num(S.series(ctx.ha.history(s['baseline_entity'], dt.datetime.fromtimestamp(t0 - 6 * 3600, dt.timezone.utc),
                                                        dt.datetime.fromtimestamp(t1, dt.timezone.utc))))
            except Exception:
                base_rs = None
        new, stuck = [], False
        for e in c:
            line, why = evaluate(e, use, rates, base_rs, meter)
            if line is None:
                age_d = (now - e['end']) / 86400
                if age_d >= float(s['give_up_days']):
                    ctx.log('session %s (%s): giving up after %.1f days: %s' % (e['id'], when_text(e['start'], e['end'], tz), age_d, why))
                    led['skipped'][e['id']] = why
                else:
                    ctx.log('session %s (%s): waiting, %s' % (e['id'], when_text(e['start'], e['end'], tz), why))
                    stuck = True
                continue
            new.append(line)
        if stuck:
            new = []   # the block waits as a whole, so the message covers every part (a part that gave up does not hold it)
        for line in new:
            led['sessions'][line['id']] = line
            led['total_gbp'] = round(led['total_gbp'] + line['gbp'], 4)
            led['total_kwh'] = round(led['total_kwh'] + line['kwh'], 3)
            ctx.log('session %s recorded: %.3f kWh, GBP %.4f, baseline %s, meter %s' % (line['id'], line['kwh'], line['gbp'], line['baseline_kwh'], line['meter_kwh']))
            if line['meter_kwh'] is not None:
                ctx.log('session %s cross-check: live %.3f kWh vs meter %.3f kWh (diff %.3f)' % (line['id'], line['kwh'], line['meter_kwh'], line['kwh'] - line['meter_kwh']))
        if new:
            text = message(new, led['total_gbp'], tz, float(s['min_extra_kwh']))
            chan = s['channel']
            if not ctx.dry:
                chan = brain_inbox.find_channel(ctx, ctx.option(s['token_option']), s['channel'])
                if not chan:
                    ctx.log('channel not found: message not posted')
            if chan:
                ctx.discord.post(chan, text, token_option=s['token_option'])
    ctx.state.save(name, led)
    return True
