"""Small JSON state store: one file per name under a folder (usually /data/<plugin>/). Atomic write (tmp + rename), mode 600."""
import json, os, re, tempfile


class Store:
    def __init__(self, folder):
        self.folder = folder

    def _path(self, name):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,60}', name) or name.startswith('.'):
            raise ValueError('bad state name')
        return os.path.join(self.folder, name + '.json')

    def load(self, name='state', default=None):
        try:
            with open(self._path(name)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {} if default is None else default

    def save(self, name, obj):
        os.makedirs(self.folder, exist_ok=True)
        path = self._path(name)
        fd, tmp = tempfile.mkstemp(prefix='.tmp-', dir=self.folder)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(obj, f)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return path

    def update(self, name, **changes):
        d = self.load(name)
        d.update(changes)
        self.save(name, d)
        return d
