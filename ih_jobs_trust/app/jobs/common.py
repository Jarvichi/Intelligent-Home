"""Shared helpers for the ih_jobs jobs. stdlib only. HA REST goes through the Supervisor proxy with the add-on's own SUPERVISOR_TOKEN."""
import os, urllib.request
from ihlib.ha import HA, REST_BASE

HA_BASE = REST_BASE
SUP_BASE = 'http://supervisor/'


class Ctx:
    """What a job gets. dry=True: reads are real, but set_state() only logs '[DRY] would set <entity> = <state>'."""
    def __init__(self, dry=True, log=print, token=None, opener=None, data_dir='/data', opts=None):
        self.dry, self.log, self.data_dir, self.opts = dry, log, data_dir, opts or {}
        self._token, self._opener = token, opener or urllib.request.urlopen

    @property
    def token(self):
        t = self._token if self._token is not None else os.environ.get('SUPERVISOR_TOKEN', '')
        if not t:
            raise RuntimeError('no SUPERVISOR_TOKEN')
        return t

    def open(self, req, timeout=30):
        """urlopen for non-HA calls (the Met Office API); the tests replace it through the opener."""
        return self._opener(req, timeout=timeout)

    def _call(self, base, path, data=None, timeout=30):
        c = HA(token=self.token, opener=self._opener, rest_base=base)
        return c.get(path, timeout) if data is None else c.post(path, data, timeout)

    def ha(self, path, data=None):
        """Read (data None) or POST to HA Core REST. Use set_state() for writing sensors."""
        return self._call(HA_BASE, path, data)

    def supervisor(self, path):
        return self._call(SUP_BASE, path)

    def set_state(self, entity, state, attributes):
        """The ONLY place a job writes to Home Assistant. Dry: log only."""
        if self.dry:
            self.log('[DRY] would set %s = %s' % (entity, state))
            return False
        self.ha('states/' + entity, {'state': state, 'attributes': attributes})
        return True
