"""Game modules. Interface per module:
NAME (str), TITLES (names it matches), matches(entry)->bool, summary(tc, player)->str, upcoming(tc, player)->[{when,text}],
commands: dict name -> fn(tc, player, args:list[str]) -> short text. Optional UI_COMPONENT names a standalone
app/ui/games/<name>.js component that owns this game's detail presentation. The shared library discovers and renders it.
tc = ToolCtx (data_dir, opts, now, opener, log). Modules read cached data and never block for long."""
import datetime, urllib.request


class ToolCtx:
    def __init__(self, data_dir, opts=None, now=None, opener=None, log=None):
        self.data_dir, self.opts = data_dir, opts or {}
        self._now = now
        self.opener = opener or urllib.request.urlopen
        self.log = log or (lambda m: None)

    def now(self):
        return self._now() if self._now else datetime.datetime.now(datetime.timezone.utc)

    def open(self, req, timeout=20):
        return self.opener(req, timeout=timeout)


def all_tools():
    """Discover game modules; their optional UI_COMPONENT points to app/ui/games/<name>.js."""
    import importlib, pkgutil
    out = []
    for m in sorted(pkgutil.iter_modules(__path__), key=lambda x: x.name):
        if m.name.startswith("_"):
            continue
        mod = importlib.import_module("tools." + m.name)
        if hasattr(mod, "NAME") and hasattr(mod, "matches"):
            out.append(mod)
    return out
