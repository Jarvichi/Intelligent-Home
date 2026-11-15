"""One-way mirror: tasks -> one Home Assistant to-do list, so readers of that list (wall display, chat bots) keep working.
The task database is the truth. The list is a plain copy: title, due date, one status line. Nothing is ever read back from the list
into a task, and list items that belong to no task are never touched.

mirror option: off (default) | dry (log what would change, change nothing) | live.
plan() is pure (tested); run() talks to Home Assistant through ihlib.ha."""
import model as M

MARK = 'Task #'


def status_line(task):
    """The whole description of a mirrored item, e.g. 'Task #12 · Planning · needs your input'."""
    parts = ['%s%d' % (MARK, task['id']), M.STAGE_LABELS[task['stage']]]
    if task['stage'] == 'closed' and task.get('closed_reason'):
        parts.append(M.CLOSE_LABELS.get(task['closed_reason'], task['closed_reason']))
    if task.get('needs_input'):
        parts.append('needs your input')
    elif task.get('waiting_on') == 'owner':
        parts.append('waiting for you')
    return ' · '.join(parts)


def wanted(task):
    return {'summary': task['title'], 'due': task.get('due') or '', 'description': status_line(task),
            'status': 'completed' if task['stage'] in ('done', 'closed') else 'needs_action'}


def plan(tasks, items, known):
    """tasks: task summaries; items: the list's items [{uid, summary, status, due?, description?}]; known: {task id: uid}.
    -> [op]. op: {op: 'add', task, ...fields} | {op: 'update', task, uid, changes} | {op: 'adopt', task, uid} (found our own item by its marker).
    A finished task with no item gets none. An item that was deleted by hand is not created again."""
    by_uid = {i.get('uid'): i for i in items if i.get('uid')}
    by_mark = {}
    for i in items:
        d = (i.get('description') or '')
        if d.startswith(MARK):
            num = d[len(MARK):].split(' ', 1)[0]
            if num.isdigit():
                by_mark[int(num)] = i
    ops = []
    for t in tasks:
        w = wanted(t)
        uid = known.get(t['id'])
        item = by_uid.get(uid) if uid else None
        if item is None and not uid and t['id'] in by_mark:
            item = by_mark[t['id']]
            ops.append({'op': 'adopt', 'task': t['id'], 'uid': item['uid']})
            uid = item['uid']
        if item is None:
            if not uid and w['status'] == 'needs_action':
                ops.append(dict(w, op='add', task=t['id']))
            continue
        changes = {}
        if (item.get('summary') or '') != w['summary']:
            changes['summary'] = w['summary']
        if (item.get('description') or '') != w['description']:
            changes['description'] = w['description']
        if (item.get('status') or 'needs_action') != w['status']:
            changes['status'] = w['status']
        if w['due'] and (item.get('due') or '')[:10] != w['due']:
            changes['due'] = w['due']
        if changes:
            ops.append({'op': 'update', 'task': t['id'], 'uid': uid, 'changes': changes})
    return ops


def read_items(ha, entity):
    r = ha.post('services/todo/get_items?return_response', {'entity_id': entity})
    return (((r or {}).get('service_response') or {}).get(entity) or {}).get('items') or []


def run(ha, store, entity, mode, log=print):
    """One pass. -> {mode, add, update, adopt, errors}. Titles are never logged (counts only)."""
    out = {'mode': mode, 'add': 0, 'update': 0, 'adopt': 0, 'errors': 0}
    if mode not in ('dry', 'live') or not entity:
        return out
    known = dict(store.legacy_uids())
    known.update({k: v['uid'] for k, v in store.mirror_rows().items() if v['uid']})
    ops = plan(store.list(), read_items(ha, entity), known)
    added = []
    for op in ops:
        out[op['op']] += 1
        if mode != 'live':
            continue
        try:
            if op['op'] == 'adopt':
                store.mirror_set(op['task'], op['uid'], '')
            elif op['op'] == 'add':
                data = {'entity_id': entity, 'item': op['summary'], 'description': op['description']}
                if op['due']:
                    data['due_date'] = op['due']
                ha.call_service('todo', 'add_item', data)
                added.append(op['task'])
            else:
                c = op['changes']
                data = {'entity_id': entity, 'item': op['uid']}
                for src, dst in (('summary', 'rename'), ('description', 'description'), ('status', 'status'), ('due', 'due_date')):
                    if src in c:
                        data[dst] = c[src]
                ha.call_service('todo', 'update_item', data)
        except Exception as e:
            out['errors'] += 1
            log('mirror: %s failed for task %d (%s)' % (op['op'], op['task'], type(e).__name__))
    if added:
        # the add service returns no id: read the list again and remember our items by their marker
        try:
            for i in read_items(ha, entity):
                d = i.get('description') or ''
                if d.startswith(MARK):
                    num = d[len(MARK):].split(' ', 1)[0]
                    if num.isdigit() and int(num) in added and i.get('uid'):
                        store.mirror_set(int(num), i['uid'], '')
        except Exception as e:
            out['errors'] += 1
            log('mirror: could not read the new items back (%s)' % type(e).__name__)
    if any(out[k] for k in ('add', 'update', 'adopt', 'errors')):
        log('mirror %s: %d to add, %d to update, %d adopted, %d errors' % ('LIVE' if mode == 'live' else '[DRY]', out['add'], out['update'], out['adopt'], out['errors']))
    return out
