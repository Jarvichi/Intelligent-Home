"""Reads Home Assistant state and history, learns per room, builds the three outputs. READ-ONLY toward devices:
the only write is publishing our own sensor states (live mode); no service call exists in this add-on."""
from datetime import datetime, timedelta, timezone

import insights as I

VERSION = '0.2.2'
MIGRATIONS = [
    (1, 'CREATE TABLE runs(room TEXT NOT NULL, start TEXT NOT NULL, minutes REAL, rise REAL, min_per_deg REAL, PRIMARY KEY(room,start))'),
]
DEFAULT_EXCLUDE = ('underfloor', 'volkswagen', 'troc', 't_roc', 'garage')


def utcnow():
    return datetime.now(timezone.utc)


class Engine:
    def __init__(self, db, ha, opts, clock=utcnow, log=lambda *a: None):
        self.db, self.ha, self.opts, self.clock, self.log = db, ha, opts, clock, log
        self.last = {}

    # ---- discovery ----
    def trvs(self):
        want = [e for e in (self.opts.get('rooms') or []) if e]
        if want:
            return want
        ex = tuple(x.lower() for x in (self.opts.get('exclude') or [])) or DEFAULT_EXCLUDE
        out = []
        for s in self.ha.get('states') or []:
            e = s.get('entity_id', '')
            if e.startswith('climate.') and e != self.opts.get('nest_entity', 'climate.hallway_thermostat') \
                    and not any(x in e for x in ex) and s.get('state') != 'unavailable':
                out.append(e)
        return sorted(out)

    def humidity_entities(self):
        want = [e for e in (self.opts.get('humidity_sensors') or []) if e]
        if want:
            return want
        return sorted(s['entity_id'] for s in (self.ha.get('states') or [])
                      if s.get('entity_id', '').startswith('sensor.') and (s.get('attributes') or {}).get('device_class') == 'humidity')

    # ---- learning ----
    def learn(self, room, samples):
        new = 0
        for r in I.warmup_runs(samples):
            cur = self.db.con.execute('SELECT 1 FROM runs WHERE room=? AND start=?', (room, r['start'])).fetchone()
            if not cur:
                self.db.execute('INSERT INTO runs(room,start,minutes,rise,min_per_deg) VALUES(?,?,?,?,?)',
                                (room, r['start'], r['minutes'], r['rise'], r['min_per_deg']))
                new += 1
        rows = self.db.query('SELECT min_per_deg FROM runs WHERE room=? ORDER BY start', (room,))
        vals = [x[0] for x in rows]
        return I.median_min_per_deg(vals), len(vals), new

    def run_once(self):
        now = self.clock()
        start = now - timedelta(days=int(self.opts.get('history_days', 14)))
        rooms, frozen = {}, []
        for ent in self.trvs():
            try:
                samples = I.samples_from_history(self.ha.history(ent, start, now))
            except Exception as e:
                self.log('history failed for %s (%s)' % (ent, type(e).__name__))
                continue
            mpd, n, _ = self.learn(ent, samples)
            real = [s for s in samples if s['cur'] is not None]
            cur, tgt = (real[-1]['cur'], real[-1]['target']) if real else (None, None)
            sus, why = I.frozen_check(samples, now, float(self.opts.get('frozen_hours', I.FROZEN_HOURS)))
            rooms[ent] = {'room': I.slug(ent), 'current': cur, 'target': tgt, 'min_per_deg': mpd, 'runs': n,
                          'minutes_to_target': I.minutes_to_target(cur, tgt, mpd), 'frozen': sus, 'frozen_why': why}
            if sus:
                frozen.append({'room': I.slug(ent), 'entity': ent, 'why': why, 'since': I.reading_since(samples)})
        mould = {}
        pct, hrs = float(self.opts.get('mould_pct', I.MOULD_PCT)), float(self.opts.get('mould_hours', I.MOULD_HOURS))
        for ent in self.humidity_entities():
            try:
                hs = I.humidity_samples(self.ha.history(ent, now - timedelta(hours=max(24, hrs * 6)), now))
            except Exception as e:
                self.log('history failed for %s (%s)' % (ent, type(e).__name__))
                continue
            mould[ent] = dict(I.mould_check(hs, now, pct, hrs), room=I.slug(ent))
        self.last = {'at': now.isoformat(), 'version': VERSION, 'rooms': rooms, 'frozen': frozen, 'mould': mould,
                     'mould_pct': pct, 'mould_hours': hrs}
        return self.last

    # ---- publish (our own sensors only; live mode) ----
    def publish(self, out):
        w = lambda ent, state, attrs: self.ha.post('states/' + ent, {'state': state, 'attributes': attrs})
        for ent, r in out['rooms'].items():
            m = r['minutes_to_target']
            w('sensor.ih_warmup_' + r['room'], 'unknown' if m is None else m,
              {'unit_of_measurement': 'min', 'friendly_name': 'Warm-up %s' % r['room'].replace('_', ' '), 'minutes_per_degree': r['min_per_deg'],
               'runs_learned': r['runs'], 'current': r['current'], 'target': r['target'], 'source': 'ih_room_insights'})
        names = [f['room'] for f in out['frozen']]
        w('sensor.ih_frozen_valves', len(names), {'friendly_name': 'Suspect valves', 'rooms': names, 'detail': out['frozen'], 'source': 'ih_room_insights'})
        worst = 'ok'
        for m in out['mould'].values():
            if m['level'] == 'high' or (m['level'] == 'watch' and worst == 'ok'):
                worst = m['level']
        w('sensor.ih_mould_risk', worst, {'friendly_name': 'Mould risk', 'rooms': out['mould'], 'threshold_pct': out['mould_pct'],
                                          'hours': out['mould_hours'], 'source': 'ih_room_insights'})
