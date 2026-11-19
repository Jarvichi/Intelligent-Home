"""Monochrome weather symbols used on the original Kindle screen.

The add-on image installs Symbola so librsvg can render these symbols without
requiring a colour emoji font, which e-ink displays cannot show reliably.
"""

WEATHER = {
    'clear-night': '🌙', 'cloudy': '☁', 'fog': '🌫', 'hail': '🌨',
    'lightning': '⚡', 'lightning-rainy': '⛈', 'partlycloudy': '⛅',
    'pouring': '🌧', 'rainy': '🌦', 'snowy': '❄', 'snowy-rainy': '🌨',
    'sunny': '☀', 'windy': '💨', 'windy-variant': '💨', 'exceptional': '⚠',
}

def symbol(condition):
    return WEATHER.get(str(condition or '').lower().replace('_', '-').replace(' ', '-'), '')

def svg(condition, x, y, size):
    value = symbol(condition)
    if not value:
        return ''
    # U+FE0E explicitly requests the text (monochrome) glyph when available.
    return (f'<text x="{x}" y="{y + size * .82:g}" '
            f'font-family="Symbola, sans-serif" font-size="{size:g}" '
            f'fill="black">{value}&#xFE0E;</text>')
