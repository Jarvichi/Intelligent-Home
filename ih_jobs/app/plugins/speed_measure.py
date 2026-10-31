"""speed_measure: every 6 hours, measure the download speed with 4 parallel downloads of a 100 MB file from a UK server and publish it as a sensor
(port of the host measure.sh shell script). The Speedtest.net integration under-reads on a gigabit line
(one thread on a Pi); four parallel downloads read about 900 Mbit/s.
Method (same as the shell script): each download must finish inside `max_s` seconds and be at least `min_bytes` long, otherwise the whole run is ignored
(nothing is published, the old value stays). Mbit/s = total bytes * 8 / slowest time / 1e6, rounded down to a whole number.
Every house value comes from settings (JSON in plugins[].settings); defaults in brackets:
  url              file to download (http://ipv4.download.thinkbroadband.com/100MB.zip)
  streams          parallel downloads (4)
  max_s            give up on a download after this many seconds (20)
  min_bytes        a download shorter than this counts as failed (90000000)
  publish_entity   entity to write through the HA state API, live only (sensor.internet_download_measured; empty = do not publish)
  minutes          run every N minutes (360)
The published state is the whole number; attributes carry unit_of_measurement Mbit/s, state_class measurement, friendly_name, icon.
Dry: measures (it only downloads, which the old sensor did too) and logs the result; publishes nothing. No state is kept.
SWAP: remove the old command_line sensor with the same entity id, then set the plugin live."""
import threading
import time
import urllib.request

NAME = 'speed_measure'
SCHEDULE = {'kind': 'every', 'minutes': 360}
DEFAULTS = {'url': 'http://ipv4.download.thinkbroadband.com/100MB.zip', 'streams': 4, 'max_s': 20, 'min_bytes': 90000000,
            'publish_entity': 'sensor.internet_download_measured', 'minutes': 360}


def download(url, max_s, opener=None, clock=time.time):
    """-> (bytes, seconds) of one download; (bytes so far, max_s) when the time runs out (the caller treats a short file as failed)."""
    t0 = clock()
    n = 0
    try:
        with (opener or urllib.request.urlopen)(urllib.request.Request(url, headers={'User-Agent': 'ih-jobs/1.0 (speed measure)'}), timeout=max_s) as r:
            while True:
                if clock() - t0 > max_s:
                    return n, float(max_s)
                chunk = r.read(262144)
                if not chunk:
                    break
                n += len(chunk)
    except Exception:
        return n, max(clock() - t0, 0.001)
    return n, max(clock() - t0, 0.001)


def mbit(results, min_bytes):
    """Whole Mbit/s from [(bytes, seconds), ...], or None when any download was short (same rule as measure.sh)."""
    if not results or any(b <= min_bytes for b, _ in results):
        return None
    t = max(s for _, s in results)
    return int(sum(b for b, _ in results) * 8 / t / 1e6) if t > 0 else None


def measure(s, opener=None, clock=time.time):
    out = []
    lock = threading.Lock()

    def one():
        r = download(s['url'], s['max_s'], opener, clock)
        with lock:
            out.append(r)
    ts = [threading.Thread(target=one, daemon=True) for _ in range(max(1, int(s['streams'])))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(s['max_s'] + 10)
    return mbit(out, s['min_bytes']) if len(out) == len(ts) else None


def run(ctx):
    s = ctx.settings
    v = measure(s, getattr(ctx.http, 'opener', None))
    if v is None:
        ctx.log('measurement failed (a download was short or too slow), nothing published')
        return True
    if ctx.dry:
        ctx.log('[DRY] measured %d Mbit/s; would publish to %s (nothing published)' % (v, s['publish_entity'] or '(no entity set)'))
        return True
    ctx.log('measured %d Mbit/s' % v)
    if s['publish_entity']:
        ctx.ha.post('states/' + s['publish_entity'], {'state': str(v), 'attributes': {
            'unit_of_measurement': 'Mbit/s', 'state_class': 'measurement', 'friendly_name': 'Internet download measured', 'icon': 'mdi:speedometer'}})
    return True
