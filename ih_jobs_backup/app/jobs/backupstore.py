"""Job backupstore: publish sensor.backup_storage_local / sensor.backup_storage_drive (used GB, backups vs other).
Entities, attributes, units and rounding follow the original script.
Backup sizes: websocket backup/info (per agent). Drive totals: the two HA Google Drive sensors named in the options drive_used_entity / drive_total_entity (empty = the drive sensor is not published). Disk: the container cannot see the Pi disk
(no /backup folder is mapped). Sources in order: optional HA sensors (disk_total_entity / disk_used_entity), Supervisor GET /host/info (option host_info; the Supervisor may refuse it),
a /backup folder. If none works the sensor still publishes with the backups size only (disk_known=false); the job does not fail. Units: GiB (1024^3, same as HA's own sensors), shown as 'GB'."""
import asyncio, datetime, json, os, shutil

G = 1024 ** 3
LOCAL_AGENT = 'hassio.local'
DRIVE_PREFIX = 'google_drive.'
WS_URL = 'ws://supervisor/core/websocket'
UNITS = {'B': 1 / G, 'kB': 1000 / G, 'MB': 1e6 / G, 'MiB': 1 / 1024, 'GB': 1e9 / G, 'GiB': 1, 'TB': 1e12 / G, 'TiB': 1024}


def gib(ctx, entity):
    s = ctx.ha('states/' + entity)
    return float(s['state']) * UNITS[s['attributes'].get('unit_of_measurement', 'GiB')]


async def _info(token):
    import websockets
    async with websockets.connect(WS_URL, max_size=None) as ws:
        await ws.recv(); await ws.send(json.dumps({'type': 'auth', 'access_token': token})); await ws.recv()
        await ws.send(json.dumps({'id': 1, 'type': 'backup/info'}))
        while True:
            r = json.loads(await ws.recv())
            if r.get('id') == 1:
                return r['result']


def backup_info(ctx):
    return asyncio.run(_info(ctx.token))


def disk(ctx):
    """-> (total_gib, used_gib, source text). Order: the two optional HA sensors (disk_total_entity / disk_used_entity, e.g. from the System Monitor
    integration; needs no extra permission), then the Supervisor /host/info (option host_info, default on; some Supervisor roles refuse it),
    then a /backup folder. Raises RuntimeError when none works (run() then degrades instead of failing)."""
    dt, du = (str(ctx.opts.get(k) or '').strip() for k in ('disk_total_entity', 'disk_used_entity'))
    if dt and du:
        try:
            return gib(ctx, dt), gib(ctx, du), 'HA sensors ' + du
        except Exception as e:
            ctx.log('backupstore: disk sensors failed (%s)' % type(e).__name__)
    if ctx.opts.get('host_info', True):
        try:
            d = ctx.supervisor('host/info')['data']
            return float(d['disk_total']), float(d['disk_used']), 'supervisor /host/info'
        except Exception as e:
            ctx.log('backupstore: host/info failed (%s)' % type(e).__name__)
    if os.path.isdir('/backup'):
        du = shutil.disk_usage('/backup')
        return du.total / G, (du.total - du.free) / G, 'shutil /backup'
    raise RuntimeError('no disk figure available')


def publish_backups_only(ctx, name, label, icon, backups, count):
    """Degraded sensor when the disk size is unknown: state = backups size only, no total/free/percent attributes."""
    attrs = dict(backups_gb=round(backups, 1), backups_count=count, disk_known=False, unit_of_measurement='GB', friendly_name=label, icon=icon,
                 state_class='measurement', updated=datetime.datetime.now().astimezone().isoformat(timespec='seconds'))
    ctx.set_state('sensor.' + name, round(backups, 1), attrs)
    return '%s: backups %.1f GB (%d); disk size unknown, state is the backups size only' % (label, backups, count)


def publish(ctx, name, label, icon, total, backups, count, used=None):
    used = used if used is not None else 0
    other = max(used - backups, 0)
    attrs = dict(backups_gb=round(backups, 1), other_gb=round(other, 1), total_gb=round(total, 1), free_gb=round(max(total - used, 0), 1),
                 used_pct=round(100 * used / total, 1) if total else 0, backups_count=count, unit_of_measurement='GB', friendly_name=label, icon=icon,
                 state_class='measurement', updated=datetime.datetime.now().astimezone().isoformat(timespec='seconds'))
    ctx.set_state('sensor.' + name, round(used, 1), attrs)
    return '%s: used %.1f of %.1f GB (%.1f%%), backups %.1f (%d), other %.1f, free %.1f' % (label, used, total, attrs['used_pct'], backups, count, other, attrs['free_gb'])


def run(ctx, info=None, disk_fn=None):
    """One run. Returns True; raises on failure (the scheduler then retries)."""
    bl = (info or backup_info)(ctx)['backups']
    loc = sum(b['agents'].get(LOCAL_AGENT, {}).get('size', 0) for b in bl) / G
    nloc = sum(1 for b in bl if LOCAL_AGENT in b['agents'])
    drv = drn = 0
    for b in bl:
        for a, v in b['agents'].items():
            if a.startswith(DRIVE_PREFIX):
                drv += v.get('size', 0) / G; drn += 1
    try:
        total, used, src = (disk_fn or disk)(ctx)
        ctx.log('backupstore: disk figure from ' + src)
        ctx.log(publish(ctx, 'backup_storage_local', 'Local disk', 'mdi:harddisk', total, loc, nloc, used))
    except RuntimeError:
        ctx.log('backupstore: no disk figure (set disk_total_entity and disk_used_entity to fix); publishing backups size only')
        ctx.log(publish_backups_only(ctx, 'backup_storage_local', 'Local disk', 'mdi:harddisk', loc, nloc))
    du, dt = (str(ctx.opts.get(k) or '').strip() for k in ('drive_used_entity', 'drive_total_entity'))
    if du and dt:
        ctx.log(publish(ctx, 'backup_storage_drive', 'Google Drive', 'mdi:google-drive', gib(ctx, dt), drv, drn, gib(ctx, du)))
    else:
        ctx.log('backupstore: no drive_used_entity / drive_total_entity option: drive sensor not published')
    return True
