"""Meal-plan items from the ih_cooking add-on (option meal_plan_source = cooking).
Replaces the Home Assistant script that ran the host recipes.py: this module reads the week's pickers and staple switches from Home Assistant,
asks ih_cooking POST /shop/merge which lines to add, and adds only lines that are not on the list yet (by name, ticked ones too in quiet mode).
Staple wording lives in ih_cooking (/data/staples.json), nowhere else. Standard library only."""
import json
import urllib.request

DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
SKIP = {'-', 'Freestyle', 'Eating out', 'Takeaway', 'Leftovers', 'unknown', 'unavailable', ''}
GROUPS = ['breakfast', 'breakfast_rob', 'lunch_andrew', 'lunch_rob', 'lunch_claire', 'fruit', 'snacks', 'picky_tea', 'cats', 'sometimes']


def _state(ha, entity):
    s = ha.state(entity)
    return (s or {}).get('state'), ((s or {}).get('attributes') or {})


def week(ha):
    """(recipe slugs picked for the week, staple groups switched on)."""
    slugs = _state(ha, 'sensor.recipe_meta')[1].get('slugs') or {}
    picks = []
    for d in DAYS:
        v = _state(ha, 'input_select.meal_' + d)[0]
        if not v or v in SKIP:
            continue
        v = slugs.get(v, v)
        if v not in picks:
            picks.append(v)
    groups = [g for g in GROUPS if _state(ha, 'input_boolean.staples_' + g)[0] == 'on']
    return picks, groups


def ask_cooking(url, token, body, opener=None, timeout=30):
    req = urllib.request.Request(url.rstrip('/') + '/shop/merge', data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def add_from_cooking(ha, list_entity, url, token, quiet=True, opener=None):
    """Add the week's missing lines to the list. Returns the cooking reply (with 'added'), or None when there is nothing to do.
    Raises when the cooking add-on cannot be reached or answers badly: the caller then falls back to the script."""
    if not url or not token:
        raise RuntimeError('cooking_url / cooking_token not set')
    picks, groups = week(ha)
    if not picks and not groups:
        return None
    status = ['needs_action', 'completed'] if quiet else ['needs_action']
    have = []
    for st in status:
        have += [i.get('summary') or '' for i in ha.get_items(list_entity, st)]
    res = ask_cooking(url, token, {'recipes': picks, 'groups': groups, 'have': have, 'commit': True}, opener)
    if not isinstance(res, dict) or not isinstance(res.get('add'), list):
        raise RuntimeError('cooking add-on gave an unexpected answer')
    for line in res['add']:
        ha.call('todo', 'add_item', {'entity_id': list_entity, 'item': str(line)})
    res['added'] = len(res['add'])
    return res
