"""Item library, slice 1. norm_key is a copy of ih_cooking freshdates.norm_key (keep in sync)."""
import json, re

UNITS = r'(?:kg|g|ml|l|lb|oz|tbsp|tsp|pack|packs|packet|packets|tin|tins|can|cans|jar|jars|bag|bags|bunch|bunches|pot|pots|punnet|punnets|large|small|medium|big|mixed|fresh|ripe|x)'
LEAD = re.compile(r'^(?:[0-9.,/½¼¾]+\s*(?:' + UNITS + r'\b)?\s*)+', re.I)

# PROPOSED shelf days (days to use after buying). to be corrected by the owner. First match wins.
SHELF_RULES = [
    (r'pastrami|\bham\b|salami|chorizo|cooked chicken|sliced', 5),
    (r'bacon', 5),
    (r'chicken|turkey', 2),
    (r'mince|sausage|beef|pork|lamb', 2),
    (r'fish|salmon|cod|prawn|haddock|tuna steak', 1),
    (r'milk|cream|butter', 5),
    (r'yog|skyr', 7),
    (r'salad|spinach|rocket|lettuce', 3),
    (r'berr|raspberr|grape|cherr', 3),
    (r'houmous|hummus|dip', 4),
    (r'banana', 4),
]
FROZEN = re.compile(r'frozen|ice cream|ice lolly', re.I)


def norm_key(s):
    t = str(s or '').lower().strip()
    t = re.sub(r'^(?:meals|staples):\s*', '', t)
    t = re.sub(r'\([^)]*\)', ' ', t)
    t = re.sub(r'\s+[x×]\s?\d+\s*$', '', t)
    t = re.sub(r'^\d+\s?[x×]\s+', '', t)
    t = LEAD.sub('', t)
    t = re.sub(r'\s+[x×]\s?\d+\s*$', '', t)
    return re.sub(r'\s+', ' ', t).strip(' ,;-')[:60]


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')[:40]


def guess_aisle(name, aisles):
    for aisle, rx in aisles.get('match', []):
        if re.search(rx, name, re.I):
            return aisle
    return 'Other'


def shelf_days(name):
    if FROZEN.search(name):
        return None
    for rx, d in SHELF_RULES:
        if re.search(rx, name, re.I):
            return d
    return None


def build(staples, aisles):
    items, seen = [], {}
    for group, g in staples.get('groups', {}).items():
        for raw in g.get('items', []):
            key = norm_key(raw)
            if not key:
                continue
            if key in seen:
                seen[key]['aliases'].append(raw) if raw not in seen[key]['aliases'] else None
                continue
            d = shelf_days(key)
            it = {'id': slug(key), 'name': raw, 'key': key, 'aliases': [raw],
                  'aisle': guess_aisle(key, aisles), 'shelf_days': d, 'fresh': d is not None,
                  'staple': True, 'staple_group': group, 'staple_default_on': group != 'sometimes',
                  'shelf_days_status': 'proposed' if d else None}
            seen[key] = it
            items.append(it)
    return items


def fresh_days_fact(items, limit=4000):
    out = {i['key']: i['shelf_days'] for i in items if i.get('fresh') and i.get('shelf_days')}
    while out and len(json.dumps(out, separators=(',', ':'), ensure_ascii=False).encode()) > limit:
        out.pop(max(out, key=len))
    return out
