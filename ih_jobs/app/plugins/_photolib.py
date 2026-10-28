"""Shared code of the two photo plugins (photo_catalogue, photo_albums). Not a plugin itself (the name starts with an underscore, so discovery skips it).
Ports of the old host scripts: file-name and date rules, the catalogue scan, resizing with Pillow (the old node resize helper is gone), the family and file-share copies.
WHO WRITES WHAT (so the two plugins never fight over a file):
  photo_catalogue  owns index.json (state of photo_catalogue) and everything under <media_out>/by-topic, by-month, _thumbs/by-*, and the auto albums in _family/by-*.
  photo_albums     owns albums.json (state of photo_albums, the registry), <media_out>/_lib, _thumbs/_lib, _originals, _uploads, _failed, _state, my-albums/* (also in _family and the share).
  Each only READS the other one's file (through ctx.sibling_state). Both plugins run in the same process, so the one lock below (LOCK) is held whenever either of them writes
  into the media or share folders: a nightly catalogue run and an album edit never run at the same time.
Every house-specific word (paths, file-name filters, marker for private notes, user ids) comes from settings; the defaults below are generic."""
import datetime
import os
import re
import shutil
import struct
import threading

LOCK = threading.RLock()
EXT = ('.jpg', '.jpeg', '.png')
DEFAULT_SKIP = r'licen[cs]e|passport|receipt|insurance|bank|card|statement|\bid\b|private'
FAMILY_RE = re.compile(r'by-(topic|month)/[A-Za-z0-9._-]+')


def have_pillow():
    try:
        import PIL.Image  # noqa: F401
        return True
    except Exception:
        return False


def slug(s, empty='unsorted'):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-') or empty


def exif_date(p):
    """DateTimeOriginal from a JPEG without Pillow (same reader as the old script); None if absent."""
    try:
        with open(p, 'rb') as fh:
            d = fh.read(131072)
        if d[:2] != b'\xff\xd8':
            return None
        i = 2
        while i + 4 < len(d) and d[i] == 0xFF:
            m = d[i + 1]
            ln = struct.unpack('>H', d[i + 2:i + 4])[0]
            if m == 0xE1 and d[i + 4:i + 10] == b'Exif\0\0':
                t = d[i + 10:i + 2 + ln]
                e = '<' if t[:2] == b'II' else '>'

                def u16(o): return struct.unpack(e + 'H', t[o:o + 2])[0]

                def u32(o): return struct.unpack(e + 'I', t[o:o + 4])[0]

                def ifd(off):
                    out = {}
                    for k in range(u16(off)):
                        o = off + 2 + 12 * k
                        out[u16(o)] = (u16(o + 2), u32(o + 4), u32(o + 8))
                    return out
                i0 = ifd(u32(4))
                res = None
                for src in (ifd(i0[0x8769][2]) if 0x8769 in i0 else {}, i0):
                    for tag in (0x9003, 0x0132):
                        if tag in src:
                            o = src[tag][2]
                            res = t[o:o + 19].decode('ascii', 'ignore')
                            break
                    if res:
                        break
                if res and re.match(r'\d{4}:\d\d:\d\d', res):
                    return res[:4] + '-' + res[5:7] + '-' + res[8:10]
                return None
            i += 2 + ln
    except Exception:
        pass
    return None


def name_date(s):
    m = re.search(r'(20\d\d)[-_]?([01]\d)[-_]?([0-3]\d)', s)
    if m:
        try:
            datetime.date(int(m[1]), int(m[2]), int(m[3]))
            return '%s-%s-%s' % m.groups()
        except ValueError:
            pass
    return None


def skip_regex(s):
    """Compiled file-name filter from settings (bad pattern -> the generic default)."""
    try:
        return re.compile(str(s.get('skip_name_regex') or DEFAULT_SKIP), re.I)
    except re.error:
        return re.compile(DEFAULT_SKIP, re.I)


def num_or(v, d):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def int_or(v, d):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


# ---------------- catalogue scan ----------------
def note_list(brain):
    notes = []
    for r, ds, fs in os.walk(brain):
        ds[:] = [d for d in ds if d not in ('.history', 'attachments')]
        notes += [os.path.join(r, f) for f in fs if f.endswith('.md')]
    return notes


def scan(brain, media_sources, skip, marker, old):
    """-> (photos, skipped). Same rules as the old script: skipped = file name matches `skip`, or the first note that mentions the file has `marker` in its path.
    old = {src: entry} of the previous index (captions are kept)."""
    srcs = [(os.path.join(brain, 'attachments'), 'brain')] + [(m, 'media') for m in media_sources]
    notes, texts = note_list(brain), {}

    def linking(base):
        for n in notes:
            if n not in texts:
                with open(n, errors='ignore') as fh:
                    texts[n] = fh.read()
            if base in texts[n]:
                return n
    photos, skipped = [], []
    for root, kind in srcs:
        for r, ds, fs in os.walk(root):
            for f in sorted(fs):
                if not f.lower().endswith(EXT):
                    continue
                p = os.path.join(r, f)
                if skip.search(f):
                    skipped.append((p, 'sensitive filename'))
                    continue
                note = linking(f) if kind == 'brain' else None
                if note and marker and marker in note:
                    skipped.append((p, 'private note'))
                    continue
                folder = os.path.basename(r)
                date = exif_date(p) or name_date(f) or (name_date(folder) if kind == 'brain' else None) \
                    or datetime.date.fromtimestamp(os.path.getmtime(p)).isoformat()
                if kind == 'media':
                    topic = slug(folder)
                elif note:
                    parts = os.path.relpath(note, brain).split('/')
                    topic = slug(parts[-2] if len(parts) > 1 else 'unsorted')
                else:
                    topic = 'unsorted'
                stem = os.path.splitext(f)[0]
                e = old.get(p, {})
                photos.append({'src': p, 'file': f, 'date': date, 'topic': topic,
                               'note': os.path.relpath(note, brain) if note else '',
                               'caption': e.get('caption', ''), 'album': ('%s.jpg' % slug(stem)) if stem.startswith(date) else '%s-%s.jpg' % (date, slug(stem))})
    photos.sort(key=lambda x: (x['date'], x['file']))
    return photos, skipped


def plan_jobs(photos, media_out, big=1600, small=400):
    """Fill x['paths'] = [by-topic path, by-month path] and return the resize jobs (full size + thumbnails), exactly like the old script."""
    jobs = []
    for x in photos:
        t = os.path.join(media_out, 'by-topic', x['topic'], x['album'])
        m = os.path.join(media_out, 'by-month', x['date'][:7], x['album'])
        x['paths'] = [t, m]
        jobs.append({'src': x['src'], 'dst': t, 'max': big})
        for q in (t, m):
            jobs.append({'src': x['src'], 'dst': os.path.join(media_out, '_thumbs', os.path.relpath(q, media_out)), 'max': small})
    return jobs


# ---------------- resizing (Pillow) ----------------
def resize_one(src, dst, mx, quality=82):
    """JPEG copy of src, longest side at most mx (never enlarged), EXIF dropped, orientation applied. Raises on unreadable input."""
    from PIL import Image, ImageOps
    with Image.open(src) as im0:
        im = ImageOps.exif_transpose(im0)
        w, h = im.size
        if max(w, h) > mx:
            sc = mx / float(max(w, h))
            im = im.resize((max(1, round(w * sc)), max(1, round(h * sc))), Image.LANCZOS)
        if im.mode in ('RGBA', 'LA', 'P'):
            im = im.convert('RGBA')
            bg = Image.new('RGB', im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != 'RGB':
            im = im.convert('RGB')
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + '.tmp'
        try:
            im.save(tmp, 'JPEG', quality=quality)
            os.replace(tmp, dst)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


def resize_jobs(jobs, quality=82, log=None):
    """Run jobs [{src, dst, max}] like the old node helper: an existing dst is skipped, a failure is logged and the loop goes on. -> number written."""
    n = 0
    for j in jobs:
        if os.path.exists(j['dst']):
            continue
        try:
            resize_one(j['src'], j['dst'], j['max'], quality)
            n += 1
        except Exception as e:
            if log:
                log('resize failed for %s (%s)' % (os.path.basename(j['src']), type(e).__name__))
    return n


# ---------------- file system helpers ----------------
def own(path, uid, gid, mode):
    try:
        os.chown(path, uid, gid)
    except OSError:
        pass
    os.chmod(path, mode)


def mkdir_share(d, uid, gid):
    if not os.path.isdir(d):
        mkdir_share(os.path.dirname(d), uid, gid)
        os.mkdir(d, 0o755)
        own(d, uid, gid, 0o755)


def link(src, dst):
    """Hard link src as dst (copy if the file system refuses); replaces a different file at dst."""
    if not os.path.exists(src):
        return
    if os.path.exists(dst):
        try:
            if os.path.samefile(src, dst):
                return
        except OSError:
            pass
        os.remove(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def link_months(photos, dry=False):
    """by-month hard links of the by-topic files; -> count made."""
    n = 0
    for x in photos:
        t, m = x['paths']
        if os.path.exists(t) and not os.path.exists(m):
            n += 1
            if dry:
                continue
            os.makedirs(os.path.dirname(m), exist_ok=True)
            try:
                os.link(t, m)
            except OSError:
                shutil.copy2(t, m)
    return n


def share_sync(photos, media_out, share_out, uid=1000, gid=1000, dry=False):
    """Copy (not link) the albums into the private file-share folder. Prunes everything not wanted except `my-albums` (owned by photo_albums). -> (copied, removed)."""
    if not share_out or not os.path.isdir(os.path.dirname(share_out)):
        return 0, 0
    want, n = set(), 0
    for x in photos:
        for p in x['paths']:
            if not os.path.exists(p):
                continue
            q = os.path.join(share_out, os.path.relpath(p, media_out))
            want.add(q)
            if os.path.exists(q) and os.path.getsize(q) == os.path.getsize(p) and os.path.getmtime(q) >= os.path.getmtime(p):
                continue
            n += 1
            if dry:
                continue
            mkdir_share(os.path.dirname(q), uid, gid)
            shutil.copyfile(p, q)
            own(q, uid, gid, 0o644)
    gone = 0
    if os.path.isdir(share_out):
        for r, ds, fs in os.walk(share_out, topdown=False):
            if os.path.relpath(r, share_out).split(os.sep)[0] == 'my-albums':
                continue
            for f in fs:
                q = os.path.join(r, f)
                if q not in want:
                    gone += 1
                    if not dry:
                        os.remove(q)
            if not dry and r != share_out and not os.listdir(r):
                os.rmdir(r)
    return n, gone


def family_sync(photos, media_out, picked, dry=False):
    """Copy ONLY the picked auto albums ('by-topic/<topic>' or 'by-month/<YYYY-MM>') to <media_out>/_family (full size + _thumbs) and prune everything else there
    except `my-albums` (owned by photo_albums). `photos` must already be free of skipped/private items. -> (picked count, copied, removed)."""
    picked = {x for x in (str(y).strip('/') for y in (picked or [])) if FAMILY_RE.fullmatch(x)}
    fam = os.path.join(media_out, '_family')
    if not dry:
        os.makedirs(fam, exist_ok=True)
    want, n = set(), 0
    for x in photos:
        for p in x['paths']:
            if not os.path.exists(p):
                continue
            rel = os.path.relpath(p, media_out)
            if os.path.dirname(rel) not in picked:
                continue
            for src, dst in ((p, os.path.join(fam, rel)), (os.path.join(media_out, '_thumbs', rel), os.path.join(fam, '_thumbs', rel))):
                if not os.path.exists(src):
                    continue
                want.add(dst)
                if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src) and os.path.getmtime(dst) >= os.path.getmtime(src):
                    continue
                n += 1
                if dry:
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)

    def mine(q):
        t = os.path.relpath(q, fam).split(os.sep)
        return t[:1] == ['my-albums'] or t[:2] == ['_thumbs', 'my-albums']
    gone = 0
    if os.path.isdir(fam):
        for r, ds, fs in os.walk(fam, topdown=False):
            if mine(r):
                continue
            for f in fs:
                q = os.path.join(r, f)
                if q not in want:
                    gone += 1
                    if not dry:
                        os.remove(q)
            if not dry and r != fam and not os.listdir(r):
                os.rmdir(r)
    return len(picked), n, gone
