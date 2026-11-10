"""Find official game cover art through IH AI Layer and keep a local copy."""
import datetime, ipaddress, json, os, re, socket, tempfile, threading, urllib.error, urllib.parse, urllib.request

MAX_IMAGE_BYTES = 6 * 1024 * 1024
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
        with _opener(opener)(req, timeout=20) as response:
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


def find_and_store(opts, data_dir, game, opener=None, now=None):
    """Search through a capability-limited AI Layer token, verify the source, and atomically persist one image."""
    gid = str(game.get('id') or '')
    title = str(game.get('title') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', gid) or not title:
        raise ArtError('This game cannot be identified safely.')
    base = str(opts.get('ai_layer_url') or '').strip().rstrip('/')
    token = str(opts.get('ai_layer_art_token') or '').strip()
    if not base or not token:
        raise ArtError('Configure the Games art connection in the add-on settings first.')
    query = 'Find official retail box cover art for the video game %r on %s. Return official publisher or platform store image results only.' % (title, str(game.get('platform') or 'any platform'))
    body = {'request_id': 'games-art-' + __import__('uuid').uuid4().hex[:20], 'capability': 'game_art_search', 'text': query}
    req = urllib.request.Request(base + '/request', data=json.dumps(body).encode(), method='POST',
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with _opener(opener)(req, timeout=35) as response:
            ai_result = json.loads(response.read(128 * 1024))
    except Exception as e:
        raise ArtError('IH AI Layer could not search for cover art (%s).' % type(e).__name__)
    if not ai_result.get('ok'):
        answer = str(ai_result.get('answer') or '')
        if 'No agent is available for capability' in answer:
            raise ArtError('No artwork search provider is available in IH AI Layer. Enable a provider with game_art_search, such as SerpApi.')
        if 'did not answer' in answer:
            raise ArtError('The IH AI Layer artwork search provider did not answer. Check its status and daily quota.')
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
    chosen = next((x for x in candidates if isinstance(x, dict) and _official_source(x.get('source_url')) and x.get('image_url')), None)
    if not chosen:
        raise ArtError('No image from a recognized official publisher or store was found.')
    data, content_type, suffix = _read_image(str(chosen['image_url']), opener=opener)
    art_dir = os.path.join(data_dir, 'artwork')
    os.makedirs(art_dir, mode=0o700, exist_ok=True)
    filename = gid + suffix
    image_path = os.path.join(art_dir, filename)
    meta_path = os.path.join(art_dir, gid + '.json')
    fetched = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec='seconds')
    metadata = {'game_id': gid, 'title': title, 'file': filename, 'content_type': content_type,
                'source_url': str(chosen['source_url']), 'source_title': str(chosen.get('title') or '')[:180], 'fetched_at': fetched}
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
