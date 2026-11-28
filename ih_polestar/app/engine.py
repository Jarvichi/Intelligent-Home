"""ih_polestar engine: poll a source, store readings in /data/ih.db, publish polestar_* sensors (live and fixture modes only). Read-only."""
import time
from datetime import datetime, timezone

import sources as S

VERSION = '0.1.0'
MIGRATIONS = [
    (1, 'CREATE TABLE readings(id INTEGER PRIMARY KEY, at TEXT NOT NULL, source TEXT NOT NULL, battery_pct REAL, range_km REAL, charging TEXT, '
        'charger_connected INTEGER, odometer_km REAL, eta_full_min REAL)'),
    (2, 'CREATE INDEX readings_at ON readings(at)'),
]
KM_PER_MILE = S.KM_PER_MILE


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class Engine:
    def __init__(self, db, ha, opts, source=None, log=print, clock=time.time):
        self.db, self.ha, self.opts, self.log, self.clock = db, ha, opts, log, clock
        self.source = source if source is not None else S.make_source(opts, clock)
        self.last = {'reading': None, 'error': None, 'at': None}

    def run_once(self):
        """One poll. Never raises: an error is kept as a short type name (never the password)."""
        now = self.clock()
        self.last['at'] = iso(now)
        if self.source is None:
            self.last.update(error='waiting for car: no Polestar login set', reading=None)
            return None
        try:
            rd = S.parse_payload(self.source.fetch())
        except Exception as e:
            self.last['error'] = 'fetch failed (%s)' % type(e).__name__
            return None
        self.last.update(error=None, reading=dict(rd, at=iso(now), source=self.source.name))
        if all(rd[k] is None for k in ('battery_pct', 'range_km', 'odometer_km')):
            self.last['error'] = 'reply held no usable values'
            return None
        self.db.execute('INSERT INTO readings(at,source,battery_pct,range_km,charging,charger_connected,odometer_km,eta_full_min) VALUES(?,?,?,?,?,?,?,?)',
                        (iso(now), self.source.name, rd['battery_pct'], rd['range_km'], rd['charging'],
                         None if rd['charger_connected'] is None else int(rd['charger_connected']), rd['odometer_km'], rd['eta_full_min']))
        self.db.execute('DELETE FROM readings WHERE at < ?', (iso(now - int(self.opts.get('keep_days', 90)) * 86400),))
        return rd

    def publish(self, rd):
        """Only called in live/fixture mode. Sensors are plain states (vanish on a HA restart until the next poll). Fixture values are flagged."""
        fx = self.source.name == 'fixture'
        base = {'source': 'ih_polestar', 'fixture': fx, 'last_reading': self.last['reading']['at']}
        w = lambda ent, state, attrs: self.ha.post('states/sensor.' + ent, {'state': state, 'attributes': dict(base, **attrs)})
        w('polestar_battery', 'unknown' if rd['battery_pct'] is None else rd['battery_pct'],
          {'friendly_name': 'Polestar battery', 'unit_of_measurement': '%', 'device_class': 'battery'})
        km = rd['range_km']
        w('polestar_range', 'unknown' if km is None else round(km / KM_PER_MILE), {'friendly_name': 'Polestar range', 'unit_of_measurement': 'mi', 'range_km': None if km is None else round(km)})
        w('polestar_charging', rd['charging'], {'friendly_name': 'Polestar charging', 'charger_connected': rd['charger_connected'], 'minutes_to_full': rd['eta_full_min']})
        o = rd['odometer_km']
        w('polestar_odometer', 'unknown' if o is None else round(o / KM_PER_MILE), {'friendly_name': 'Polestar odometer', 'unit_of_measurement': 'mi', 'state_class': 'total_increasing'})
        rows = [{'id': 'battery', 'item': 'Battery', 'value': 'unknown' if rd['battery_pct'] is None else '%s %%' % round(rd['battery_pct'])},
                {'id': 'range', 'item': 'Range', 'value': 'unknown' if km is None else '%d mi' % round(km / KM_PER_MILE)},
                {'id': 'charging', 'item': 'Charging', 'value': rd['charging']},
                {'id': 'updated', 'item': 'Last update', 'value': self.last['reading']['at'].replace('T', ' ').replace('Z', ' UTC')}]
        w('polestar_summary', 'unknown' if rd['battery_pct'] is None else rd['battery_pct'], {'friendly_name': 'Polestar summary', 'unit_of_measurement': '%', 'rows': rows})
        w('polestar_last_update', self.last['reading']['at'], {'friendly_name': 'Polestar last update', 'device_class': 'timestamp'})

    def readings(self, limit=200):
        rows = self.db.query('SELECT at,source,battery_pct,range_km,charging,charger_connected,odometer_km FROM readings ORDER BY id DESC LIMIT ?', (int(limit),))
        return [dict(zip(('at', 'source', 'battery_pct', 'range_km', 'charging', 'charger_connected', 'odometer_km'), tuple(r))) for r in rows]

    def status(self):
        return {'version': VERSION, 'mode': self.opts.get('mode', 'dry'), 'source': getattr(self.source, 'name', None) or 'none', **self.last}
