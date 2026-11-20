"""Weather icons for the Kindle picture: the Makin-Things static set (MIT, see weather_svg/LICENSE).

The 25 icons we need are kept in weather_svg/. They are coloured; the Kindle shows
greys, so each colour is swapped for a dark grey when the icon is loaded (orange and
pale blue would otherwise come out as faint greys on e-ink). The soft drop-shadow
filter is dropped: it is almost invisible and its id clashes when several icons
share one page.
"""

import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent / 'weather_svg'

# Home Assistant condition -> icon file (without .svg)
FILES = {
    'sunny': 'clear-day', 'clear-night': 'clear-night', 'cloudy': 'cloudy', 'partlycloudy': 'cloudy-1-day',
    'rainy': 'rainy-2-day', 'pouring': 'rainy-3-day', 'snowy': 'snowy-2-day', 'snowy-rainy': 'rain-and-snow-mix',
    'lightning': 'thunderstorms', 'lightning-rainy': 'severe-thunderstorm', 'fog': 'fog', 'hail': 'hail',
    'windy': 'wind', 'windy-variant': 'wind', 'exceptional': 'tornado',
}
# coloured -> ink greys (lower-case hex as they appear in the files)
GREYS = {'#ffa500': '#444', '#ffc04a': '#666', '#57a0ee': '#222', '#91c0f8': '#555', '#c6deff': '#777',
         '#cd9e73': '#555', '#c00': '#000', '#666': '#333'}
_cache = {}


def _load(name):
    if name not in _cache:
        raw = (HERE / (name + '.svg')).read_text(encoding='utf-8')
        body = re.sub(r'<defs>.*?</defs>', '', raw, flags=re.S)
        body = re.sub(r'\sfilter="url\(#blur\)"', '', body)
        body = re.sub(r'<\?xml.*?\?>|<!--.*?-->', '', body, flags=re.S)
        inner = re.search(r'<svg\b[^>]*>(.*)</svg>', body, flags=re.S).group(1)
        inner = re.sub(r'#[0-9a-fA-F]{3,6}\b', lambda m: GREYS.get(m.group(0).lower(), m.group(0)), inner)
        _cache[name] = re.sub(r'\s+', ' ', inner).strip()
    return _cache[name]


def _draw(name):
    # the files are 56 x 48; fit them across the 100 x 100 icon square, centred
    return lambda: f'<svg x="0" y="7" width="100" height="86" viewBox="0 0 56 48">{_load(name)}</svg>'


ICONS = {condition: _draw(name) for condition, name in FILES.items()}


def key(condition):
    return str(condition or '').lower().replace('_', '-').replace(' ', '-')


def known(condition):
    return key(condition) in ICONS


def svg(condition, x, y, size):
    draw = ICONS.get(key(condition))
    if not draw:
        return ''
    return f'<g aria-label="{key(condition)}" transform="translate({x:g} {y:g}) scale({size / 100:g})">{draw()}</g>'
