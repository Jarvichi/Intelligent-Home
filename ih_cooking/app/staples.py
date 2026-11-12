"""Staples (the things bought every week, in groups): read them and edit the text of one line. stdlib only.
Data: /data/staples.json = {"groups": {"<group id>": {"name": "...", "items": ["line", ...]}}}, the file POST /shop reads.
An edit keeps the old file in /data/staples_backups/ and carries the meal-items ledger entry to the new text, so a list that
already holds the line is still known as a meal-plan item. Item text is never logged."""
import json
import os
import re
import time

import recipeadmin

FILE = 'staples.json'
LEDGER = 'meal_items.json'
BACKUP_DIR = 'staples_backups'
BACKUPS_KEEP = 20
TEXT_MAX = 120
BAD_CHARS = re.compile(r'[\x00-\x1f\x7f]')


def _key(s):
    return re.sub(r'^(meals|staples): ', '', str(s).lower().strip())


def read(data):
    """-> {'groups': {id: {'name': str, 'items': [str]}}} ({} groups when the file is missing or broken)."""
    try:
        with open(os.path.join(data, FILE), encoding='utf-8') as fh:
            groups = json.load(fh)['groups']
    except (OSError, ValueError, KeyError, TypeError):
        groups = {}
    out = {}
    for gid, g in (groups.items() if isinstance(groups, dict) else []):
        if isinstance(g, dict) and isinstance(g.get('items'), list):
            out[gid] = {'name': str(g.get('name') or gid), 'items': [str(i) for i in g['items']]}
    return {'groups': out}


def _backup(data, obj):
    folder = os.path.join(data, BACKUP_DIR)
    os.makedirs(folder, exist_ok=True)
    recipeadmin.atomic(os.path.join(folder, 'staples-%s-%s.json' % (time.strftime('%Y%m%d-%H%M%S'), time.time_ns())), obj)
    for old in sorted(f for f in os.listdir(folder) if f.startswith('staples-') and f.endswith('.json'))[:-BACKUPS_KEEP]:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass


@recipeadmin.serialized_write
def rename(data, old, new, group=None):
    """Change the text of one staple line. `old` must match a line exactly, or without regard to case when exactly one text matches.
    `group` (optional) limits the change to that group; without it every group that holds the line is changed.
    -> (True, {'changed': [group ids], 'old': text, 'new': text}) or (False, [reasons]). Nothing is written on failure."""
    path = os.path.join(data, FILE)
    try:
        with open(path, encoding='utf-8') as fh:
            doc = json.load(fh)
        groups = doc['groups']
    except (OSError, ValueError, KeyError, TypeError):
        return False, ['no staples file']
    if not isinstance(groups, dict):
        return False, ['no staples file']
    if BAD_CHARS.search(str(new or '')):
        return False, ['new text has a line break or control character']
    old, new = str(old or '').strip(), re.sub(r'\s+', ' ', str(new or '')).strip()
    if not old or not new:
        return False, ['old and new text are required']
    if len(new) > TEXT_MAX:
        return False, ['new text is longer than %d characters' % TEXT_MAX]
    if group is not None and group not in groups:
        return False, ['no such group']
    scope = [g for g in groups if group in (None, g) and isinstance(groups[g], dict) and isinstance(groups[g].get('items'), list)]
    texts = {i for g in scope for i in groups[g]['items'] if isinstance(i, str)}
    if old not in texts:
        loose = [t for t in texts if t.lower() == old.lower()]
        if len(loose) != 1:
            return False, ['no such staple' if not loose else 'more than one staple matches']
        old = loose[0]
    if new == old:
        return False, ['new text is the same']
    hit = [g for g in scope if old in groups[g]['items']]
    for g in hit:
        if any(isinstance(i, str) and i != old and i.lower() == new.lower() for i in groups[g]['items']):
            return False, ['that group already has this text']
    _backup(data, doc)
    for g in hit:
        groups[g]['items'] = [new if i == old else i for i in groups[g]['items']]
    recipeadmin.atomic(path, doc)
    try:   # the ledger: the new text counts as a meal-plan item from the same moment as the old one
        lf = os.path.join(data, LEDGER)
        with open(lf, encoding='utf-8') as fh:
            led = json.load(fh)
        if isinstance(led, dict) and _key(old) in led and _key(new) not in led:
            led[_key(new)] = led[_key(old)]
            recipeadmin.atomic(lf, led)
    except (OSError, ValueError):
        pass
    return True, {'changed': hit, 'old': old, 'new': new}
