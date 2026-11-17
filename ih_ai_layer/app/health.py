"""Per-provider health: skip a provider that keeps failing (cool-down), and report its recent calls.

After `threshold` consecutive failures a provider cools down for `cooldown_s` seconds. When that time is over it is
allowed ONE trial request; a success closes the cool-down, a failure re-opens it at once. Only the error CLASS is kept
(never error text), so nothing private can reach the status page.
"""
import collections
import re
import threading
import time

WINDOW = 50
_TYPE_RE = re.compile(r'[^A-Za-z0-9_]')


def _etype(err):
    return _TYPE_RE.sub('_', str(err or 'error'))[:40] or 'error'


class ProviderHealth:
    def __init__(self, clock=time.time, threshold=5, cooldown_s=1800):
        self.clock = clock
        self.threshold = max(1, int(threshold))
        self.cooldown_s = max(1, int(cooldown_s))
        self.lock = threading.Lock()
        self.calls = {}      # name -> deque of (ok, err_type), newest last, max WINDOW
        self.streak = {}     # name -> consecutive failures
        self.until = {}      # name -> cool-down end (clock seconds)
        self.last_error = {}  # name -> error class of the last failure

    def record(self, name, ok, err_type=None):
        with self.lock:
            self.calls.setdefault(name, collections.deque(maxlen=WINDOW)).append((bool(ok), None if ok else _etype(err_type)))
            if ok:
                self.streak[name] = 0
                self.until.pop(name, None)
                return
            self.last_error[name] = _etype(err_type)
            self.streak[name] = self.streak.get(name, 0) + 1
            if self.streak[name] >= self.threshold:
                # reached the limit, or a failed trial after a cool-down: (re)open it at once
                self.until[name] = self.clock() + self.cooldown_s

    def available(self, name):
        """False only while the cool-down is running. Once it is over the provider is available again (the trial request)."""
        with self.lock:
            return self.clock() >= self.until.get(name, 0)

    def cooling(self, name):
        return not self.available(name)

    def status(self):
        """name -> {state: ok|cooling_down|idle, until, last_error, streak, calls, ok, failed} for the last WINDOW calls."""
        now = self.clock()
        out = {}
        with self.lock:
            for name, dq in self.calls.items():
                good = sum(1 for ok, _ in dq if ok)
                u = self.until.get(name)
                out[name] = {'state': 'cooling_down' if u and now < u else 'ok', 'until': u if u and now < u else None,
                             'last_error': self.last_error.get(name), 'streak': self.streak.get(name, 0),
                             'calls': len(dq), 'ok': good, 'failed': len(dq) - good}
        return out
