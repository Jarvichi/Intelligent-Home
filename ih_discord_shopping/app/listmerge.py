"""Merge duplicate shopping items for the shop checklist. Pure logic, stdlib only.
The Home Assistant list is NEVER changed here: no item is renamed or removed. Only the checklist shows one line for several list items,
and a tick on that line ticks every list item behind it (the same idea as the battery lines).
Why display-only: a renamed list item is added again by the next meal-plan run, and a tick is matched by name (a rename mid-shop loses it).

Two items are the same thing when their name is equal after: lower case, no 'Meals: ' / 'Staples: ' tag, no (note), no count.
A count is 'Name x3', 'Name ×3' or '3 x Name'. Rules for one group of same-name items:
  every item has a count  -> one line 'Name x<sum>'
  no item has a count     -> one line 'Name'
  some have, some do not  -> not merged (the total is not known)
An item with a weight, a unit or a leading number ('500 g mince', '2 onions') is never merged: those amounts do not add up safely.
Notes in brackets are kept, each once: 'Name x3' + 'Name x3 (for the salad)' -> 'Name x6 (for the salad)'."""
import re

import factsview as FV

PREFIX_RE = re.compile(r'^(?:meals|staples):\s*', re.I)
NOTE_RE = re.compile(r'\(([^)]*)\)')
COUNT_TAIL_RE = re.compile(r'^(.*\S)\s+[x×]\s?(\d+)$', re.I)
COUNT_HEAD_RE = re.compile(r'^(\d+)\s?[x×]\s+(\S.*)$', re.I)


def split(summary):
    """-> {'key', 'name', 'count' (int or None), 'notes' [str]} or None when the item must be left alone."""
    t = PREFIX_RE.sub('', str(summary or '').strip())
    notes = [n.strip() for n in NOTE_RE.findall(t) if n.strip()]
    t = re.sub(r'\s+', ' ', NOTE_RE.sub(' ', t)).strip()
    name, count = t, None
    m = COUNT_TAIL_RE.match(t)
    if m:
        name, count = m.group(1), int(m.group(2))
    else:
        m = COUNT_HEAD_RE.match(t)
        if m:
            name, count = m.group(2), int(m.group(1))
    name = name.strip(' ,;-')
    key = name.lower()
    if not key or FV.norm_key(name) != key:   # a weight, unit or other number is still in the name
        return None
    return {'key': key, 'name': name, 'count': count, 'notes': notes}


def label_of(parts):
    """One checklist line for the parts (split() results) of a mergeable group. The wording of the first item is kept."""
    out = parts[0]['name']
    if parts[0]['count'] is not None:
        out += ' x%d' % sum(p['count'] for p in parts)
    notes, seen = [], set()
    for p in parts:
        for n in p['notes']:
            if n.lower() not in seen:
                seen.add(n.lower())
                notes.append(n)
    return out + (' (%s)' % ', '.join(notes) if notes else '')


def merge(pairs):
    """pairs = [(label, [list item names])] -> the same shape, same-name items joined into one line.
    The merged line stands where the first of its items stood. Every list item name is kept, once per list item, so no tick can be lost."""
    pairs = [(lbl, list(names)) for lbl, names in pairs]
    parts = [split(lbl) for lbl, _ in pairs]
    groups = {}
    for n, p in enumerate(parts):
        if p:
            groups.setdefault(p['key'], []).append(n)
    out, done = [], set()
    for n, (lbl, names) in enumerate(pairs):
        if n in done:
            continue
        idx = groups.get(parts[n]['key'], [n]) if parts[n] else [n]
        counts = [parts[i]['count'] is not None for i in idx] if len(idx) > 1 else []
        if len(idx) < 2 or (any(counts) and not all(counts)):
            out.append((lbl, names))
            continue
        done.update(idx)
        out.append((label_of([parts[i] for i in idx]), [nm for i in idx for nm in pairs[i][1]]))
    return out


def merged_names(pairs):
    """{label: [list item names]} for the lines that stand for more than one list item."""
    return {lbl: names for lbl, names in pairs if len(names) > 1}
