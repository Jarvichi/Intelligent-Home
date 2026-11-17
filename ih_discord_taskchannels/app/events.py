"""Wake-up events for a local consumer: "an allowed user posted in task channel X".

Why: a consumer that answers in the task channels had to ask Discord itself every few seconds. The transport already reads these channels for this
module, so the module keeps a short numbered list and the consumer WAITS on it (`GET /events?after=N&wait=S`, a long poll).

An event holds a number, the channel key, the message id and a time. NEVER the message text, the author or the channel name: the consumer reads the
message itself. The list lives in memory only (a restart empties it and changes `boot`, which tells the consumer to check everything once).
"""
import collections, hmac, threading, time, uuid

MAX_EVENTS = 200          # events kept; older ones fall off (the consumer then sees a gap and checks everything once)
MAX_WAIT_S = 25           # longest long poll, below common HTTP client and proxy timeouts
MAX_BATCH = 100           # events per answer
MIN_TOKEN_LEN = 16        # shorter tokens are refused (as the bus signing key)


class EventLog:
    def __init__(self, clock=time.time, max_events=MAX_EVENTS, boot=None):
        self.clock, self.boot = clock, boot or uuid.uuid4().hex[:8]
        self.items = collections.deque(maxlen=max_events)
        self.last = 0
        self.cv = threading.Condition()

    def add(self, channel_key, msg_id):
        with self.cv:
            self.last += 1
            self.items.append({'n': self.last, 'channel_key': str(channel_key), 'msg_id': str(msg_id or ''), 'ts': int(self.clock())})
            self.cv.notify_all()
            return self.last

    def since(self, after, wait=0.0):
        """Events with n > after. Waits up to `wait` seconds (capped) for the first one. -> answer dict."""
        try:
            after = max(0, int(after))
        except (TypeError, ValueError):
            after = 0
        try:
            wait = max(0.0, min(float(wait), MAX_WAIT_S))
        except (TypeError, ValueError):
            wait = 0.0
        with self.cv:
            if after > self.last:
                after = 0          # the consumer's number is from an older run of this add-on
            if wait and self.last <= after:
                self.cv.wait_for(lambda: self.last > after, timeout=wait)
            ev = [dict(e) for e in self.items if e['n'] > after][:MAX_BATCH]
            first = self.items[0]['n'] if self.items else self.last + 1
            return {'ok': True, 'boot': self.boot, 'last': self.last, 'gap': after + 1 < first and self.last > after, 'events': ev}


def token_ok(header, token):
    """True only for 'Bearer <token>' with a configured token of a sensible length. Constant-time compare."""
    token = str(token or '')
    if len(token) < MIN_TOKEN_LEN or not isinstance(header, str) or not header.startswith('Bearer '):
        return False
    return hmac.compare_digest(header[7:].strip().encode(), token.encode())
