"""Mapping from the old tag-based task workflow to ih_tasks stages. Pure logic (no files, no network): tested in tests/test_migrate_map.py.

Old world: one to-do list whose item descriptions carry tags, plus a work queue.
  [needs-ok #N] text        a worker asks for an OK (optionally with [questions #N] {"q":[...]} = a form)
  [approved #N]             the owner said yes; the work has not run yet
  [done-approved #N date]   the approved work ran
  [declined #N]             the owner said no
  [needs-you #N] text       only the owner can do or decide this; cleared by [done-you #N]
  [replan] why              a reviewer threw the plan out
  PLAN (unreviewed): ...    a plan from an outside AI that nobody checked
  PLAN (Claude...): ...     a plan the worker wrote or checked
  WAITING / [waiting ...]   parked
  DROPPED ...               dropped (the item was ticked)
  [queue date] text, (comment date) text, [job id], [overnight date]: history lines

Rules (first match wins). The migration NEVER places a task past Approved, and only in Approved when an open [approved #N] is there:
  dropped            -> Closed (dropped)
  approved-open      -> Approved
  needs-ok-form      -> Planning + needs input (the form becomes the question set)
  needs-ok           -> Planning (waits for the owner's Approve)
  needs-you          -> Planning if a checked plan exists, else To do; + needs input (one yes/no question: "Is this done?")
  waiting-on-you     -> same as needs-you (parked items whose text says the owner must act)
  checked-plan       -> Planning (waits for the owner's Approve)
  replan             -> To do (the plan was thrown out; a worker must write a new one)
  unreviewed-plan    -> To do
  waiting-other      -> To do (flag: check by hand)
  declined           -> To do (flag: the owner said no to the last ask; close as won't do?)
  ran-before         -> To do (an approved step ran, the item is still open: what is left needs a new plan)
  plain              -> To do"""
import json
import re

RULES = ('dropped', 'approved-open', 'needs-ok-form', 'needs-ok', 'needs-you', 'waiting-on-you', 'checked-plan', 'replan', 'unreviewed-plan',
         'waiting-other', 'declined', 'ran-before', 'plain')
ASK_TAG = re.compile(r'\[(needs-ok|approved|done-approved|declined) #(\d+)[^\]]*\]([^\n\[]*)')
NEEDS_YOU = re.compile(r'\[needs-you #(\d+)\]([^\n\[]*)')
DONE_YOU = re.compile(r'\[done-you(?: #(\d+))?[^\]]*\]')
QUESTIONS = re.compile(r'\[questions #(\d+)\]\s*(\{[^\n]*\})')
WAIT_UPPER = re.compile(r'\bWAITING\b|\[[Ww]aiting[^\]]*\]')
OWNER_ACTS = r'\b%s\s+(to|gets?|decides?|needs?\s+to|must|will)\b'
CHECKED_PLAN = re.compile(r'PLAN \((?!unreviewed)[^)]*\):?\s*([^\n\[]*)')
UNREVIEWED_PLAN = re.compile(r'PLAN \(unreviewed\)')
OPEN_QUEUE = ('queued', 'claimed', 'running', 'waiting', 'failed')
HOUSEKEEPING_KINDS = ('review_batch', 'plan_todos')
QUESTION_TYPES = ('choice', 'multi', 'yesno', 'text')
TITLE_MAX, TEXT_MAX = 200, 20000


def parse_description(desc, owner_name=''):
    """-> the facts the rules need. owner_name (optional) is the first name used in 'X to ...' lines of parked items."""
    desc = desc or ''
    asks = {}                                   # number -> (state, text): the LAST tag for a number in the text wins
    for m in ASK_TAG.finditer(desc):
        asks[int(m.group(2))] = (m.group(1), m.group(3).strip())
    done_you = {int(m.group(1)) for m in DONE_YOU.finditer(desc) if m.group(1)}
    needs_you = {}
    for m in NEEDS_YOU.finditer(desc):
        if int(m.group(1)) not in done_you:
            needs_you[int(m.group(1))] = m.group(2).strip()
    forms = {}
    for m in QUESTIONS.finditer(desc):
        try:
            q = json.loads(m.group(2)).get('q')
        except (ValueError, AttributeError):
            q = None
        if isinstance(q, list) and q:
            forms[int(m.group(1))] = q
    plans = [m.group(1).strip() for m in CHECKED_PLAN.finditer(desc)]
    waiting = bool(WAIT_UPPER.search(desc))
    numberless_done = any(m.group(1) is None for m in DONE_YOU.finditer(desc))
    on_owner = bool(waiting and owner_name and re.search(OWNER_ACTS % re.escape(owner_name), desc, re.I)) and not numberless_done
    return {'asks': asks, 'needs_you': needs_you, 'forms': forms, 'checked_plans': plans, 'unreviewed_plan': bool(UNREVIEWED_PLAN.search(desc)),
            'replans': desc.count('[replan]'), 'waiting': waiting, 'waiting_on_owner': on_owner, 'dropped': desc.lstrip().startswith('DROPPED'),
            'numbers': sorted(set(asks) | set(needs_you) | done_you | set(forms))}


def clean_form(raw):
    """An old [questions] form -> a valid question list (bad entries become text questions, as the old worker did)."""
    out = []
    for n, q in enumerate(raw[:12], 1):
        if not isinstance(q, dict) or not str(q.get('text') or '').strip():
            continue
        typ = q.get('type') if q.get('type') in QUESTION_TYPES else 'text'
        options = [str(o).strip()[:300] for o in (q.get('options') or []) if str(o).strip()] if typ in ('choice', 'multi') else []
        options = list(dict.fromkeys(options))[:12]
        if typ in ('choice', 'multi') and len(options) < 2:
            typ, options = 'text', []
        out.append({'id': 'q%d' % n, 'text': str(q['text']).strip()[:1000], 'type': typ, 'options': options})
    return out


def map_todo(item, owner_name=''):
    """One open to-do item {uid, summary, description, due} -> {rule, stage, task (ready for POST /import), flags: [text], numbers}."""
    desc = item.get('description') or ''
    f = parse_description(desc, owner_name)
    uid = str(item.get('uid') or '')
    legacy = [{'kind': 'todo_uid', 'value': uid}] if uid else []
    legacy += [{'kind': 'ok_number', 'value': str(n)} for n in f['numbers']]
    flags, question, close = [], None, None
    plan = (f['checked_plans'][-1] if f['checked_plans'] else '')[:600]
    open_ok = sorted(n for n, (s, _) in f['asks'].items() if s == 'needs-ok')
    approved = sorted(n for n, (s, _) in f['asks'].items() if s == 'approved')
    declined = sorted(n for n, (s, _) in f['asks'].items() if s == 'declined')
    ran = sorted(n for n, (s, _) in f['asks'].items() if s == 'done-approved')
    if f['dropped']:
        rule, stage, close = 'dropped', 'closed', {'reason_kind': 'dropped', 'detail': 'Marked DROPPED in the old list.'}
    elif approved:
        n = approved[-1]
        rule, stage = 'approved-open', 'approved'
        plan = ('Approved ask #%d: %s' % (n, f['asks'][n][1] or '(see the description)'))[:2000]
    elif open_ok:
        n = open_ok[-1]
        ask = f['asks'][n][1] or '(see the description)'
        form = clean_form(f['forms'].get(n, []))
        stage = 'planning'
        plan = ('Ask #%d: %s' % (n, ask))[:2000] if not plan else ('%s\nAsk #%d: %s' % (plan, n, ask))[:2000]
        if form:
            rule = 'needs-ok-form'
            question = {'questions': form, 'after': 'Your answers go back to planning: the worker updates the plan, then you approve or reject it.'}
        else:
            rule = 'needs-ok'
        if len(open_ok) > 1:
            flags.append('more than one open ask (%s): only the newest is carried as the gate' % ', '.join('#%d' % x for x in open_ok))
    elif f['needs_you'] or f['waiting_on_owner']:
        rule = 'needs-you' if f['needs_you'] else 'waiting-on-you'
        stage = 'planning' if plan else 'todo'
        if f['needs_you']:
            n = sorted(f['needs_you'])[-1]
            what = f['needs_you'][n] or '(see the description)'
            if len(f['needs_you']) > 1:
                flags.append('more than one open needs-you (%s): only the newest became the question' % ', '.join('#%d' % x for x in sorted(f['needs_you'])))
        else:
            what = 'This item was parked until you do or decide something (see the description).'
            if uid:
                legacy.append({'kind': 'wy_id', 'value': 'wy' + uid[:8]})
        question = {'questions': [{'id': 'q1', 'text': ('Only you can do or decide this: %s Is it done?' % what)[:1000], 'type': 'yesno', 'options': []}],
                    'after': 'Yes: the worker carries on with this task. No: it stays parked; add a comment if something changed.'}
    elif plan:
        rule, stage = 'checked-plan', 'planning'
        if f['replans']:
            flags.append('has a checked plan and %d thrown-out plan mark(s): check the plan is the newest before approving' % f['replans'])
    elif f['replans']:
        rule, stage = 'replan', 'todo'
    elif f['unreviewed_plan']:
        rule, stage = 'unreviewed-plan', 'todo'
    elif f['waiting']:
        rule, stage = 'waiting-other', 'todo'
        flags.append('parked (WAITING) but it does not say the owner must act: check by hand')
    elif declined:
        rule, stage = 'declined', 'todo'
        flags.append("the owner declined ask %s: close as won't do?" % ', '.join('#%d' % x for x in declined))
    elif ran:
        rule, stage = 'ran-before', 'todo'
    else:
        rule, stage = 'plain', 'todo'
    if stage in ('planning', 'approved') and not plan:
        plan = 'See the description (carried over from the old list).'
    title = (item.get('summary') or '').strip() or '(no title)'
    if len(title) > TITLE_MAX:
        flags.append('title cut to %d characters' % TITLE_MAX)
    if len(desc) > TEXT_MAX:
        flags.append('description cut to %d characters' % TEXT_MAX)
    if not item.get('due'):
        flags.append('no due date')
    task = {'title': title[:TITLE_MAX], 'stage': stage, 'description': desc.strip()[:TEXT_MAX], 'due': str(item.get('due') or '')[:10], 'legacy': legacy,
            'why': 'Carried over from the old to-do list by rule "%s".' % rule}
    if stage in ('planning', 'approved'):
        task['plan'] = plan
    if question:
        task['question'] = question
    if close:
        task['close'] = close
    return {'rule': rule, 'stage': stage, 'task': task, 'flags': flags, 'numbers': f['numbers'], 'needs_input': bool(question)}


def _match(q, mapped):
    """Index of the to-do a queue item belongs to, or None. approved_request refs are ask numbers; other refs are a to-do uid or its first 8+ characters."""
    ref = str(q.get('ref') or '')
    title = str(q.get('title') or '')
    if q.get('kind') == 'approved_request' and ref.isdigit():
        for i, m in enumerate(mapped):
            if int(ref) in m['numbers']:
                return i
    if len(ref) >= 8:
        for i, m in enumerate(mapped):
            uid = next((x['value'] for x in m['task']['legacy'] if x['kind'] == 'todo_uid'), '')
            if uid and uid.startswith(ref):
                return i
    m2 = re.search(r'\bOK #(\d+)', title)
    if m2:
        for i, m in enumerate(mapped):
            if int(m2.group(1)) in m['numbers']:
                return i
    return None


def map_all(todo_items, queue_items, owner_name=''):
    """-> {tasks: [mapped to-do + queue ids], orphans: [mapped queue-only task], skipped: {reason: count}, counts: {...}}."""
    mapped = [map_todo(i, owner_name) for i in todo_items]
    seen = {}                                   # an old ask number can only point at one task: the first item keeps it
    for m in mapped:
        keep = []
        for x in m['task']['legacy']:
            key = (x['kind'], x['value'])
            if x['kind'] != 'todo_uid' and key in seen:
                m['flags'].append('old id %s %s is also on another item: kept there only' % (x['kind'], x['value']))
                continue
            seen[key] = True
            keep.append(x)
        m['task']['legacy'] = keep
    skipped = {'queue: finished (done or cancelled)': 0, 'queue: housekeeping job': 0, 'queue: its to-do is closed': 0, 'queue: unknown status': 0}
    orphans = []
    for q in queue_items:
        status = q.get('status')
        if status in ('done', 'cancelled'):
            skipped['queue: finished (done or cancelled)'] += 1
            continue
        if status not in OPEN_QUEUE:
            skipped['queue: unknown status'] += 1
            continue
        if q.get('kind') in HOUSEKEEPING_KINDS:
            skipped['queue: housekeeping job'] += 1
            continue
        qid = str(q.get('id') or '')
        i = _match(q, mapped)
        if i is not None:
            m = mapped[i]
            if qid and {'kind': 'queue_id', 'value': qid} not in m['task']['legacy']:
                m['task']['legacy'].append({'kind': 'queue_id', 'value': qid})
            if status in ('claimed', 'running'):
                m['flags'].append('queue job %s is running now: wait for it to finish before the switch-over' % qid)
            elif status == 'failed':
                m['flags'].append('queue job %s failed' % qid)
            continue
        if status == 'waiting' and q.get('kind') in ('todo', 'approved_request'):
            skipped['queue: its to-do is closed'] += 1      # the job ended waiting for input, and the item it worked on is no longer open
            continue
        title = (str(q.get('title') or '').strip() or '(no title)')
        orphans.append({'rule': 'queue-only', 'stage': 'todo', 'needs_input': False, 'numbers': [],
                        'flags': ['queue job (%s, %s) with no open to-do item: check by hand' % (q.get('kind'), status)],
                        'task': {'title': title[:TITLE_MAX], 'stage': 'todo', 'description': str(q.get('instruction') or '').strip()[:TEXT_MAX],
                                 'due': '', 'legacy': [{'kind': 'queue_id', 'value': qid}] if qid else [],
                                 'why': 'Carried over from the old work queue (job %s, status %s): it had no open to-do item.' % (qid, status)}})
    by_rule, by_stage = {}, {}
    for m in mapped + orphans:
        by_rule[m['rule']] = by_rule.get(m['rule'], 0) + 1
        by_stage[m['stage']] = by_stage.get(m['stage'], 0) + 1
    counts = {'todo_items': len(todo_items), 'queue_items': len(queue_items), 'tasks': len(mapped) + len(orphans), 'by_rule': by_rule, 'by_stage': by_stage,
              'needs_input': sum(1 for m in mapped + orphans if m['needs_input']), 'flagged': sum(1 for m in mapped + orphans if m['flags'])}
    return {'tasks': mapped, 'orphans': orphans, 'skipped': skipped, 'counts': counts}


STAGE_LABELS = {'todo': 'To do', 'planning': 'Planning', 'approved': 'Approved', 'closed': 'Closed'}


def report(result, live=False, sent=None, when=''):
    """Markdown mapping report for a person to check before the live run."""
    c = result['counts']
    L = ['# ih_tasks migration: mapping report', '',
         '%s%s' % ('LIVE RUN.' if live else 'DRY RUN: nothing was written anywhere.', (' Made %s.' % when) if when else ''), '',
         'Read %d open to-do items and %d work queue jobs. They become **%d tasks**.' % (c['todo_items'], c['queue_items'], c['tasks']), '',
         '## Tasks per stage', '', '| Stage | Tasks |', '|---|---|']
    for s in ('todo', 'planning', 'approved', 'closed'):
        L.append('| %s | %d |' % (STAGE_LABELS[s], c['by_stage'].get(s, 0)))
    L += ['', 'Needs input (open question set): %d. Flagged for a look by hand: %d.' % (c['needs_input'], c['flagged']), '',
          '## Tasks per rule', '', '| Rule | Tasks |', '|---|---|']
    for r in RULES + ('queue-only',):
        if c['by_rule'].get(r):
            L.append('| %s | %d |' % (r, c['by_rule'][r]))
    L += ['', '## Not carried over', '', '| Reason | Jobs |', '|---|---|']
    L += ['| %s | %d |' % (k, v) for k, v in result['skipped'].items()]
    if sent is not None:
        L += ['', '## Live result', '', 'Created %d, already there %d, failed %d.' % (sent.get('created', 0), sent.get('existing', 0), sent.get('failed', 0))]
    L += ['', '## Every task', '', '| Stage | Needs input | Rule | Due | Old ids | Title | Check |', '|---|---|---|---|---|---|---|']
    order = {'approved': 0, 'planning': 1, 'todo': 2, 'closed': 3}
    for m in sorted(result['tasks'] + result['orphans'], key=lambda m: (order.get(m['stage'], 9), m['rule'], m['task']['title'].lower())):
        t = m['task']
        ids = []
        for x in t['legacy']:
            ids.append({'todo_uid': 'uid ', 'queue_id': 'queue ', 'ok_number': '#', 'wy_id': ''}[x['kind']] + (x['value'][:8] if x['kind'] == 'todo_uid' else x['value']))
        cell = lambda s: str(s).replace('|', '/').replace('\n', ' ')
        L.append('| %s | %s | %s | %s | %s | %s | %s |' % (STAGE_LABELS[m['stage']], 'yes' if m['needs_input'] else '', m['rule'], t['due'] or '-',
                                                       cell(', '.join(ids)) or '-', cell(t['title'][:90]), cell('; '.join(m['flags'])) or ''))
    return '\n'.join(L) + '\n'
