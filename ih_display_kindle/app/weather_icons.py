"""Weather icons for the Kindle picture: the Meteocons monochrome static set (Bas Milius, MIT; see weather_svg/LICENSE).

The 16 icons we need are kept in weather_svg/. They are black line art (drawn with
currentColor, which becomes black here), which suits e-ink. Day and night versions
are picked from the same set.
"""

import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent / 'weather_svg'

# Home Assistant condition -> icon file (without .svg)
FILES = {
    'sunny': 'clear-day', 'clear-night': 'clear-night', 'cloudy': 'cloudy', 'partlycloudy': 'partly-cloudy-day',
    'rainy': 'partly-cloudy-day-rain', 'pouring': 'rain', 'snowy': 'snow', 'snowy-rainy': 'sleet',
    'lightning': 'thunderstorms', 'lightning-rainy': 'thunderstorms-rain', 'fog': 'fog', 'hail': 'hail',
    'windy': 'wind', 'windy-variant': 'wind', 'exceptional': 'weather-alert',
}
# the same conditions after dark (the rest look the same day and night)
NIGHT_FILES = {'sunny': 'clear-night', 'partlycloudy': 'partly-cloudy-night', 'rainy': 'partly-cloudy-night-rain'}
_cache = {}


def _load(name):
    if name not in _cache:
        raw = (HERE / (name + '.svg')).read_text(encoding='utf-8')
        inner = re.search(r'<svg\b[^>]*>(.*)</svg>', raw, flags=re.S).group(1)
        _cache[name] = re.sub(r'\s+', ' ', inner.replace('currentColor', '#000')).strip()
    return _cache[name]


def _draw(name):
    # the files are 128 x 128 with a margin; crop to 100 x 100 so the icon fills the 100 x 100 square
    return lambda: f'<svg x="0" y="0" width="100" height="100" viewBox="14 16 100 100">{_load(name)}</svg>'


ICONS = {condition: _draw(name) for condition, name in FILES.items()}
NIGHT = {condition: _draw(name) for condition, name in NIGHT_FILES.items()}


def draw(condition, night=False):
    """The icon's drawing for a condition, or None. `night` picks the after-dark version where there is one."""
    name = key(condition)
    return (NIGHT.get(name) if night else None) or ICONS.get(name)


def after_dark(row=None, dark=None):
    """True when a forecast row (or the sun entity: dark=True/False) says it is night. A row with a UV index of 0 is after dark."""
    if isinstance(dark, bool):
        return dark
    uv = row.get('uv_index') if isinstance(row, dict) else None
    return isinstance(uv, (int, float)) and not isinstance(uv, bool) and uv == 0


def key(condition):
    return str(condition or '').lower().replace('_', '-').replace(' ', '-')


def known(condition):
    return key(condition) in ICONS


def svg(condition, x, y, size, night=False):
    drawing = draw(condition, night)
    if not drawing:
        return ''
    return f'<g aria-label="{key(condition)}" transform="translate({x:g} {y:g}) scale({size / 100:g})">{drawing()}</g>'
