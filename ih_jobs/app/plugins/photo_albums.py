"""photo_albums: manual photo albums, driven by a dashboard card. Port of the old host service 'photoalbums' (+ its resize helper), now with Pillow.
A `service` plugin: each pass opens a websocket to Home Assistant, subscribes to the event `event_type` (an admin browser fires it with fire_event) and handles every
event for up to `listen_s` seconds, then returns (the host starts the next pass `seconds` later). Events that fire in that short gap are lost: the card shows the
result from the sensor, so a missed click is visible and can be repeated. Once a minute it also ingests files dropped in <media_out>/_uploads and refreshes the sensor.
Actions in the event data: refresh, create_album, rename_album, delete_album, add, remove, move, set_family, delete_photos, ingest. Destructive and sharing actions need confirm:true.
Registry (albums.json = plugin state /data/photo_albums/albums.json; THIS plugin is its only writer; photo_catalogue only reads auto_family from it):
  {albums:[{id,name,dir,family,photos:[ids]}], photos:{u-<sha1>:{name,file,date,added}}, auto_family:["by-topic/x"|"by-month/YYYY-MM"], rev}
It reads photo_catalogue's index.json (read only) to list the catalogue photos ('c:<album file>').
Files (all under media_out): _lib/<id>.jpg (1600 px), _thumbs/_lib, _originals (never touched), _uploads (drop folder), _failed, _state/state.png (JSON in a .png name, the card
reads it), my-albums/<dir>/ (hard links), _family/my-albums/<dir>/ (only while the family flag is on), file-share copy <share_out>/my-albums (if share_out is set).
Sensor: `sensor_entity` (REST, small: rev, album summary, last_result) so the card knows when to refetch.
Settings (JSON in plugins[].settings; defaults in brackets):
  event_type ['house_photo_action']   sensor_entity ['sensor.photo_albums']   media_out ['/media/albums']   share_out ['']
  skip_name_regex [generic]   big_px [1600] thumb_px [400] jpeg_quality [82]   share_uid [1000] share_gid [1000]
  seconds [2] wait between passes   listen_s [600] length of one pass   periodic_s [60]   settle_s [20] upload must be this old before it is picked up   max_upload_mb [10]
  auto_family_seed []   seed_registry [''] path of an old albums.json read ONCE when the plugin has none (never written)
Dry (default): subscribes (read only), runs each action against an in-memory copy of the registry and logs the result; writes no file, posts no sensor, moves no upload.
Live needs Pillow (a clear log line and no work if it is missing)."""
import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import time

from . import _photolib as L

NAME = 'photo_albums'
SCHEDULE = {'kind': 'service', 'seconds': 2}
DEFAULTS = {'seconds': 2, 'listen_s': 600, 'periodic_s': 60, 'settle_s': 20, 'max_upload_mb': 10, 'event_type': 'house_photo_action', 'sensor_entity': 'sensor.photo_albums',
            'media_out': '/media/albums', 'share_out': '', 'skip_name_regex': L.DEFAULT_SKIP, 'big_px': 1600, 'thumb_px': 400, 'jpeg_quality': 82,
            'share_uid': 1000, 'share_gid': 1000, 'auto_family_seed': [], 'seed_registry': ''}
MINE = 'my-albums'
_STARTED = set()   # state folders whose first 'refresh' has run in this process


def now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def clean_name(n):
    return re.sub(r'[\x00-\x1f\x7f/\\]', '', str(n)).strip()[:60]


def jwrite(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    t = p + '.tmp'
    with open(t, 'w') as fh:
        json.dump(obj, fh, indent=1)
    os.replace(t, p)


class Env:
    """Paths and switches of one pass, from the settings."""
    def __init__(self, ctx):
        s = ctx.settings
        self.ctx, self.s, self.dry = ctx, s, ctx.dry
        self.A = s['media_out'].rstrip('/')
        self.LIB, self.TLIB, self.ORIG = self.A + '/_lib', self.A + '/_thumbs/_lib', self.A + '/_originals'
        self.UP, self.FAIL, self.STATE = self.A + '/_uploads', self.A + '/_failed', self.A + '/_state/state.png'
        self.SHARE = (s.get('share_out') or '').rstrip('/')
        self.sk = L.skip_regex(s)
        self.big, self.small, self.q = L.int_or(s['big_px'], 1600), L.int_or(s['thumb_px'], 400), L.int_or(s['jpeg_quality'], 82)
        self.uid, self.gid = L.int_or(s['share_uid'], 1000), L.int_or(s['share_gid'], 1000)
        self.maxsize = L.int_or(s['max_upload_mb'], 10) * 1024 * 1024
        self.settle = L.int_or(s['settle_s'], 20)
        self.last = {}
        self.log = ctx.log

    def share_on(self):
        return bool(self.SHARE) and os.path.isdir(os.path.dirname(self.SHARE))


class Reg:
    def __init__(self, env):
        self.env = env
        ctx, s = env.ctx, env.s
        d = ctx.state.load('albums')
        if not isinstance(d.get('albums'), list) or not isinstance(d.get('photos'), dict):
            d = None
            if s.get('seed_registry'):
                try:
                    with open(s['seed_registry']) as fh:
                        d = json.load(fh)
                except (OSError, ValueError):
                    d = None
            if not isinstance(d, dict) or not isinstance(d.get('albums'), list):
                d = {'albums': [], 'photos': {}, 'auto_family': [str(x).strip('/') for x in (s.get('auto_family_seed') or [])], 'rev': 0}
            self.d = d
            self.d.setdefault('auto_family', [])
            self.d.setdefault('photos', {})
            self.save()
        else:
            self.d = d
        self.d.setdefault('rev', 0)

    def save(self):
        if not self.env.dry:
            self.env.ctx.state.save('albums', self.d)

    albums = property(lambda s: s.d['albums'])
    photos = property(lambda s: s.d['photos'])

    def album(self, i):
        return next((a for a in self.albums if a['id'] == i), None)


def catalogue_photos(env):
    """id -> dict for the nightly catalogue (read only here)."""
    out = {}
    items = env.ctx.sibling_state('photo_catalogue').load('index').get('photos') or []
    for x in items:
        p = x['paths'][0]
        if not os.path.exists(p):
            continue
        rel = os.path.relpath(p, env.A)
        out['c:' + x['album']] = {'id': 'c:' + x['album'], 'name': x['album'], 'date': x['date'], 'origin': 'auto', 'file': x['album'], 'src': p,
                                  'thumb_src': env.A + '/_thumbs/' + rel, 'full': rel, 'thumb': '_thumbs/' + rel, 'auto': ['by-topic/' + x['topic'], 'by-month/' + x['date'][:7]]}
    return out


def all_photos(env, reg):
    out = catalogue_photos(env)
    for i, m in reg.photos.items():
        out[i] = {'id': i, 'name': m['name'], 'date': m['date'], 'origin': 'upload', 'file': m['file'], 'src': env.LIB + '/' + i + '.jpg',
                  'thumb_src': env.TLIB + '/' + i + '.jpg', 'full': '_lib/' + i + '.jpg', 'thumb': '_thumbs/_lib/' + i + '.jpg', 'auto': []}
    return out


def prune(root, want, keep_dirs):
    if not os.path.isdir(root):
        return 0
    n = 0
    for r, ds, fs in os.walk(root, topdown=False):
        for f in fs:
            q = os.path.join(r, f)
            if q not in want:
                os.remove(q)
                n += 1
        if r != root and r not in keep_dirs and not os.listdir(r):
            os.rmdir(r)
    return n


def sync_all(env, reg):
    """Rebuild every managed folder from the registry. Idempotent, cheap when nothing changed. -> list of notes."""
    A = env.A
    ph = all_photos(env, reg)
    notes, used = [], set()
    for a in reg.albums:   # drop references to photos that no longer exist (catalogue changed)
        keep = [i for i in a['photos'] if i in ph]
        if len(keep) != len(a['photos']):
            notes.append('album %s lost %d missing photos' % (a['name'], len(a['photos']) - len(keep)))
            a['photos'] = keep
        d = a['dir']
        if d in used:
            d = a['dir'] = L.slug(a['name'], 'album') + '-' + a['id'][1:5]
        used.add(d)
    share = env.share_on()
    want = {'main': set(), 'thumb': set(), 'fam': set(), 'famt': set(), 'share': set()}
    dirs = {k: set() for k in want}
    roots = {'main': A + '/' + MINE, 'thumb': A + '/_thumbs/' + MINE, 'fam': A + '/_family/' + MINE, 'famt': A + '/_family/_thumbs/' + MINE, 'share': env.SHARE + '/' + MINE}
    for a in reg.albums:
        for k in ('main', 'thumb') + (('fam', 'famt') if a['family'] else ()) + (('share',) if share else ()):
            d = roots[k] + '/' + a['dir']
            dirs[k].add(d)
            os.makedirs(d, exist_ok=True)
        for i in a['photos']:
            p = ph[i]
            f = p['file']
            for k, src in (('main', p['src']), ('thumb', p['thumb_src']), ('fam', p['src']), ('famt', p['thumb_src'])):
                if k in ('fam', 'famt') and not a['family']:
                    continue
                dst = roots[k] + '/' + a['dir'] + '/' + f
                if os.path.exists(src):
                    L.link(src, dst)
                    want[k].add(dst)
            if share:
                dst = roots['share'] + '/' + a['dir'] + '/' + f
                if os.path.exists(p['src']):
                    if not (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(p['src'])):
                        L.mkdir_share(os.path.dirname(dst), env.uid, env.gid)
                        shutil.copyfile(p['src'], dst)
                        L.own(dst, env.uid, env.gid, 0o644)
                    want['share'].add(dst)
    for k in want:
        if k == 'share' and not share:
            continue
        prune(roots[k], want[k], dirs[k])
    try:
        items = env.ctx.sibling_state('photo_catalogue').load('index').get('photos') or []
        L.family_sync(items, A, reg.d['auto_family'])   # auto albums; leaves my-albums alone
    except Exception as e:
        notes.append('auto family sync: %s' % e)
    return notes


def state_obj(env, reg):
    ph = all_photos(env, reg)
    auto = {}
    for p in ph.values():
        for t in p['auto']:
            auto.setdefault(t, []).append(p['id'])
    in_album = {i for a in reg.albums for i in a['photos']}
    return {'rev': reg.d['rev'], 'updated': now(),
            'photos': [{'id': p['id'], 'n': p['name'], 'd': p['date'], 'o': p['origin'], 'f': p['full'], 't': p['thumb']} for p in sorted(ph.values(), key=lambda p: (p['date'], p['name']), reverse=True)],
            'albums': [{'id': a['id'], 'name': a['name'], 'family': a['family'], 'photos': a['photos']} for a in reg.albums],
            'auto': [{'id': 'auto:' + k, 'name': k, 'family': k in reg.d['auto_family'], 'photos': v} for k, v in sorted(auto.items())],
            'unsorted': sorted(i for i, p in ph.items() if p['origin'] == 'upload' and i not in in_album)}


def post_state(env, reg, last=None, bump=True):
    if last:
        env.last = last
    last = env.last or {'req': 'start', 'ok': True, 'msg': 'running', 'ts': now()}
    if env.dry:
        env.log('[DRY] would write the state file and update the sensor (last result: %s)' % ('ok' if last.get('ok') else 'failed'))
        return
    if bump:
        reg.d['rev'] += 1
        reg.save()
    s = state_obj(env, reg)
    jwrite(env.STATE, s)
    attrs = {'friendly_name': 'Photo albums', 'rev': s['rev'], 'updated': s['updated'], 'photo_count': len(s['photos']),
             'albums': [{'id': a['id'], 'name': a['name'], 'family': a['family'], 'count': len(a['photos'])} for a in s['albums']],
             'auto_albums': len(s['auto']), 'last_result': last}
    try:
        env.ctx.ha.post('states/' + env.s['sensor_entity'], {'state': 'ok', 'attributes': attrs}, timeout=15)
    except Exception as e:
        env.log('sensor post failed (%s)' % type(e).__name__)


def ingest(env, reg, names=None, album=None):
    """Process files in _uploads. names=None -> every settled file. -> (ok, message)."""
    if env.dry:
        n = 0
        if os.path.isdir(env.UP):
            n = sum(1 for f in os.listdir(env.UP) if not f.startswith('.') and os.path.isfile(os.path.join(env.UP, f)) and (names is None or f in names))
        env.log('[DRY] would ingest %d file(s) from the uploads folder' % n)
        return True, '0 added, 0 already had (dry run, %d waiting)' % n
    os.makedirs(env.UP, exist_ok=True)
    added = dup = 0
    bad, ids = [], []
    for f in sorted(os.listdir(env.UP)):
        p = os.path.join(env.UP, f)
        if f.startswith('.') or not os.path.isfile(p) or os.path.islink(p):
            continue
        if names is not None and f not in names:
            continue
        if names is None and time.time() - os.path.getmtime(p) < env.settle:
            continue
        base = re.sub(r'^up-\d+-[0-9a-f]{4}-', '', f)
        if not f.lower().endswith(L.EXT):
            bad.append(base + ' (type)')
            os.makedirs(env.FAIL, exist_ok=True)
            shutil.move(p, env.FAIL + '/' + f)
            continue
        if env.sk.search(base):
            bad.append(base + ' (sensitive name, not stored)')
            os.remove(p)
            continue
        if os.path.getsize(p) > env.maxsize:
            bad.append(base + ' (over %d MB)' % (env.maxsize // 1048576))
            os.makedirs(env.FAIL, exist_ok=True)
            shutil.move(p, env.FAIL + '/' + f)
            continue
        with open(p, 'rb') as fh:
            h = hashlib.sha1(fh.read()).hexdigest()[:12]
        i = 'u-' + h
        if i in reg.photos and os.path.exists(env.LIB + '/' + i + '.jpg'):
            dup += 1
            ids.append(i)
            os.remove(p)
            continue
        L.resize_jobs([{'src': p, 'dst': env.LIB + '/' + i + '.jpg', 'max': env.big}, {'src': p, 'dst': env.TLIB + '/' + i + '.jpg', 'max': env.small}], env.q, env.log)
        if not os.path.exists(env.LIB + '/' + i + '.jpg') or not os.path.exists(env.TLIB + '/' + i + '.jpg'):
            bad.append(base + ' (not a readable image)')
            os.makedirs(env.FAIL, exist_ok=True)
            shutil.move(p, env.FAIL + '/' + f)
            continue
        date = L.exif_date(p) or L.name_date(base) or datetime.date.today().isoformat()
        stem = os.path.splitext(base)[0]
        os.makedirs(env.ORIG, exist_ok=True)
        shutil.move(p, env.ORIG + '/' + i + '-' + re.sub(r'[^A-Za-z0-9._-]+', '_', base)[:80])
        reg.photos[i] = {'name': base, 'file': '%s-%s.jpg' % (L.slug(stem, 'album')[:40], h[:6]), 'date': date, 'added': now()}
        added += 1
        ids.append(i)
    a = reg.album(album) if album else None
    if a:
        for i in ids:
            if i not in a['photos']:
                a['photos'].append(i)
    msg = '%d added, %d already had' % (added, dup) + ('' if not a else ', put in "%s"' % a['name']) + ('; skipped: ' + ', '.join(bad) if bad else '')
    reg.save()
    return (not bad or added + dup > 0), msg


def act(env, reg, d):
    """Run one action. -> (ok, msg). Every destructive/sharing action needs confirm:true (the UI sends it after its own dialog)."""
    a = d.get('action')
    ph = None

    def need_ids(key='photos'):
        nonlocal ph
        ph = ph or all_photos(env, reg)
        ids = d.get(key)
        if not isinstance(ids, list) or not ids or len(ids) > 500 or not all(isinstance(x, str) and x in ph for x in ids):
            raise ValueError('unknown photos')
        return list(dict.fromkeys(ids))

    def alb(key='album'):
        x = reg.album(d.get(key))
        if not x:
            raise ValueError('unknown album')
        return x
    try:
        if a == 'refresh':
            return True, 'refreshed'
        if a == 'create_album':
            n = clean_name(d.get('name', ''))
            if not n:
                return False, 'Give the album a name'
            if any(x['name'].lower() == n.lower() for x in reg.albums):
                return False, 'An album called "%s" already exists' % n
            i = 'a' + secrets.token_hex(4)
            dr = L.slug(n, 'album')
            if any(x['dir'] == dr for x in reg.albums):
                dr += '-' + i[1:5]
            reg.albums.append({'id': i, 'name': n, 'dir': dr, 'family': False, 'photos': []})
            return True, 'Created "%s"' % n
        if a == 'rename_album':
            x = alb()
            n = clean_name(d.get('name', ''))
            if not n:
                return False, 'Give the album a name'
            if any(y['name'].lower() == n.lower() and y is not x for y in reg.albums):
                return False, 'An album called "%s" already exists' % n
            old = x['name']
            x['name'] = n
            dr = L.slug(n, 'album')
            x['dir'] = dr if not any(y['dir'] == dr and y is not x for y in reg.albums) else dr + '-' + x['id'][1:5]
            return True, 'Renamed "%s" to "%s"' % (old, n)
        if a == 'delete_album':
            if d.get('confirm') is not True:
                return False, 'Delete needs confirmation'
            x = alb()
            reg.albums.remove(x)
            return True, 'Deleted album "%s" (the photos are kept)' % x['name']
        if a == 'add':
            x = alb()
            ids = need_ids()
            n = 0
            for i in ids:
                if i not in x['photos']:
                    x['photos'].append(i)
                    n += 1
            return True, 'Added %d to "%s"' % (n, x['name'])
        if a == 'remove':
            x = alb()
            ids = need_ids()
            x['photos'] = [i for i in x['photos'] if i not in ids]
            return True, 'Removed %d from "%s"' % (len(ids), x['name'])
        if a == 'move':
            s = alb('from')
            t = alb('to')
            ids = need_ids()
            s['photos'] = [i for i in s['photos'] if i not in ids]
            for i in ids:
                if i not in t['photos']:
                    t['photos'].append(i)
            return True, 'Moved %d to "%s"' % (len(ids), t['name'])
        if a == 'set_family':
            if d.get('confirm') is not True or not isinstance(d.get('family'), bool):
                return False, 'Sharing needs confirmation'
            aid = str(d.get('album', ''))
            if aid.startswith('auto:'):
                k = aid[5:]
                if not L.FAMILY_RE.fullmatch(k):
                    return False, 'unknown album'
                s = set(reg.d['auto_family'])
                (s.add if d['family'] else s.discard)(k)
                reg.d['auto_family'] = sorted(s)
                nm = k
            else:
                x = alb()
                x['family'] = d['family']
                nm = x['name']
            return True, ('"%s" is now shared with the family' if d['family'] else '"%s" is no longer shared with the family') % nm
        if a == 'delete_photos':
            if d.get('confirm') is not True:
                return False, 'Delete needs confirmation'
            ids = need_ids()
            if any(not i.startswith('u-') for i in ids):
                return False, 'Only uploaded photos can be deleted'
            for i in ids:
                for x in reg.albums:
                    x['photos'] = [p for p in x['photos'] if p != i]
                reg.photos.pop(i, None)
                if not env.dry:
                    for f in (env.LIB + '/' + i + '.jpg', env.TLIB + '/' + i + '.jpg'):
                        if os.path.exists(f):
                            os.remove(f)
            return True, 'Deleted %d photo(s) (the original upload file is kept in _originals)' % len(ids)
        if a == 'ingest':
            names = d.get('files')
            names = [str(x) for x in names] if isinstance(names, list) else None
            if names is not None:
                names = {n for n in names if '/' not in n and not n.startswith('.')}
            return ingest(env, reg, names, d.get('album') or None)
        return False, 'Unknown action'
    except ValueError as e:
        return False, str(e)


def handle(env, reg, d, req):
    with L.LOCK:
        try:
            ok, msg = act(env, reg, d)
        except Exception as e:
            ok, msg = False, 'Error: %s' % e
        if env.dry:
            env.log('[DRY] action %s -> %s: %s (in-memory only; no file, registry or sensor written)' % (d.get('action'), 'ok' if ok else 'refused', msg))
            return
        reg.save()
        try:
            notes = sync_all(env, reg)
        except Exception as e:
            notes = ['sync error: %s' % e]
        env.log('%s %s %s %s' % (d.get('action'), ok, msg, notes))
        post_state(env, reg, {'req': req, 'ok': ok, 'msg': msg + (' [' + '; '.join(notes) + ']' if notes else ''), 'ts': now()})


def periodic(env, reg):
    """Pick up files dropped in _uploads without an event."""
    ok, msg = True, ''
    try:
        if os.path.isdir(env.UP) and any(not f.startswith('.') for f in os.listdir(env.UP)):
            with L.LOCK:
                ok, msg = ingest(env, reg)
    except Exception as e:
        ok, msg = False, 'periodic ingest: %s' % e
    if msg and not msg.startswith('0 added, 0'):
        handle(env, reg, {'action': 'refresh'}, 'periodic')


def run(ctx):
    env = Env(ctx)
    if not ctx.dry and not L.have_pillow():
        ctx.log('error: Pillow is not installed in this add-on image, so uploads cannot be resized; album service not started (rebuild the add-on)')
        return False
    reg = Reg(env)
    key = ctx.state.folder
    if not ctx.dry and key not in _STARTED:
        _STARTED.add(key)
        handle(env, reg, {'action': 'refresh'}, 'start')
    tick = [time.time()]
    gap = L.num_or(ctx.settings['periodic_s'], 60)

    def on_event(d):
        if not d:
            return
        handle(env, reg, d, str(d.get('req', '')))

    def on_idle():
        if time.time() - tick[0] > gap:
            tick[0] = time.time()
            periodic(env, reg)
            if not ctx.dry:
                post_state(env, reg, None, False)
    ctx.log('listening for %s (%s)' % (ctx.settings['event_type'], 'dry: nothing is written' if ctx.dry else 'live'))
    ctx.ha.subscribe_events(ctx.settings['event_type'], on_event, max_seconds=L.num_or(ctx.settings['listen_s'], 600), on_idle=on_idle)
    return True
