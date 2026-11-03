"""One-line logs that never contain secrets. Option names ending in key / token / password / secret are secret: their VALUES are scrubbed from every line."""
import re

SECRET_SUFFIXES = ('key', 'token', 'password', 'secret')
MIN_SECRET_LEN = 4


def is_secret_name(name):
    return str(name).lower().rstrip('_').endswith(SECRET_SUFFIXES)


def secret_values(opts):
    """Values of secret-named options (strings of 4+ characters)."""
    return [str(v) for k, v in (opts or {}).items() if is_secret_name(k) and isinstance(v, str) and len(v) >= MIN_SECRET_LEN]


def masked(opts):
    """A copy of opts that is safe to print: secret-named values replaced by 'set' / 'unset'."""
    return {k: (('set' if v else 'unset') if is_secret_name(k) else v) for k, v in (opts or {}).items()}


def scrub(text, secrets):
    t = str(text)
    for s in sorted(set(secrets or ()), key=len, reverse=True):
        if s:
            t = t.replace(s, '***')
    return t


def one_line(text, limit=300):
    return re.sub(r'\s+', ' ', str(text)).strip()[:limit]


class Logger:
    """Logger(sink, secrets=[...], prefix='name'). Call it like a function: log('text'). Always one line, secrets scrubbed."""
    def __init__(self, sink=print, secrets=(), prefix=''):
        self.sink, self.secrets, self.prefix = sink, list(secrets), prefix

    @classmethod
    def from_options(cls, opts, sink=print, prefix=''):
        return cls(sink, secret_values(opts), prefix)

    def __call__(self, msg, **kv):
        parts = [str(msg)] + ['%s=%s' % (k, v) for k, v in kv.items() if not is_secret_name(k)]
        line = one_line(scrub(' '.join(parts), self.secrets))
        self.sink(('[%s] %s' % (self.prefix, line)) if self.prefix else line)
        return line
