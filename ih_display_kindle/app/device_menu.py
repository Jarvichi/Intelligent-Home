"""Local-navigation menu: labels and touch geometry share the same rows."""
from render import clean

ROWS=(('COOK DINNER','dinner'),('WHO IS HOME','home'),('ALL LIGHTS OFF','lights'),('REFRESH','refresh'),('WI-FI','wifi'),('BIRTHDAYS','birthdays'),('BACK','main'))

def svg():
    parts=['<svg xmlns="http://www.w3.org/2000/svg" width="1072" height="1448" viewBox="0 0 1072 1448"><rect width="1072" height="1448" fill="white"/><text x="48" y="84" font-family="Roboto, sans-serif" font-size="48" font-weight="bold">Menu</text>']
    for i,(label,_) in enumerate(ROWS):
        y=300+i*140
        parts.append(f'<rect x="140" y="{y}" width="792" height="116" rx="30" fill="black"/><text x="536" y="{y+73}" fill="white" font-family="Roboto, sans-serif" font-size="30" font-weight="bold" text-anchor="middle">{clean(label,50)}</text>')
    return (''.join(parts)+'</svg>').encode('utf-8')
