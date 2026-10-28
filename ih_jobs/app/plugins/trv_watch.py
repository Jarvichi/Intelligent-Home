"""trv_watch: every 30 minutes, find radiator valves (ZHA) whose temperature reading has frozen, try a ZHA reconfigure (at most one per valve per 12 h),
and put ONE display message on the display if a valve is still frozen an hour after the reconfigure (at most one per valve per day).
Stage 1 (history): current_temperature has not CHANGED value for `frozen_h` hours and the valve was not 'off' during that time.
Stage 2 (the valve itself): read the valve's own temperature (a radio read, which also refreshes HA's cached value). Frozen when it differs from what HA shows by
`diff_c` or more. If the valve does not answer, act only when it was flat >= 24 h and heating (or off its target) during that time.
Valves are found at run time from the device and entity registries (platform zha, climate, device model == `model`), so no addresses are stored.
SAFETY: the reconfigure is a real action on a heating device. Only ONE live copy may exist: if the old host loop (tmux 'trvwatch') is live, keep this plugin dry
(live false). Never both: they would each keep their own 12 h cap.
Every house value comes from settings (JSON in plugins[].settings); defaults in brackets:
  model             device model text to watch (TRVZB)
  display_script     script entity that shows a display row; called with text 'title | detail' (empty = no display message)
  frozen_h          hours without a change = candidate (6)
  diff_c            stage-2 difference counted as frozen (0.5)
  reconf_gap_h      hours before reconfigure is tried again (12)
  check_after_min   minutes after a reconfigure before the follow-up read (60)
  msg_gap_h         hours between display messages for one valve (24)
  ok_read_gap_h     hours before a valve whose read matched HA is asked again (3)
  history_h         hours of history read per valve (30; more than 24 so the 24 h rule still works; the old script read 72)
  title_strip       text removed from the device name in the message (' Thermostat')
  msg_title         message title, {name} = short device name ('{name} valve stuck')
  msg_detail        message detail ('reconfigure did not help, check batteries')
  dry_device_read   true = a DRY run may also do the stage-2 radio read (read only, costs valve battery); default false
  minutes           run every N minutes (30)
State (plugin state 'valves'): {entity_id: {"reconf_ts", "msg_ts", "ok_read_ts"}}, unix seconds. Same shape as the old script's json, so it can be copied in.
Dry: reads history and states, logs the verdict per valve and 'would reconfigure <entity>' / 'would send display'; sends nothing, saves nothing."""
import datetime as dt

from ihlib import series as S

NAME = 'trv_watch'
SCHEDULE = {'kind': 'every', 'minutes': 30}
DEFAULTS = {'model': 'TRVZB', 'display_script': '', 'frozen_h': 6.0, 'diff_c': 0.5, 'reconf_gap_h': 12, 'check_after_min': 60, 'msg_gap_h': 24, 'ok_read_gap_h': 3,
            'history_h': 30, 'title_strip': ' Thermostat', 'msg_title': '{name} valve stuck', 'msg_detail': 'reconfigure did not help, check batteries',
            'dry_device_read': False}


def find_valves(devices, entities, model):
    """{entity_id: (ieee, device name)} for every zha climate entity whose device model equals `model`."""
    dm = {d['id']: d for d in devices or []}
    out = {}
    for e in entities or []:
        if e.get('entity_id', '').startswith('climate.') and e.get('platform') == 'zha':
            d = dm.get(e.get('device_id'), {})
            if d.get('model') == model:
                ieee = next((i[1] for i in d.get('identifiers', []) if i and i[0] == 'zha'), None)
                out[e['entity_id']] = (ieee, d.get('name_by_user') or d.get('name') or e['entity_id'])
    return out


def history_rows(rest_rows):
    """REST history rows -> [(ts, state, current_temperature, target, hvac_action)] oldest first."""
    out = []
    for r in rest_rows or []:
        a = r.get('attributes') or {}
        try:
            out.append((S.ts_of(r.get('last_updated') or r.get('last_changed')), r.get('state'), a.get('current_temperature'), a.get('temperature'), a.get('hvac_action')))
        except (TypeError, ValueError):
            pass
    out.sort(key=lambda x: x[0])
    return out


def verdict(rows, now, s):
    """rows: history up to `now`. -> dict(frozen, why, hours, last_change, active, ...). Stage-1 rule only."""
    rows = [r for r in rows if r[0] <= now and r[2] is not None]
    if not rows:
        return dict(frozen=False, why='no data', hours=0, last_change=None, active=False)
    cur = rows[-1][2]
    i = len(rows) - 1
    while i > 0 and rows[i - 1][2] == cur:
        i -= 1
    last_change = rows[i][0]
    hours = (now - last_change) / 3600
    run = rows[i:]
    awake = any(r[1] != 'off' for r in run)
    active = any(r[1] != 'off' and (r[4] == 'heating' or (r[3] is not None and abs(r[3] - r[2]) >= s['diff_c'])) for r in run)
    frozen = hours >= s['frozen_h'] and awake
    why = 'frozen' if frozen else ('valve off' if hours >= s['frozen_h'] else 'ok')
    return dict(frozen=frozen, why=why, hours=hours, last_change=last_change, active=active, cur=cur, target=run[-1][3], action=run[-1][4])


def confirm(s, dev_t, ha_t, hours, active):
    """Stage 2: (frozen, text). dev_t = temperature read from the valve (None = no answer), ha_t = what HA shows."""
    if dev_t is None:
        return (hours >= 24 and active), 'valve did not answer the read'
    if ha_t is None or abs(dev_t - ha_t) >= s['diff_c']:
        return True, 'valve says %s, HA showed %s' % (dev_t, ha_t)
    return False, 'valve says %s, HA shows %s: same, steady room' % (dev_t, ha_t)


def device_temp(ha, ieee):
    """The valve's own local temperature (cluster 513 attribute 0, hundredths of a degree). None = no answer."""
    try:
        return int(ha.ws({'type': 'zha/devices/clusters/attributes/value', 'ieee': ieee, 'endpoint_id': 1, 'cluster_id': 513, 'cluster_type': 'in', 'attribute': 0})) / 100
    except Exception:
        return None


def _reconf(ha, ieee):
    try:
        return repr(ha.ws({'type': 'zha/devices/reconfigure', 'ieee': ieee}))
    except Exception as e:
        return 'failed (%s)' % type(e).__name__


def run(ctx):
    s = ctx.settings
    now = ctx.now.timestamp()
    start = ctx.now - dt.timedelta(hours=float(s['history_h']))
    devs, ents = ctx.ha.ws_many([{'type': 'config/device_registry/list'}, {'type': 'config/entity_registry/list'}])
    vs = find_valves(devs, ents, s['model'])
    st = ctx.state.load('valves')
    read_ok = (not ctx.dry) or s['dry_device_read']
    ncand = nconf = 0

    def show_row(short):
        if not s['display_script']:
            ctx.log('no display_script set: message not sent')
            return False
        dom, _, svc = s['display_script'].partition('.')
        ctx.service(dom, svc, {'text': '%s | %s' % (s['msg_title'].format(name=short), s['msg_detail'])})
        return not ctx.dry

    for ent, (ieee, name) in sorted(vs.items()):
        v = verdict(history_rows(ctx.ha.history(ent, start, ctx.now)), now, s)
        short = name.replace(s['title_strip'], '').strip() if s['title_strip'] else name.strip()
        cand = v['frozen']
        ncand += cand
        d = st.setdefault(ent, {})
        rts = d.get('reconf_ts')
        followup = rts is not None and (now - rts) / 60 >= s['check_after_min']
        try:
            ha_t = (ctx.ha.get('states/' + ent) or {}).get('attributes', {}).get('current_temperature')
        except Exception:
            ha_t = None
        note, frozen = '', False
        if followup or (rts is None and cand):
            if rts is None and now - d.get('ok_read_ts', 0) < s['ok_read_gap_h'] * 3600:
                note = 'checked OK recently'
            elif not read_ok:
                note = 'stage-2 valve read skipped (dry)'
                ctx.log('[DRY] %s: would read the valve and reconfigure it if it disagrees with HA' % ent)
            else:
                frozen, note = confirm(s, device_temp(ctx.ha, ieee), ha_t, v['hours'], True if followup else v['active'])
                if not followup and not frozen and 'same' in note:
                    d['ok_read_ts'] = now
            if followup:
                note = 'follow-up after reconfigure: ' + note
        nconf += bool(frozen)
        ctx.log('%s: unchanged %.1f h, heating/gap %s -> %s%s' % (ent, v['hours'], 'yes' if v['active'] else 'no',
                'FROZEN' if frozen else ('candidate, not frozen' if cand else 'ok'), ' (%s)' % note if note else ''))
        if followup:
            if frozen:
                if (now - d.get('msg_ts', 0)) / 3600 >= s['msg_gap_h']:
                    ok = show_row(short)
                    if not ctx.dry:
                        d['msg_ts'] = now
                    ctx.log('%s: still frozen %.0f min after reconfigure (%s) -> display message sent=%s' % (ent, (now - rts) / 60, note, ok))
                if (now - rts) / 3600 >= s['reconf_gap_h']:
                    if ctx.dry:
                        ctx.log('[DRY] would reconfigure %s (reconfigure gap passed)' % ent)
                    else:
                        ctx.log('%s: reconfigure gap passed, trying again: %s' % (ent, _reconf(ctx.ha, ieee)))
                        d['reconf_ts'] = now
            else:
                ctx.log('%s: recovered after reconfigure (%s)' % (ent, note))
                d.pop('reconf_ts', None)
        elif frozen and rts is None:
            if ctx.dry:
                ctx.log('[DRY] would reconfigure %s (frozen %.1f h; %s)' % (ent, v['hours'], note))
            else:
                res = _reconf(ctx.ha, ieee)
                d['reconf_ts'] = now
                ctx.log('%s: frozen %.1f h (%s) -> ZHA reconfigure sent, result %s' % (ent, v['hours'], note, res))
        elif rts is not None and (now - rts) / 3600 >= s['reconf_gap_h']:
            d.pop('reconf_ts', None)
    if ctx.dry:
        ctx.log('[DRY] heartbeat: valves %d, candidates %d, confirmed %d (nothing saved)' % (len(vs), ncand, nconf))
    else:
        ctx.state.save('valves', st)
        ctx.log('heartbeat: valves %d, candidates %d, confirmed %d' % (len(vs), ncand, nconf))
    return True
