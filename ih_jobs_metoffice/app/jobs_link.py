"""jobs_link: how a child job add-on and the parent "Intelligent Home - Jobs" add-on talk. stdlib only. SHARED SOURCE: sync_from_service.sh copies it
into addon_jobs/app and into each child add-on. Never edit the copies.

One shared secret (the parent's option register_key, the same value in each child's option register_key) signs every message in both directions:
  child -> parent  POST /register   body {name, slug, job, version, mode, url, port, healthy, status, ts}
  parent -> child  POST /run        body {job, ts}
Header X-IH-Signature: sha256 HMAC (hex) of the exact body bytes, keyed with the secret. The body carries ts (unix seconds); a message older (or newer)
than MAX_SKEW_S is refused, so a captured message cannot be replayed later. Empty secret = the link is OFF (nothing is sent, everything is refused)."""
import hashlib, hmac, json, time

HEADER = 'X-IH-Signature'
MAX_SKEW_S = 300


def sign(key, body):
    """-> hex signature of the body bytes."""
    return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def pack(key, obj, now=None):
    """-> (body bytes, headers dict). Adds ts. Raises ValueError when there is no key."""
    if not key:
        raise ValueError('no register_key')
    obj = dict(obj, ts=int(time.time() if now is None else now))
    body = json.dumps(obj, sort_keys=True).encode()
    return body, {HEADER: sign(key, body), 'Content-Type': 'application/json'}


def unpack(key, body, signature, now=None):
    """-> (object or None, reason). Never raises. Constant-time signature check, then freshness."""
    if not key:
        return None, 'link off (no register_key)'
    if not signature or not hmac.compare_digest(sign(key, body), str(signature)):
        return None, 'bad signature'
    try:
        obj = json.loads(body)
        ts = int(obj['ts'])
    except Exception:
        return None, 'bad body'
    if abs((time.time() if now is None else now) - ts) > MAX_SKEW_S:
        return None, 'stale'
    return obj, 'ok'
