"""Private, revision-checked usual-week preset. It never changes the active plan."""
import hashlib
import json
import os
import recipeadmin


def read(data):
    path = os.path.join(data, 'usual_week.json')
    try:
        with open(path, encoding='utf-8') as f:
            plan = json.load(f)
    except FileNotFoundError:
        plan = None
    if plan is not None and not valid(plan):
        raise ValueError('Invalid usual week')
    revision = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {'plan': plan, 'revision': revision}


def valid(plan):
    return (isinstance(plan, dict) and set(plan) == set(recipeadmin.DAYS)
            and all(isinstance(v, str) and len(v) <= 80 for v in plan.values()))


def save(data, plan, revision):
    if not valid(plan):
        return 422, {'errors': ['Invalid usual week.']}
    with recipeadmin._WRITE_LOCK:
        try:
            current = read(data)
        except (ValueError, OSError):
            return 500, {'errors': ['The saved usual week could not be read.']}
        if revision != current['revision']:
            return 409, {'errors': ['The usual week changed. Reload before saving.']}
        try:
            recipeadmin.atomic(os.path.join(data, 'usual_week.json'), plan)
            return 200, read(data)
        except (ValueError, OSError):
            return 500, {'errors': ['The usual week could not be saved.']}
