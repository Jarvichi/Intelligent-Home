"""Single-display frame capabilities for an opt-in device adapter.

The HTTP layer must authenticate every request. A frame ID and opaque zone ID
are identifiers, not credentials. Descriptors never leave this process.
"""
import copy
import hashlib
import re
import secrets
import threading
import time

PNG = b'\x89PNG\r\n\x1a\n'
MAX_IMAGE = 8 * 1024 * 1024
ID = re.compile(r'^[0-9a-f]{32}$')


class DeviceFrames:
    def __init__(self, dispatch, clock=time.monotonic, ttl=900, maximum=8, dispatch_many=None):
        if not callable(dispatch) or ttl <= 0 or not 1 <= maximum <= 32:
            raise ValueError('invalid frame configuration')
        self.dispatch = dispatch
        self.dispatch_many = dispatch_many
        self.clock = clock
        self.ttl = ttl
        self.maximum = maximum
        self.lock = threading.Lock()
        self.frames = {}
        self.active = None

    def _prune(self):
        now = self.clock()
        for key in list(self.frames):
            # The e-ink picture may remain visible overnight. Only pending
            # frames expire; active actions still perform owner-side checks.
            if key != self.active and self.frames[key]['expires'] <= now:
                del self.frames[key]
                if self.active == key:
                    self.active = None

    def issue(self, image, zones):
        """Capture image and zones together; never expose action descriptors."""
        if not isinstance(image, bytes) or not image.startswith(PNG) or len(image) > MAX_IMAGE:
            raise ValueError('invalid picture')
        if not isinstance(zones, list) or len(zones) > 128:
            raise ValueError('invalid zones')
        entries = []
        for zone in zones:
            if not isinstance(zone, dict) or not isinstance(zone.get('action'), dict):
                raise ValueError('invalid zone')
            power=zone.get('power','mains')
            if power not in ('mains','battery'):raise ValueError('invalid power capability')
            preview=zone.get('preview','none')
            if preview not in ('none','dinner','home','birthdays','batch'):raise ValueError('invalid preview capability')
            box = zone.get('box')
            if not isinstance(box, (list, tuple)) or len(box) != 4 or any(type(x) is not int for x in box):
                raise ValueError('invalid box')
            x0, y0, x1, y1 = box
            if not (0 <= x0 < x1 <= 1072 and 0 <= y0 < y1 <= 1448):
                raise ValueError('invalid box')
            entries.append((list(box), copy.deepcopy(zone['action']),power,preview))
        with self.lock:
            self._prune()
            # Never evict the frame currently on screen to make room for prefetches.
            while len(self.frames) >= self.maximum:
                candidate = next((k for k in self.frames if k != self.active), None)
                if candidate is None:
                    raise ValueError('frame store full')
                del self.frames[candidate]
            key = secrets.token_hex(16)
            controls = {secrets.token_hex(16): {'box': box, 'action': action, 'power':power, 'preview':preview, 'used': False}
                        for box, action, power, preview in entries}
            self.frames[key] = {'image': image, 'sha256': hashlib.sha256(image).hexdigest(),
                                'controls': controls, 'expires': self.clock() + self.ttl,
                                'activated': False}
            return key

    def bundle(self, key):
        """Return immutable picture and public metadata for the same generation."""
        if not isinstance(key, str) or not ID.fullmatch(key):
            return None
        with self.lock:
            self._prune()
            frame = self.frames.get(key)
            if frame is None:
                return None
            return {'frame': key, 'sha256': frame['sha256'], 'image': frame['image'],
                    'zones': [{'id': k, 'box': list(v['box']),'power':v['power'],'preview':v['preview']} for k, v in frame['controls'].items()]}

    def activate(self, key, sha256):
        """Mark the actually displayed image active; retire the preceding one."""
        if not isinstance(key, str) or not ID.fullmatch(key) or not isinstance(sha256, str):
            return False
        with self.lock:
            self._prune()
            frame = self.frames.get(key)
            if frame is None or not secrets.compare_digest(frame['sha256'], sha256):
                return False
            if self.active == key:
                return True
            # An old picture cannot be resurrected after a newer one was displayed.
            if frame['activated']:
                return False
            frame['activated'] = True
            self.active = key
            return True

    def tap(self, key, zone):
        """Dispatch exactly one captured action for the current generation."""
        if isinstance(zone, str) and ',' in zone:
            return self.tap_many(key, zone.split(','))
        if not isinstance(key, str) or not ID.fullmatch(key) or not isinstance(zone, str) or not ID.fullmatch(zone):
            return {'ok': False, 'why': 'invalid frame or zone'}
        with self.lock:
            self._prune()
            frame = self.frames.get(key)
            entry = frame['controls'].get(zone) if frame and self.active == key else None
            if entry is None or entry['used']:
                return {'ok': False, 'why': 'stale frame or zone'}
            # Consume before dispatch: timeout, exception or retry must never act twice.
            entry['used'] = True
            try:
                result = self.dispatch(copy.deepcopy(entry['action']))
                return result if isinstance(result, dict) else {'ok': False, 'why': 'action unavailable'}
            except Exception:
                return {'ok': False, 'why': 'action unavailable'}

    def tap_many(self, key, zones):
        """Several taps made on one picture, sent together (ticking things off a list). Each zone is still used at most once."""
        if not isinstance(key, str) or not ID.fullmatch(key) or not 2 <= len(zones) <= 12 or len(set(zones)) != len(zones) \
                or any(not isinstance(z, str) or not ID.fullmatch(z) for z in zones):
            return {'ok': False, 'why': 'invalid frame or zone'}
        with self.lock:
            self._prune()
            frame = self.frames.get(key)
            if frame is None or self.active != key:
                return {'ok': False, 'why': 'stale frame or zone'}
            picked = []
            for zone in zones:
                entry = frame['controls'].get(zone)
                if entry is not None and not entry['used']:
                    entry['used'] = True   # consume before dispatch, as for a single tap
                    picked.append(copy.deepcopy(entry['action']))
            if not picked:
                return {'ok': False, 'why': 'stale frame or zone'}
            try:
                if callable(self.dispatch_many):
                    result = self.dispatch_many(picked)
                else:
                    results = [self.dispatch(action) for action in picked]
                    result = {'ok': any(isinstance(r, dict) and r.get('ok') is True for r in results)}
                return result if isinstance(result, dict) else {'ok': False, 'why': 'action unavailable'}
            except Exception:
                return {'ok': False, 'why': 'action unavailable'}
