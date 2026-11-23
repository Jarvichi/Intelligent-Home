"""Batch newly discovered games into an IH Tasks research item."""
import datetime
import json
import urllib.request

SUMMARY = 'Games: check for new games'
DUE_DAYS = 7
HOW = ('Research the listed games for useful progress trackers, including quest checklists where appropriate. '
       'Check available APIs and reusable data sources, assess spoilers and propose an approach. '
       'Preserve player progress and do not claim automatic quest tracking without verifying it.')


def _call(ctx, path, body=None):
    url = str(ctx.opts.get('tasks_api_url') or '').strip().rstrip('/')
    token = str(ctx.opts.get('tasks_api_token') or '').strip()
    if not url or not token:
        raise RuntimeError('IH Tasks is not configured (tasks_api_url and tasks_api_token required)')
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
                                 method='GET' if body is None else 'POST')
    with ctx.open(req, timeout=10) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise RuntimeError('IH Tasks returned an invalid response')
    return result


def _line(row):
    return '- %s (%s)' % (row['title'], ', '.join(row['players']))


def sync(lib, ctx, today=None, entity=None):
    """Record candidates only after IH Tasks confirms the batch was saved."""
    cand = lib.games_without_todo()
    if not cand:
        return [], []
    if ctx.dry:
        ctx.log('[DRY] would queue new games in IH Tasks')
        return [], []
    rows = _call(ctx, '/tasks?stage=todo&q=Games%3A%20check%20for%20new%20games').get('tasks')
    if not isinstance(rows, list):
        raise RuntimeError('IH Tasks returned an invalid task list')
    item = next((t for t in rows if t.get('title') == SUMMARY and t.get('source') == 'ih_games'), None)
    if item:
        task = _call(ctx, '/tasks/%d' % int(item['id']))
        if task.get('stage') != 'todo':
            raise RuntimeError('IH Tasks research item changed stage; retry later')
        old = task.get('description') or ''
        existing = set(old.splitlines())
        missing = [_line(r) for r in cand if _line(r) not in existing]
        if missing:
            _call(ctx, '/tasks/%d/edit' % int(item['id']), {'description': old.rstrip() + '\n' + '\n'.join(missing)})
    else:
        body = {'title': SUMMARY, 'description': HOW + '\n\nNew games:\n' + '\n'.join(_line(r) for r in cand),
                'priority': 'normal', 'source': 'ih_games', 'labels': ['games', 'research'],
                'due': ((today or datetime.date.today()) + datetime.timedelta(days=DUE_DAYS)).isoformat(),
                'allow_duplicate': True}
        result = _call(ctx, '/tasks', body)
        task = result.get('task')
        if not isinstance(task, dict) or not task.get('id') or task.get('stage') != 'todo':
            raise RuntimeError('IH Tasks did not confirm creation of the research task')
    for row in cand:
        for player, game_id in row['ids'].items():
            lib.mark_todo(player, game_id)
    return [r['title'] for r in cand], []
