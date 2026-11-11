"""Met Office storage: legacy import (copy, verify, trash), restart, damaged and too-new files, degraded run of the job, history pruning,
snapshots, trash purge, status. Temp folders only; no network."""
import hashlib, json, os, shutil, sys, tempfile, unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import metodb
from jobs import metoffice
from jobs.common import Ctx
from ihlib import db as ihdb

START = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc).timestamp()
DAY = 86400
HOURS = 6
SERIES_PER_FETCH = 8


class Clock:
    def __init__(self, t=START):
        self.t = t

    def __call__(self):
        return self.t


def cache(fetched_at):
    """Real shape of the old metoffice_cache.json: percentile series (time-major values) and single-value series."""
    times = ['2026-11-12T%02d:00Z' % (6 + i) for i in range(HOURS)]
    pc = ['5', '25', '50', '75', '95']
    ps = {'airTemperature1p5m': 280.0, 'lweThicknessOfPrecipitationAmountSumPt01h': 0.0001, 'relativeHumidity1p5m': 0.8, 'windSpeed10m': 4.0,
          'windFromDirection10mMean': 200.0, 'cloudAreaFraction': 0.6, 'ultravioletIndex': 1.0}
    series = {n: {'t': times, 'pc': pc, 'v': [[b + j * 0.1 + i * 0.01 for i in range(HOURS)] for j in range(len(pc))]} for n, b in ps.items()}
    series['weatherCodePt01h'] = {'t': times, 'pc': None, 'v': [[7] * HOURS]}
    return {'fetched_at': fetched_at, 'series': series}


def digest_of(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def put(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj))


class Base(unittest.TestCase):
    def setUp(self):
        self.d, self.clock, self.logs, self.stores = tempfile.mkdtemp(), Clock(), [], []

    def tearDown(self):
        for s in self.stores:
            s.close()
        shutil.rmtree(self.d, ignore_errors=True)

    def store(self):
        s = metodb.MetStore(self.d, clock=self.clock, log=self.logs.append).open()
        self.stores.append(s)
        return s

    def p(self, *a):
        return os.path.join(self.d, *a)

    def legacy(self):
        self.old = cache(START - 600)
        put(self.p('metoffice_cache.json'), self.old)


class Import(Base):
    def test_real_shaped_file_moves_to_trash_after_verify(self):
        self.legacy()
        s = self.store()
        self.assertIsNone(s.degraded)
        self.assertEqual(s.load(), json.loads(json.dumps(self.old)))
        self.assertEqual(s.health()['rows']['forecast_fetch'], 1)
        self.assertEqual(s.health()['rows']['forecast_series'], 8)
        self.assertFalse(os.path.exists(self.p('metoffice_cache.json')))
        self.assertEqual(os.listdir(self.p('trash')), ['metoffice_cache.json'])
        self.assertEqual(s.imported, 1)
        self.assertEqual(len(os.listdir(self.p('backup'))), 1)
        self.assertTrue(s.last_check['ok'])

    def test_imported_cache_builds_the_same_forecast(self):
        self.legacy()
        want = metoffice.build(json.loads(json.dumps(self.old)))
        self.assertEqual(metoffice.build(self.store().load()), want)

    def test_restart_is_idempotent(self):
        self.legacy()
        self.store().close()
        s = self.store()
        self.assertEqual(s.imported, 0)
        self.assertEqual(os.listdir(self.p('trash')), ['metoffice_cache.json'])
        self.assertEqual(s.health()['rows']['forecast_fetch'], 1)

    def test_file_that_reappears_is_trashed_not_reimported(self):
        self.legacy()
        self.store().close()
        put(self.p('metoffice_cache.json'), cache(START - 99999))
        s = self.store()
        self.assertEqual(s.load()['fetched_at'], START - 600)
        self.assertFalse(os.path.exists(self.p('metoffice_cache.json')))

    def test_bad_file_stays_and_is_reported(self):
        put(self.p('metoffice_cache.json'), '{not json')
        s = self.store()
        self.assertTrue(os.path.exists(self.p('metoffice_cache.json')))
        self.assertEqual(len(s.health()['legacy_import_errors']), 1)
        self.assertIsNone(s.degraded)
        self.assertIsNone(s.load())

    def test_wrong_shape_stays(self):
        put(self.p('metoffice_cache.json'), {'fetched_at': 'x', 'series': 3})
        s = self.store()
        self.assertTrue(os.path.exists(self.p('metoffice_cache.json')))
        self.assertEqual(len(s.import_errors), 1)

    def test_failed_verify_leaves_file_and_rows(self):
        self.legacy()
        orig = metodb._put_cache
        metodb._put_cache = lambda con, obj, replace=False: None      # the copy "silently fails": verify must catch it
        try:
            s = self.store()
        finally:
            metodb._put_cache = orig
        self.assertTrue(os.path.exists(self.p('metoffice_cache.json')))
        self.assertIn('verify failed', ' '.join(s.import_errors))
        self.assertEqual(s.load()['fetched_at'], START - 600)         # no row yet, so it reads the old file: nothing lost

    def test_degraded_file_imports_after_restore(self):
        s = self.store()
        s.save(cache(START - 100))
        s.maintain_now(4)
        s.close()
        snap = self.p('backup', os.listdir(self.p('backup'))[0])
        with open(self.p('ih.db'), 'wb') as f:
            f.write(b'not a database at all' * 50)
        for ext in ('-wal', '-shm'):
            if os.path.exists(self.p('ih.db' + ext)):
                os.unlink(self.p('ih.db' + ext))
        s = self.store()
        self.assertIsNotNone(s.degraded)
        s.save(cache(START - 50))
        s.close()
        shutil.copy(snap, self.p('ih.db'))                            # the restore recipe from DOCS.md
        s = self.store()
        self.assertIsNone(s.degraded)
        self.assertEqual(s.load()['fetched_at'], START - 50)
        self.assertEqual(s.health()['rows']['forecast_fetch'], 2)
        self.assertFalse(os.path.exists(self.p('degraded', 'metoffice_cache.json')))


class History(Base):
    def test_history_pruned_keeps_newest(self):
        s = self.store()
        for i in range(5):
            self.clock.t = START + i * DAY
            s.save(cache(self.clock.t))
        rows = s.health()['rows']
        kept = metodb.HISTORY_KEEP_DAYS + 1                                            # day 4 and the three days before it (not older than the limit)
        self.assertEqual(rows['forecast_fetch'], kept)
        self.assertEqual(rows['forecast_series'], SERIES_PER_FETCH * kept)       # cascade delete leaves no orphans
        self.assertEqual(s.load()['fetched_at'], START + 4 * DAY)

    def test_newest_kept_even_when_old(self):
        s = self.store()
        s.save(cache(START - 30 * DAY))
        self.assertEqual(s.load()['fetched_at'], START - 30 * DAY)

    def test_time_index_exists(self):
        s = self.store()
        s.save(cache(START))
        con = s.db.con
        plan = ' '.join(r[-1] for r in con.execute('EXPLAIN QUERY PLAN SELECT fetched_at FROM forecast_fetch ORDER BY fetched_at DESC LIMIT 1'))
        self.assertNotIn('SCAN forecast_fetch', plan.replace('SCAN forecast_fetch USING', ''))


class Failure(Base):
    def test_corrupt_db_runs_degraded_and_is_never_recreated(self):
        self.legacy()
        with open(self.p('ih.db'), 'wb') as f:
            f.write(b'garbage' * 200)
        s = self.store()
        h = s.health()
        self.assertTrue(h['degraded'])
        self.assertIn('WARNING', h)
        self.assertIn('CorruptError', h['error'])
        self.assertTrue(any('DEGRADED' in m for m in self.logs))
        self.assertEqual(s.load()['fetched_at'], START - 600)         # still reads the old file, which was not touched
        self.assertTrue(os.path.exists(self.p('metoffice_cache.json')))
        s.save(cache(START))
        self.assertEqual(s.load()['fetched_at'], START)
        self.assertFalse(os.path.exists(self.p('ih.db')))
        self.assertEqual(len(ihdb.quarantined(self.p('ih.db'))), 1)
        s2 = self.store()
        self.assertTrue(s2.degraded)
        self.assertFalse(os.path.exists(self.p('ih.db')))
        self.assertEqual(s2.load()['fetched_at'], START)

    def test_too_new_schema_runs_degraded_and_file_untouched(self):
        d = ihdb.open(self.p('ih.db'), metodb.MIGRATIONS + [(2, 'CREATE TABLE future(x)')])
        d.close()
        digest = digest_of(self.p('ih.db'))
        s = self.store()
        self.assertIn('SchemaTooNewError', s.health()['error'])
        s.save(cache(START))
        s.close()
        for ext in ('-wal', '-shm'):
            if os.path.exists(self.p('ih.db' + ext)):
                os.unlink(self.p('ih.db' + ext))
        self.assertEqual(digest_of(self.p('ih.db')), digest)

    def test_runtime_write_failure_falls_back_to_file(self):
        s = self.store()
        s.db.tx = lambda *a, **k: (_ for _ in ()).throw(ihdb.DBError('disk full'))
        self.assertTrue(s.save(cache(START)))
        self.assertTrue(os.path.exists(self.p('degraded', 'metoffice_cache.json')))
        self.assertIn('disk full', s.health()['error'])
        self.assertEqual(s.load()['fetched_at'], START)


class JobRun(Base):
    """The job must publish its sensors even when the database is unusable."""
    def ctx(self, fail_ha=False):
        self.sets = {}
        key = 'SECRET-KEY-1'
        ctx = Ctx(dry=False, log=self.logs.append, token='t', data_dir=self.d,
                  opts={'met_office_key': key, 'timezone': 'Europe/London', 'zone_entity': 'zone.home', 'fallback_weather_entity': 'weather.x'})

        def ha(path, data=None):
            if path.startswith('states/zone'):
                return {'attributes': {'latitude': 51.0, 'longitude': -2.0}}
            if data is not None and path.startswith('states/'):
                self.sets[path[7:]] = data
                return {}
            return {'service_response': {'weather.x': {'forecast': [{'datetime': 'x', 'condition': 'rainy'}] * 3}}}
        ctx.ha = ha
        return ctx

    def fresh_cache(self):
        c = cache(self.clock())
        base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        for s in c['series'].values():
            s['t'] = [(base.replace(tzinfo=None) + __import__('datetime').timedelta(hours=i + 1)).strftime('%Y-%m-%dT%H:%MZ') for i in range(HOURS)]
        return c

    def tearDown(self):
        metodb.forget(self.d)
        super().tearDown()

    def test_fetch_stores_and_second_run_uses_cache_without_api(self):
        self.clock.t = __import__('time').time()
        ctx = self.ctx()
        calls = []
        raw = self.fresh_cache()
        def fake_fetch(c):
            calls.append(1)
            metodb.get(c.data_dir, log=c.log).save(raw)
            return raw
        orig = metoffice.fetch
        metoffice.fetch = fake_fetch
        try:
            self.assertTrue(metoffice.run(ctx))
            self.assertTrue(metoffice.run(ctx))
        finally:
            metoffice.fetch = orig
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.sets['sensor.met_office_forecast']['attributes']['source'], 'met_office')
        self.assertEqual(self.sets['sensor.weather_forecast_best']['state'], 'met_office')
        self.assertTrue(os.path.exists(self.p('ih.db')))

    def test_degraded_db_still_sets_sensors(self):
        with open(self.p('ih.db'), 'wb') as f:
            f.write(b'garbage' * 200)
        raw = self.fresh_cache()
        put(self.p('metoffice_cache.json'), raw)
        ctx = self.ctx()
        self.assertTrue(metoffice.run(ctx))
        self.assertEqual(self.sets['sensor.weather_forecast_best']['state'], 'met_office')
        self.assertTrue(metodb.get(self.d).health()['degraded'])

    def test_api_fetch_writes_to_database_through_real_fetch(self):
        import io
        ctx = self.ctx()
        times = ['2026-11-12T%02d:00Z' % (6 + i) for i in range(2)]
        raw = {'coverages': [{'ranges': {'airTemperature1p5m': {'values': [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 'axisNames': ['percentiles', 't']}},
                              'domain': {'axes': {'t': {'values': times}, 'percentiles': {'values': ['5', '50', '95']}}}},
                             {'ranges': {'weatherCodePt01h': {'values': [7, 8]}}, 'domain': {'axes': {'t': {'values': times}}}}]}

        class R(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        ctx.open = lambda req, timeout=30: R(json.dumps(raw).encode())
        out = metoffice.fetch(ctx)
        self.assertEqual(metodb.get(self.d).load(), json.loads(json.dumps(out)))
        self.assertEqual(out['series']['airTemperature1p5m']['v'], [[1.0, 4.0], [2.0, 5.0], [3.0, 6.0]])


class Nightly(Base):
    def test_snapshot_once_a_night_keep_three_and_restore_check(self):
        self.legacy()
        s = self.store()
        s.maintain_now(3)
        n0 = len(os.listdir(self.p('backup')))
        for i in range(5):
            self.clock.t += DAY
            s.maintain_now(4)
            s.maintain_now(5)
        names = sorted(os.listdir(self.p('backup')))
        self.assertEqual(n0, 1)
        self.assertEqual(len(names), ihdb.SNAPSHOT_KEEP)
        h = s.health()
        self.assertEqual(h['snapshots'], 3)
        self.assertTrue(h['last_restore_check']['ok'])
        self.assertTrue(h['last_restore_check']['rows_match_live'])
        self.assertTrue(any('restore check OK' in m and 'forecast_fetch' in m for m in self.logs))
        self.assertEqual(ihdb.restore_check(self.p('backup', names[-1]))['tables']['forecast_fetch'], 1)

    def test_trash_purged_after_30_days_with_fake_clock(self):
        self.legacy()
        s = self.store()
        self.assertEqual(len(os.listdir(self.p('trash'))), 1)
        self.clock.t += 29 * DAY
        s.maintain_now(5)
        self.assertEqual(len(os.listdir(self.p('trash'))), 1)
        self.clock.t += 2 * DAY
        s.maintain_now(5)
        self.assertEqual(os.listdir(self.p('trash')), [])
        self.assertEqual(s.health()['trash'], {'files': 0, 'bytes': 0})

    def test_status_has_db_health_counts_only(self):
        self.legacy()
        h = self.store().health()
        for k in ('schema_version', 'rows', 'last_snapshot', 'trash', 'degraded', 'last_restore_check', 'snapshots'):
            self.assertIn(k, h)
        self.assertEqual(h['schema_version'], metodb.EXPECTED_SCHEMA)
        self.assertEqual(h['trash']['files'], 1)
        self.assertNotIn('airTemperature', json.dumps(h))

    def test_status_wrapper_adds_db(self):
        import main, jobs_child
        orig, old_data = jobs_child.Child.status, main.DATA
        main.DATA = self.d
        try:
            main._with_db_status()
            s = jobs_child.Child({'job': 'metoffice', 'source': 'x', 'version': '1', 'port': 1, 'slug': 'x', 'title': 't', 'runner': lambda c: True},
                                 {'mode': 'dry'}, data=self.d).status()
            self.assertIn('db', s)
            self.assertFalse(s['db_degraded'])
        finally:
            jobs_child.Child.status = orig
            main.DATA = old_data
            metodb.forget(self.d)


if __name__ == '__main__':
    unittest.main()
