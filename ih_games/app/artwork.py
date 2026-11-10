"""Find official game cover art through IH AI Layer and keep a local copy."""
import datetime, ipaddress, json, os, re, socket, struct, tempfile, threading, urllib.error, urllib.parse, urllib.request

BYTES_PER_MB = 1024 * 1024
MAX_IMAGE_BYTES = 6 * BYTES_PER_MB
UPLOAD_SOURCE_TITLE = 'Uploaded by the household'
PNG_SIZE_OFFSET = 16            # width and height (two big-endian 32-bit numbers) in a PNG header
JPEG_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}      # start-of-frame markers that carry the size
WEBP_CHUNK_OFFSET = 12
WEBP_LOSSY_SIZE_MASK = 0x3FFF
IMAGE_TIMEOUT_S = 20            # download of one cover image
LAYER_TIMEOUT_S = 35            # one AI Layer art search (it may try more than one helper)
MAX_LAYER_REPLY_BYTES = 128 * 1024
MAX_CANDIDATES_TRIED = 5        # returned images tried before giving up
REQUEST_ID_CHARS = 20
MAX_SOURCE_TITLE_CHARS = 180
_LOCK = threading.Lock()
OFFICIAL_SOURCES = (
    'store.steampowered.com', 'store.playstation.com', 'playstation.com', 'xbox.com', 'microsoft.com',
    'nintendo.com', 'epicgames.com', 'gog.com', 'bungie.net', 'ea.com', 'ubisoft.com', 'activision.com',
    'rockstargames.com', 'cdprojektred.com', 'embark-studios.com', 'arcraiders.com', 'bandainamcoent.com',
    'capcom.com', 'sega.com', 'atlus.com', 'thqnordic.com', '2k.com', 'take2games.com', 'gearboxsoftware.com',
    'bethesda.net', 'bethesda.com', 'blizzard.com', 'riotgames.com', 'larian.com', 'teamcherry.com',
    'devolverdigital.com', 'focus-entmt.com', 'deep-silver.com', '505games.com', 'konami.com', 'square-enix-games.com',
)


class ArtError(Exception):
    pass


def _public_https(url):
    try:
        u = urllib.parse.urlsplit(str(url))
        if u.scheme != 'https' or not u.hostname or u.username or u.password or len(str(url)) > 2048:
            return False
        host = u.hostname.rstrip('.').lower()
        addresses = {ipaddress.ip_address(x[4][0]) for x in socket.getaddrinfo(host, u.port or 443, type=socket.SOCK_STREAM)}
        return bool(addresses) and all(ip.is_global for ip in addresses)
    except (ValueError, OSError):
        return False


def _official_source(url):
    try:
        u = urllib.parse.urlsplit(str(url))
        host = (u.hostname or '').rstrip('.').lower()
        return u.scheme == 'https' and any(host == d or host.endswith('.' + d) for d in OFFICIAL_SOURCES)
    except ValueError:
        return False


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _public_https(newurl):
            raise urllib.error.URLError('redirect target refused')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(injected=None):
    return injected or urllib.request.build_opener(_SafeRedirect()).open


def _read_image(url, opener=None):
    if not _public_https(url):
        raise ArtError('The image host is not a public HTTPS address.')
    req = urllib.request.Request(url, headers={'User-Agent': 'IntelligentHomeGames/0.6', 'Accept': 'image/avif,image/webp,image/png,image/jpeg'})
    try:
        with _opener(opener)(req, timeout=IMAGE_TIMEOUT_S) as response:
            final_url = response.geturl() if hasattr(response, 'geturl') else url
            if not _public_https(final_url):
                raise ArtError('The image redirect target was refused.')
            content_type = str(response.headers.get('Content-Type', '')).split(';', 1)[0].strip().lower()
            data = response.read(MAX_IMAGE_BYTES + 1)
    except ArtError:
        raise
    except Exception as e:
        raise ArtError('Could not download the official cover image (%s).' % type(e).__name__)
    if len(data) > MAX_IMAGE_BYTES:
        raise ArtError('The cover image is larger than 6 MB.')
    signatures = {
        'image/jpeg': (data.startswith(b'\xff\xd8\xff'), '.jpg'),
        'image/png': (data.startswith(b'\x89PNG\r\n\x1a\n'), '.png'),
        'image/webp': (data.startswith(b'RIFF') and data[8:12] == b'WEBP', '.webp'),
    }
    if content_type not in signatures or not signatures[content_type][0]:
        raise ArtError('The selected result was not a supported image file.')
    return data, content_type, signatures[content_type][1]



def image_size(data):
    """(width, height) read from the image's own header (JPEG, PNG or WebP), or None when it cannot be read."""
    try:
        if data.startswith(b'\x89PNG\r\n\x1a\n'):
            return struct.unpack('>II', data[PNG_SIZE_OFFSET:PNG_SIZE_OFFSET + 8])
        if data.startswith(b'\xff\xd8'):
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker == 0xFF:
                    i += 1
                    continue
                if marker in JPEG_SOF_MARKERS:
                    h, w = struct.unpack('>HH', data[i + 5:i + 9])
                    return w, h
                i += 2 + struct.unpack('>H', data[i + 2:i + 4])[0]
            return None
        if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
            kind = data[WEBP_CHUNK_OFFSET:WEBP_CHUNK_OFFSET + 4]
            if kind == b'VP8 ':
                return (struct.unpack('<H', data[26:28])[0] & WEBP_LOSSY_SIZE_MASK, struct.unpack('<H', data[28:30])[0] & WEBP_LOSSY_SIZE_MASK)
            if kind == b'VP8L':
                bits = int.from_bytes(data[21:25], 'little')
                return (bits & WEBP_LOSSY_SIZE_MASK) + 1, ((bits >> 14) & WEBP_LOSSY_SIZE_MASK) + 1
            if kind == b'VP8X':
                return int.from_bytes(data[24:27], 'little') + 1, int.from_bytes(data[27:30], 'little') + 1
    except (struct.error, IndexError):
        pass
    return None


def _is_portrait(size):
    return bool(size) and size[1] > size[0]


def find_and_store(opts, data_dir, game, opener=None, now=None, require_portrait=False):
    """Search through a capability-limited AI Layer token, verify the source, and atomically persist one image.
    Portrait images are preferred; with require_portrait (a refresh of existing art) a portrait image is mandatory."""
    gid = str(game.get('id') or '')
    title = str(game.get('title') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', gid) or not title:
        raise ArtError('This game cannot be identified safely.')
    base = str(opts.get('ai_layer_url') or '').strip().rstrip('/')
    token = str(opts.get('ai_layer_art_token') or '').strip()
    if not base or not token:
        raise ArtError('Configure the Games art connection in the add-on settings first.')
    platform = str(game.get('platform') or '').strip()
    # Just the title and platform: the AI Layer routes this to whichever helper suits (search, store lookup...) and each builds its own query.
    body = {'request_id': 'games-art-' + __import__('uuid').uuid4().hex[:REQUEST_ID_CHARS], 'capability': 'game_art_search', 'text': title,
            'context': {'title': title, 'platform': platform, 'domains': list(OFFICIAL_SOURCES)}}      # helpers keep only results from these sites
    req = urllib.request.Request(base + '/request', data=json.dumps(body).encode(), method='POST',
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with _opener(opener)(req, timeout=LAYER_TIMEOUT_S) as response:
            ai_result = json.loads(response.read(MAX_LAYER_REPLY_BYTES))
    except Exception as e:
        raise ArtError('IH AI Layer could not search for cover art (%s).' % type(e).__name__)
    if not ai_result.get('ok'):
        answer = str(ai_result.get('answer') or '')
        why = str(ai_result.get('error') or '')
        if 'No agent is available for capability' in answer:
            raise ArtError('No artwork search provider is available in IH AI Layer. Enable a provider with game_art_search, such as Serper, SerpApi or Steam Store.')
        if why == 'cap':
            raise ArtError('Every artwork search provider has used its daily limit. Try again tomorrow, or raise a provider limit in IH AI Layer.')
        if why == 'not_configured':
            raise ArtError('The artwork search provider in IH AI Layer has no key. Add one in its settings.')
        if why == 'upstream_error' or 'did not answer' in answer:
            raise ArtError('The artwork search providers in IH AI Layer did not answer (timeout or provider error). Try again shortly.')
        raise ArtError('IH AI Layer could not complete the artwork search. Check that an artwork provider is enabled and available.')
    answer = ai_result.get('answer')
    if isinstance(answer, str) and answer.startswith('[dry] shadow mode:'):
        raise ArtError('IH AI Layer is in shadow mode, so it did not run the artwork search. Set shadow to false to enable searches.')
    try:
        candidates = json.loads(answer or '[]')
    except (TypeError, ValueError):
        candidates = []
    if not isinstance(candidates, list):
        candidates = []
    usable = [x for x in candidates if isinstance(x, dict) and _official_source(x.get('source_url')) and x.get('image_url')]
    if not usable:
        raise ArtError('No image from a recognized official publisher or store was found.')
    chosen = fallback = None      # chosen: first portrait image; fallback: first image of any shape
    last_error = None
    for cand in usable[:MAX_CANDIDATES_TRIED]:
        try:
            data, content_type, suffix = _read_image(str(cand['image_url']), opener=opener)
        except ArtError as e:
            last_error = e
            continue
        size = image_size(data) or ((cand.get('width'), cand.get('height')) if cand.get('width') and cand.get('height') else None)
        found = (cand, data, content_type, suffix, size)
        if _is_portrait(size):
            chosen = found
            break
        fallback = fallback or found
    if chosen is None and require_portrait:
        raise last_error if (fallback is None and last_error) else ArtError('No portrait cover art was found.')
    chosen = chosen or fallback
    if chosen is None:
        raise last_error
    cand, data, content_type, suffix, size = chosen
    return _save(data_dir, gid, title, data, content_type, suffix, str(cand['source_url']),
                 str(cand.get('title') or '')[:MAX_SOURCE_TITLE_CHARS], now)


def _save(data_dir, gid, title, data, content_type, suffix, source_url, source_title, now=None):
    """Atomically persist one image plus its metadata under /data/artwork, replacing any older image for the game."""
    art_dir = os.path.join(data_dir, 'artwork')
    os.makedirs(art_dir, mode=0o700, exist_ok=True)
    filename = gid + suffix
    image_path = os.path.join(art_dir, filename)
    meta_path = os.path.join(art_dir, gid + '.json')
    fetched = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec='seconds')
    metadata = {'game_id': gid, 'title': title, 'file': filename, 'content_type': content_type,
                'source_url': source_url, 'source_title': source_title, 'fetched_at': fetched}
    with _LOCK:
        for old in ('.jpg', '.png', '.webp'):
            stale = os.path.join(art_dir, gid + old)
            if stale != image_path and os.path.exists(stale):
                os.unlink(stale)
        fd, tmp = tempfile.mkstemp(prefix=gid + '.', dir=art_dir)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            os.replace(tmp, image_path)
            fd, mt = tempfile.mkstemp(prefix=gid + '.', dir=art_dir)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False)
            os.replace(mt, meta_path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return metadata


def sniff_image(data):
    """(content_type, suffix) from the file's own first bytes (the browser's label is not trusted), or None."""
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg', '.jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png', '.png'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return 'image/webp', '.webp'
    return None


def store_uploaded(data_dir, game, data, now=None):
    """Keep an image the user uploaded as this game's cover art. Only JPEG, PNG and WebP up to MAX_IMAGE_BYTES are accepted."""
    gid = str(game.get('id') or '')
    title = str(game.get('title') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', gid) or not title:
        raise ArtError('This game cannot be identified safely.')
    if not data:
        raise ArtError('The uploaded file was empty.')
    if len(data) > MAX_IMAGE_BYTES:
        raise ArtError('The image is larger than %d MB.' % (MAX_IMAGE_BYTES // BYTES_PER_MB))
    kind = sniff_image(data)
    if not kind:
        raise ArtError('That file is not a JPEG, PNG or WebP image.')
    return _save(data_dir, gid, title, data, kind[0], kind[1], '', UPLOAD_SOURCE_TITLE, now)
