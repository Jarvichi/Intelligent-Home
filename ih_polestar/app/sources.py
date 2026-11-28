"""Data sources for ih_polestar. READ-ONLY: nothing here sends a command to the car.
parse_payload() turns a raw API dict into one reading; it is tolerant because the real pypolestar payload shape is UNCONFIRMED until the car exists
(the samples in tests/ are assumptions, to be replaced by a real capture after delivery)."""
import asyncio, math, time

KM_PER_MILE = 1.609344


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _pick(d, *names):
    for n in names:
        if isinstance(d, dict) and d.get(n) is not None:
            return d[n]
    return None


def charging_state(raw):
    s = str(raw or '').upper()
    if not s:
        return 'unknown'
    if 'DONE' in s or 'FULL' in s:
        return 'full'
    if 'ERROR' in s or 'FAULT' in s:
        return 'error'
    if 'SMART' in s or 'SCHEDULED' in s:
        return 'scheduled'
    if 'IDLE' in s or 'NOT_CHARGING' in s or 'DISCONNECT' in s:
        return 'idle'
    if 'CHARGING' in s:
        return 'charging'
    return 'unknown'


def parse_payload(raw):
    """-> {battery_pct, range_km, charging, charger_connected, odometer_km, eta_full_min}; every missing value is None, never a guess."""
    raw = raw if isinstance(raw, dict) else {}
    bat = raw.get('battery') if isinstance(raw.get('battery'), dict) else raw
    odo = raw.get('odometer') if isinstance(raw.get('odometer'), dict) else raw
    pct = _num(_pick(bat, 'batteryChargeLevelPercentage', 'battery_charge_level_percentage', 'battery_charge_level', 'battery_pct'))
    if pct is not None and not 0 <= pct <= 100:
        pct = None
    rng = _num(_pick(bat, 'estimatedDistanceToEmptyKm', 'estimated_distance_to_empty_km', 'range_km'))
    if rng is None:
        mi = _num(_pick(bat, 'estimatedDistanceToEmptyMiles', 'estimated_distance_to_empty_miles'))
        rng = None if mi is None else mi * KM_PER_MILE
    if rng is not None and rng < 0:
        rng = None
    odo_m = _num(_pick(odo, 'odometerMeters', 'odometer_meters'))
    odo_km = None if odo_m is None else odo_m / 1000.0
    if odo_km is None:
        odo_km = _num(_pick(odo, 'odometer_km'))
    conn = _pick(bat, 'chargerConnectionStatus', 'charger_connection_status')
    eta = _num(_pick(bat, 'estimatedChargingTimeToFullMinutes', 'estimated_charging_time_to_full_minutes'))
    return {'battery_pct': pct, 'range_km': rng, 'charging': charging_state(_pick(bat, 'chargingStatus', 'charging_status')),
            'charger_connected': None if conn is None else 'DISCONNECTED' not in str(conn).upper(),
            'odometer_km': odo_km, 'eta_full_min': eta}


class FixtureSource:
    """Fake values that move over time (battery 20..95 % over 6 h), so the UI and the Cars card can be tested without a car."""
    name = 'fixture'

    def __init__(self, clock=time.time):
        self.clock = clock

    def fetch(self):
        t = self.clock()
        phase = (t % 21600) / 21600.0
        pct = 20 + 75 * (phase * 2 if phase < 0.5 else (1 - phase) * 2)
        charging = 'CHARGING_STATUS_CHARGING' if phase < 0.5 else 'CHARGING_STATUS_IDLE'
        return {'battery': {'batteryChargeLevelPercentage': round(pct, 1), 'chargingStatus': charging,
                            'estimatedDistanceToEmptyKm': round(pct * 4.5), 'chargerConnectionStatus': 'CHARGER_CONNECTION_STATUS_CONNECTED' if phase < 0.5 else 'CHARGER_CONNECTION_STATUS_DISCONNECTED',
                            'estimatedChargingTimeToFullMinutes': int((95 - pct) * 3) if phase < 0.5 else None},
                'odometer': {'odometerMeters': 12345000 + int(t % 86400) * 3}}


class PolestarSource:
    """Real source: the pypolestar library (imported only here, only in live/dry-with-login). Read-only calls only.
    UNCONFIRMED against a real account: method names are probed, and any failure surfaces as a short error type, never a password."""
    name = 'pypolestar'

    def __init__(self, email, password, vin=''):
        self.email, self.password, self.vin = email, password, vin

    async def _fetch(self):
        from pypolestar import PolestarApi  # noqa: imported late on purpose
        api = PolestarApi(username=self.email, password=self.password, vins=[self.vin] if self.vin else None)
        await api.async_init()
        vins = [self.vin] if self.vin else list(getattr(api, 'vins', None) or [])
        if not vins:
            raise RuntimeError('no vehicle on this Polestar ID')
        await api.get_ev_data(vins[0])
        return api.get_latest_data(vins[0]) or {}

    def fetch(self):
        if not (self.email and self.password):
            raise RuntimeError('no Polestar login set')
        return asyncio.run(self._fetch())


def make_source(opts, clock=time.time):
    mode = opts.get('mode', 'dry')
    if mode == 'fixture':
        return FixtureSource(clock)
    if opts.get('polestar_email') and opts.get('polestar_password'):
        return PolestarSource(opts['polestar_email'], opts['polestar_password'], opts.get('vin', ''))
    return None
