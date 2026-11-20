"""Read-only 'scores' payload for the AI layer's "AI scores" page: the latest monthly run, one row per AI.

Only fixed fields are copied out (agent label, weighted test score, counts, invented flag, date). Question text, answers and notes are never read out.
Served by main.py as GET /scores, bearer register_key (the same shared secret as the parent link). Empty key = refused."""
import hmac
from datetime import datetime

import trustdb
import trustjob
import today


def build(result):
    """result = newest results dict (or None) -> {'ok': bool, 'month', 'date', 'dry', 'rows': [...]}."""
    if not result:
        return {'ok': True, 'month': None, 'date': None, 'dry': None, 'rows': []}
    try:
        date = datetime.fromisoformat(str(result.get('finished'))).date().isoformat()
    except (TypeError, ValueError):
        date = None
    rows = []
    for agent, qs in trustjob.by_agent(result.get('results') or {}).items():
        s = trustjob.summarise(qs)
        rows.append({'agent': str(agent).replace('ih_ai_layer_', '', 1)[:40], 'name': today.label(agent), 'test': s['test'],
                     'scored': s['scored'], 'unscored': s['unscored'], 'invented': bool(s['invented'])})
    rows.sort(key=lambda r: (-(r['test'] if r['test'] is not None else -1), r['name']))
    return {'ok': True, 'month': str(result.get('month') or '')[:7] or None, 'date': date, 'dry': bool(result.get('dry')), 'rows': rows}


def authorised(key, header):
    """Constant-time check of 'Bearer <key>'. No key set = always refused."""
    if not key or not isinstance(header, str) or not header.startswith('Bearer '):
        return False
    return hmac.compare_digest(header[7:].strip().encode(), key.encode())


def payload(data_dir):
    try:
        return build(trustdb.read_latest(data_dir))
    except Exception:
        return {'ok': False, 'rows': [], 'error': 'results could not be read'}
