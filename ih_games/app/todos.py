"""One IH Tasks research task per new game, plus timed re-checks for recent releases (owner request, 23 Nov 2026).
Priority: HIGH when the game was played in the last RECENT_DAYS, else LOW (backlog). A game is marked handled only after
IH Tasks confirms its task exists. Games first seen in the last NEW_DAYS that are still being played get a follow-up
're-check' task every RECHECK_DAYS, up to MAX_RECHECKS times, because launch-day guides and data are sparse."""
import datetime
import json
import urllib.parse
import urllib.request

RECENT_DAYS = 30
NEW_DAYS = 90
RECHECK_DAYS = 21
MAX_RECHECKS = 4
DUE_HIGH, DUE_LOW = 7, 60
MAX_PER_RUN = 40
HOW = ('Research useful progress trackers for this game, including quest/mission checklists where appropriate. '
       'Check available APIs and reusable data sources, assess spoilers and propose ONE approach to the player via their '
       'assistant. Preserve player progress and do not claim automatic tracking without verifying it. How to add a tool once '
       'approved: brain note projects/ih-games-hub-design, section "Adding a game tool".')


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


def _date(text):
    try:
        return datetime.date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def is_recent(entry, today):
    seen = _date(entry.get('last_seen'))
    return bool(seen and (today - seen).days <= RECENT_DAYS)


def is_new(entry, today):
    first = _date(entry.get('first_seen'))
    return bool(first and (today - first).days <= NEW_DAYS)


def _facts(entry, player):
    bits = ['Player: %s' % player, 'Platform: %s' % (entry.get('platform') or 'unknown')]
    if entry.get('playtime_min'):
        bits.append('Play time: %.1f h' % (entry['playtime_min'] / 60.0))
    bits += ['First seen: %s' % (entry.get('first_seen') or '?')[:10], 'Last seen: %s' % (entry.get('last_seen') or '?')[:10]]
    return ' | '.join(bits)


def _create(ctx, title, desc, priority, due, key):
    """Return the id of the OPEN task carrying this game's key, creating one when none exists. A closed task that carries the
    key (a dropped or wrongly closed one) does not count: the next one gets the key with a #2, #3 ... suffix."""
    for attempt in range(1, 6):
        k = 'ihgames:' + key + ('' if attempt == 1 else '#%d' % attempt)   # IH Tasks accepts only its own legacy kinds; todo_uid with a namespaced value is the free one
        try:
            found = _call(ctx, '/legacy?' + urllib.parse.urlencode({'kind': 'todo_uid', 'value': k}))   # the task itself; 404 = none
        except RuntimeError:
            raise
        except Exception:
            found = None   # no task carries this key (404) or the lookup failed: create one
        if found and found.get('id'):
            if found.get('stage') != 'closed':
                return int(found['id'])
            continue
        result = _call(ctx, '/tasks', {'title': title[:200], 'description': desc, 'priority': priority, 'source': 'ih_games',
                                       'labels': ['games', 'research'], 'due': due.isoformat(),
                                       'legacy': [{'kind': 'todo_uid', 'value': k}],
                                       'allow_duplicate': True})   # similar titles are different games; our own key does the dedupe
        task = result.get('task')
        if not isinstance(task, dict) or not task.get('id') or task.get('duplicate_of'):
            raise RuntimeError('IH Tasks did not confirm creation of the research task')
        return int(task['id'])
    raise RuntimeError('too many closed tasks carry this game key')


def sync(lib, ctx, today=None, entity=None):
    """New games -> one task each; then due re-checks. Dry: only logs. Returns (titles queued, [])."""
    today = today or datetime.date.today()
    cand = lib.games_without_todo()
    if ctx.dry:
        if cand:
            ctx.log('[DRY] would create %d research task(s) in IH Tasks' % len(cand))
        return [], []
    made, errors = [], []
    for row in cand[:MAX_PER_RUN]:
        for player, gid in row['ids'].items():
            entry = lib.get(player, gid) or {}
            high = is_recent(entry, today)
            try:
                tid = _create(ctx, 'Games: research a tracker for %s' % row['title'],
                              HOW + '\n\n' + _facts(entry, player), 'high' if high else 'low',
                              today + datetime.timedelta(days=DUE_HIGH if high else DUE_LOW), '%s:%s' % (player, gid))
            except Exception as ex:
                errors.append(ex)
                continue
            nxt = (today + datetime.timedelta(days=RECHECK_DAYS)).isoformat() if is_new(entry, today) and high else None
            lib.set_research(player, gid, tid, nxt)
            made.append(row['title'])
    try:
        recheck(lib, ctx, today)
    except Exception as ex:
        errors.append(ex)
    if errors and not made:
        raise errors[0]
    return made, []


def _recheck_task(ctx, entry, player, today, n, note=''):
    key = '%s:%s:recheck%d' % (player, entry['id'], n)
    desc = ('Re-check %s (%s). Launch-day guides and data are sparse, so look again: new guides, community checklists, '
            'APIs, mod/tool support. Original research: task #%s. Re-check %d of %d.%s\n\n%s'
            % (entry['title'], player, entry.get('task'), n, MAX_RECHECKS, note, _facts(entry, player)))
    return _create(ctx, 'Games: re-check %s - guides and data now?' % entry['title'], desc, 'high', today + datetime.timedelta(days=DUE_HIGH), key)


def recheck(lib, ctx, today=None):
    """Create the follow-up tasks that are due. Returns the titles."""
    today = today or datetime.date.today()
    out = []
    for player, e in lib.research_entries():
        n = int(e.get('rechecks') or 0)
        due = _date(e.get('next_recheck'))
        if not due or due > today or n >= MAX_RECHECKS or e.get('tools') or not is_recent(e, today) or not is_new(e, today):
            continue
        _recheck_task(ctx, e, player, today, n + 1)
        lib.bump_recheck(player, e['id'], (today + datetime.timedelta(days=RECHECK_DAYS)).isoformat() if n + 1 < MAX_RECHECKS else None)
        out.append(e['title'])
    return out


def recheck_now(lib, ctx, player, title, today=None):
    """Owner request: queue a re-check task immediately for one game. Returns the task id."""
    today = today or datetime.date.today()
    e = lib.find(player, title)
    if not e or not e.get('task'):
        raise RuntimeError('that game has no research task yet')
    n = int(e.get('rechecks') or 0) + 1
    tid = _recheck_task(ctx, e, player, today, n, ' Requested by hand.')
    lib.bump_recheck(player, e['id'], e.get('next_recheck'))
    return tid
