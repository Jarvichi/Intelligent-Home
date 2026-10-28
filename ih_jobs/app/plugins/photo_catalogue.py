"""photo_catalogue: nightly catalogue of photos into viewable albums. Port of the old host script 'catalogue' (+ its resize helper), now with Pillow.
Sources: <brain_dir>/attachments/** and every folder in `media_sources` (an allow-list: nothing else is scanned).
Outputs (all private, never in a public web folder):
  index.json (plugin state /data/photo_catalogue/index.json)   catalogue: date, topic, note, caption (kept on re-run), album file name, paths
  <media_out>/by-topic/<topic>/<name>.jpg      1600 px copies          <media_out>/by-month/<YYYY-MM>/... hard links of the same files
  <media_out>/_thumbs/by-*/...                 400 px thumbnails (own tree)
  <media_out>/_family/...                      only the auto albums picked in the album registry (owned by photo_albums; read here), pruned otherwise
  <share_out>/{by-topic,by-month}              real copies for a private file share (skipped when share_out is empty)
Privacy rules are SETTINGS: skip_name_regex (file names that are never catalogued), private_note_marker (a photo whose first mentioning note has this text in its path is skipped).
Settings (JSON in plugins[].settings; defaults in brackets):
  at ['03:20']          daily start (the add-on timezone)
  brain_dir ['/share/brain']   media_out ['/media/albums']   share_out [''] (empty = no file-share copy)
  media_sources []      extra folders to scan (topic = folder name)
  skip_name_regex       [generic: licence/passport/receipt/insurance/bank/card/statement/id/private]
  private_note_marker ['private']      big_px [1600]   thumb_px [400]   jpeg_quality [82]
  share_uid [1000] share_gid [1000]    owner of copies in the file share
  auto_family_seed []   picked auto albums used until the album registry exists (e.g. ["by-topic/holiday"])
  seed_index ['']       path of an old index.json read ONCE when the plugin has none (keeps captions); never written
Dry (the default): scans and logs counts, what it would resize/copy/remove; writes nothing. Live needs Pillow (logged and skipped if missing).
Differences from the old node resizer: see addon_jobs.md (Pillow LANCZOS, EXIF orientation applied, transparency flattened onto white)."""
import datetime
import json
import os

from . import _photolib as L

NAME = 'photo_catalogue'
SCHEDULE = {'kind': 'daily', 'at': '03:20'}
DEFAULTS = {'at': '03:20', 'brain_dir': '/share/brain', 'media_out': '/media/albums', 'share_out': '', 'media_sources': [], 'skip_name_regex': L.DEFAULT_SKIP,
            'private_note_marker': 'private', 'big_px': 1600, 'thumb_px': 400, 'jpeg_quality': 82, 'share_uid': 1000, 'share_gid': 1000,
            'auto_family_seed': [], 'seed_index': ''}


def old_index(ctx, s):
    d = ctx.state.load('index')
    if not d.get('photos') and s.get('seed_index'):
        try:
            with open(s['seed_index']) as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            d = {}
    return {e['src']: e for e in (d.get('photos') or []) if isinstance(e, dict) and 'src' in e}


def picked_family(ctx, s):
    reg = ctx.sibling_state('photo_albums').load('albums')
    if isinstance(reg.get('auto_family'), list):
        return reg['auto_family']
    return s.get('auto_family_seed') or []


def run(ctx):
    s = ctx.settings
    media_out = s['media_out']
    sources = [m for m in (s.get('media_sources') or []) if isinstance(m, str) and m]
    if not os.path.isdir(os.path.join(s['brain_dir'], 'attachments')) and not sources:
        ctx.log('no photo source folder found (brain_dir/attachments, media_sources): nothing to do')
        return False
    live = not ctx.dry
    if live and not L.have_pillow():
        ctx.log('error: Pillow is not installed in this add-on image, so nothing can be resized; catalogue run skipped (rebuild the add-on)')
        return False
    big, small, q = L.int_or(s['big_px'], 1600), L.int_or(s['thumb_px'], 400), L.int_or(s['jpeg_quality'], 82)
    uid, gid = L.int_or(s['share_uid'], 1000), L.int_or(s['share_gid'], 1000)
    with L.LOCK:
        photos, skipped = L.scan(s['brain_dir'], sources, L.skip_regex(s), s.get('private_note_marker') or '', old_index(ctx, s))
        jobs = L.plan_jobs(photos, media_out, big, small)
        ctx.log('%d photos, %d skipped (%s)' % (len(photos), len(skipped), '; '.join('%s: %s' % (os.path.basename(p), w) for p, w in skipped)))
        picked = picked_family(ctx, s)
        if not live:
            todo = sum(1 for j in jobs if not os.path.exists(j['dst']))
            months = L.link_months(photos, dry=True)
            c, g = L.share_sync(photos, media_out, s['share_out'], uid, gid, dry=True)
            fp, fc, fg = L.family_sync(photos, media_out, picked, dry=True)
            ctx.log('[DRY] would write index.json (%d photos), resize %d of %d images, link %d month files, share sync %d copied/%d removed, family sync %d albums picked/%d copied/%d removed; nothing written'
                    % (len(photos), todo, len(jobs), months, c, g, fp, fc, fg))
            return True
        ctx.state.save('index', {'updated': datetime.datetime.now().isoformat(timespec='seconds'), 'photos': photos})
        ctx.log('resize: written %d' % L.resize_jobs(jobs, q, ctx.log))
        L.link_months(photos)
        c, g = L.share_sync(photos, media_out, s['share_out'], uid, gid)
        if s['share_out']:
            ctx.log('share sync: %d copied, %d removed' % (c, g))
        fp, fc, fg = L.family_sync(photos, media_out, picked)
        ctx.log('family sync: %d albums picked, %d copied, %d removed' % (fp, fc, fg))
    return True
