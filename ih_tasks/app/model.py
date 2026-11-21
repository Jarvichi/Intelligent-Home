"""ih_tasks rules: stages, who may move a task where, close reasons, question sets, duplicate titles.
Pure logic: no files, no network, no clock. The store and the API call these and store what they return.

One task has ONE stage. "Needs input" is NOT a stage: it is true while the task has an open question set.
Actors:
  owner   the person whose house it is (ingress page, or the owner_token). The only actor who can approve.
  worker  an AI worker (api_token).
  check   an automated check reporting its result (api_token).
  system  the add-on itself (duplicate auto-close, import)."""
import difflib
import re

STAGES = ('todo', 'planning', 'approved', 'implementing', 'verifying', 'done', 'closed')
STAGE_LABELS = {'todo': 'To do', 'planning': 'Planning', 'approved': 'Approved', 'implementing': 'Implementing',
                'verifying': 'Verifying', 'done': 'Done', 'closed': 'Closed'}
OPEN_STAGES = ('todo', 'planning', 'approved', 'implementing', 'verifying')
ACTORS = ('owner', 'worker', 'check', 'system')
CLOSE_REASONS = ('wont_do', 'duplicate', 'already_done', 'dropped')
CLOSE_LABELS = {'wont_do': "Won't do", 'duplicate': 'Duplicate', 'already_done': 'Already done', 'dropped': 'Dropped'}
VERIFY_BY = ('check', 'owner')
QUESTION_TYPES = ('choice', 'multi', 'yesno', 'text')
LEGACY_KINDS = ('todo_uid', 'queue_id', 'ok_number', 'wy_id')
LINK_KINDS = ('pr', 'result', 'note', 'other')

PRIORITIES = ('high', 'normal', 'low')   # workers take high before normal before low, whatever the stage
PRIORITY_LABELS = {'high': 'High', 'normal': 'Normal', 'low': 'Low'}
TITLE_MAX = 200
TEXT_MAX = 20000
REASON_MAX = 2000
MAX_QUESTIONS = 12
MAX_OPTIONS = 12
DUPLICATE_THRESHOLD = 0.72
MAX_CHILDREN = 12   # child tasks of one parent

# (from, to) -> (actors allowed, what the move needs). Anything not listed is refused.
#   plan    the task must have a plan (sent with the move or already stored)
#   reason  free text saying why
#   result  the evidence of the check (what was looked at and what it showed)
#   no_question  refused while a question set is open
TRANSITIONS = {
    ('todo', 'planning'): (('worker',), ('plan',)),
    ('planning', 'approved'): (('owner',), ('no_question',)),
    ('approved', 'implementing'): (('worker',), ()),
    ('implementing', 'verifying'): (('worker',), ()),
    ('verifying', 'done'): (('check', 'owner'), ()),          # see verify rule in check_transition
    ('verifying', 'implementing'): (('worker', 'check', 'owner'), ('reason',)),
    ('approved', 'planning'): (('worker', 'owner'), ('reason',)),
    ('implementing', 'planning'): (('worker', 'owner'), ('reason',)),
    ('verifying', 'planning'): (('worker', 'owner'), ('reason',)),
    ('closed', 'todo'): (('owner',), ('reason',)),
    ('done', 'todo'): (('owner',), ('reason',)),
}


class RuleError(Exception):
    """A request the rules refuse. code: bad (400), forbidden (403), missing (404), conflict (409)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def clean_text(value, limit, name, required=False):
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise RuleError('bad', '%s must be text' % name)
    value = value.strip()
    if required and not value:
        raise RuleError('bad', '%s is needed' % name)
    if len(value) > limit:
        raise RuleError('bad', '%s is longer than %d characters' % (name, limit))
    return value


def check_actor(actor):
    if actor not in ACTORS:
        raise RuleError('bad', 'actor must be one of: ' + ', '.join(ACTORS))
    return actor


def check_transition(task, to, actor, reason='', plan='', result='', has_open_question=False):
    """Refuse (RuleError) or return the cleaned {reason, plan, result} for a move of `task` (dict with stage, plan, verify_by) to stage `to`.
    Closing is not a transition: use check_close."""
    check_actor(actor)
    if to not in STAGES:
        raise RuleError('bad', 'stage must be one of: ' + ', '.join(STAGES))
    if to == 'closed':
        raise RuleError('bad', 'use close (it needs a reason)')
    frm = task['stage']
    if frm == to:
        raise RuleError('conflict', 'the task is already in %s' % STAGE_LABELS[to])
    rule = TRANSITIONS.get((frm, to))
    if not rule:
        raise RuleError('conflict', 'a task cannot go from %s to %s' % (STAGE_LABELS[frm], STAGE_LABELS[to]))
    actors, needs = rule
    if actor not in actors:
        raise RuleError('forbidden', 'only %s can move a task from %s to %s' % (' or '.join(actors), STAGE_LABELS[frm], STAGE_LABELS[to]))
    reason = clean_text(reason, REASON_MAX, 'reason', required='reason' in needs)
    plan = clean_text(plan, TEXT_MAX, 'plan')
    result = clean_text(result, TEXT_MAX, 'result')
    if 'plan' in needs and not (plan or (task.get('plan') or '').strip()):
        raise RuleError('bad', 'a plan is needed to move to Planning')
    if 'no_question' in needs and has_open_question:
        raise RuleError('conflict', 'answer the open questions first: approving is the last step')
    if (frm, to) == ('verifying', 'done'):
        by = task.get('verify_by') or 'check'
        if actor == 'check':
            if by == 'owner':
                raise RuleError('forbidden', 'this task needs the owner to look at it before it is done')
            if not result:
                raise RuleError('bad', 'a check must give its result')
    return {'reason': reason, 'plan': plan, 'result': result}


def check_close(task, actor, reason_kind, detail='', duplicate_of=None):
    """Refuse or return {reason_kind, detail, duplicate_of}. Any open task can be closed by the owner or a worker (or the system), always with a reason."""
    check_actor(actor)
    if actor == 'check':
        raise RuleError('forbidden', 'a check cannot close a task')
    if task['stage'] == 'closed':
        raise RuleError('conflict', 'the task is already closed')
    if task['stage'] == 'done':
        raise RuleError('conflict', 'the task is done; a finished task is not closed')
    if reason_kind not in CLOSE_REASONS:
        raise RuleError('bad', 'closing needs a reason: ' + ', '.join(CLOSE_REASONS))
    detail = clean_text(detail, REASON_MAX, 'detail')
    if reason_kind == 'duplicate':
        if isinstance(duplicate_of, bool) or not isinstance(duplicate_of, int) or duplicate_of < 1:
            raise RuleError('bad', 'duplicate needs duplicate_of (the number of the task it repeats)')
        if duplicate_of == task.get('id'):
            raise RuleError('bad', 'a task cannot be a duplicate of itself')
    else:
        duplicate_of = None
        if reason_kind in ('wont_do', 'dropped') and actor == 'worker' and not detail:
            raise RuleError('bad', 'a worker must say why (detail)')
    return {'reason_kind': reason_kind, 'detail': detail, 'duplicate_of': duplicate_of}


def clean_questions(questions):
    """Validate a question set -> [{id, text, type, options}]. choice and multi need 2 or more options; yesno and text take none."""
    if not isinstance(questions, list) or not questions:
        raise RuleError('bad', 'questions must be a list with at least one question')
    if len(questions) > MAX_QUESTIONS:
        raise RuleError('bad', 'at most %d questions' % MAX_QUESTIONS)
    out, seen = [], set()
    for n, q in enumerate(questions, 1):
        if not isinstance(q, dict):
            raise RuleError('bad', 'question %d must be an object' % n)
        extra = set(q) - {'id', 'text', 'type', 'options'}
        if extra:
            raise RuleError('bad', 'question %d has an unknown field' % n)
        qid = q.get('id') or 'q%d' % n
        if not isinstance(qid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,24}', qid) or qid in seen:
            raise RuleError('bad', 'question %d needs a short unique id' % n)
        seen.add(qid)
        text = clean_text(q.get('text'), 1000, 'question %d text' % n, required=True)
        typ = q.get('type', 'text')
        if typ not in QUESTION_TYPES:
            raise RuleError('bad', 'question %d type must be one of: %s' % (n, ', '.join(QUESTION_TYPES)))
        options = q.get('options') or []
        if typ in ('choice', 'multi'):
            if not isinstance(options, list) or not 2 <= len(options) <= MAX_OPTIONS:
                raise RuleError('bad', 'question %d needs 2 to %d options' % (n, MAX_OPTIONS))
            options = [clean_text(o, 300, 'question %d option' % n, required=True) for o in options]
            if len(set(options)) != len(options):
                raise RuleError('bad', 'question %d has the same option twice' % n)
        else:
            if options:
                raise RuleError('bad', 'question %d (%s) takes no options' % (n, typ))
            options = []
        out.append({'id': qid, 'text': text, 'type': typ, 'options': options})
    return out


def clean_answers(questions, answers):
    """Validate the answers to a stored question set -> {id: value}. Every question must be answered.
    choice -> one of the options; multi -> list of options (may be empty); yesno -> True/False; text -> text (not empty)."""
    if not isinstance(answers, dict):
        raise RuleError('bad', 'answers must be an object: question id -> answer')
    ids = [q['id'] for q in questions]
    extra = sorted(set(answers) - set(ids))
    if extra:
        raise RuleError('bad', 'no such question: %s' % str(extra[0])[:24])
    out = {}
    for q in questions:
        if q['id'] not in answers:
            raise RuleError('bad', 'question %s has no answer' % q['id'])
        a = answers[q['id']]
        if q['type'] == 'choice':
            if a not in q['options']:
                raise RuleError('bad', 'question %s: pick one of the options' % q['id'])
        elif q['type'] == 'multi':
            if not isinstance(a, list) or any(x not in q['options'] for x in a) or len(set(a)) != len(a):
                raise RuleError('bad', 'question %s: pick from the options' % q['id'])
        elif q['type'] == 'yesno':
            if not isinstance(a, bool):
                raise RuleError('bad', 'question %s: answer true or false' % q['id'])
        else:
            a = clean_text(a, 4000, 'answer to %s' % q['id'], required=True)
        out[q['id']] = a
    return out


def check_due(due):
    """'' or YYYY-MM-DD (a date-time is cut to its date)."""
    if due in (None, ''):
        return ''
    if not isinstance(due, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}([T ].*)?', due.strip()):
        raise RuleError('bad', 'due must be a date: YYYY-MM-DD')
    return due.strip()[:10]


def check_priority(p):
    """'' or None means normal; otherwise high | normal | low."""
    if p in (None, ''):
        return 'normal'
    if not isinstance(p, str) or p.strip().lower() not in PRIORITIES:
        raise RuleError('bad', 'priority must be one of: ' + ', '.join(PRIORITIES))
    return p.strip().lower()


LABEL_MAX_COUNT = 8
LABEL_MAX_LEN = 24


def check_labels(labels):
    """None or [] -> []. Otherwise a list of short words (letters, digits, dash): lowercased, trimmed, de-duplicated, in the order given.
    A comma-separated text is accepted too (what the page's text box sends)."""
    if labels in (None, '', []):
        return []
    if isinstance(labels, str):
        labels = labels.split(',')
    if not isinstance(labels, list):
        raise RuleError('bad', 'labels must be a list of words')
    out = []
    for x in labels:
        if not isinstance(x, str):
            raise RuleError('bad', 'each label must be text')
        x = x.strip().lower()
        if not x:
            continue
        if len(x) > LABEL_MAX_LEN:
            raise RuleError('bad', 'a label is at most %d characters: %s' % (LABEL_MAX_LEN, x[:LABEL_MAX_LEN] + '...'))
        if not re.fullmatch(r'[a-z0-9-]+', x):
            raise RuleError('bad', 'labels use letters, digits and dashes only: %s' % x[:LABEL_MAX_LEN])
        if x not in out:
            out.append(x)
    if len(out) > LABEL_MAX_COUNT:
        raise RuleError('bad', 'at most %d labels on a task' % LABEL_MAX_COUNT)
    return out


def check_legacy(legacy):
    """[{kind, value}] of old identifiers (to-do uid, queue id, OK number, waiting-on-you id)."""
    if legacy in (None, []):
        return []
    if not isinstance(legacy, list) or len(legacy) > 40:
        raise RuleError('bad', 'legacy must be a short list')
    out = []
    for x in legacy:
        if not isinstance(x, dict) or x.get('kind') not in LEGACY_KINDS or not isinstance(x.get('value'), str) or not 0 < len(x['value'].strip()) <= 80:
            raise RuleError('bad', 'legacy entries are {kind, value}; kind is one of: ' + ', '.join(LEGACY_KINDS))
        pair = (x['kind'], x['value'].strip())
        if pair not in out:
            out.append(pair)
    return [{'kind': k, 'value': v} for k, v in out]


# ---- duplicate titles (same idea as the old host helper: sequence ratio or word overlap, whichever is higher) ----

# Labels that say where a task came from, not what it is. Stripped (repeatedly) before titles are compared, so
# "Handoff: X", "Handoff from <name>: X", "OK: X", "OK #12: X" and "Reopened: X" all compare as "X".
_PREFIX = re.compile(r"^(handoff( from [\w.'-]+( [\w.'-]+)?)?|ok( ?#?\d+)?|reopened|plan|built|waiting|todo|ih|check|review)\s*:\s*")


def _words(s):
    s = (s or '').lower().strip()
    for _ in range(4):
        cut = _PREFIX.sub('', s)
        if cut == s:
            break
        s = cut
    return re.sub(r'[^a-z0-9 ]+', ' ', s).split()


def _similar_words(wa, wb):
    if not wa or not wb:
        return 0.0
    sa, sb = set(wa), set(wb)
    return max(difflib.SequenceMatcher(None, ' '.join(wa), ' '.join(wb)).ratio(), len(sa & sb) / len(sa | sb))


def similar(a, b):
    """0..1 for two titles."""
    return _similar_words(_words(a), _words(b))


def find_duplicates(title, candidates, threshold=DUPLICATE_THRESHOLD, limit=5):
    """candidates: [(id, title)] -> [(id, title, score)] best first; [] means it looks new."""
    hits = []
    for cid, ctitle in candidates:
        s = similar(title, ctitle)
        if s >= threshold:
            hits.append((cid, ctitle, round(s, 2)))
    return sorted(hits, key=lambda h: (-h[2], h[0]))[:limit]


def duplicate_groups(tasks, dismissed=(), threshold=DUPLICATE_THRESHOLD):
    """tasks: [(id, title)] of OPEN tasks; dismissed: pairs (low id, high id) the owner said are not duplicates.
    -> [{'ids': [id, ...], 'score': lowest match in the group}] lowest first id first; only groups of 2 or more.
    A group is built around its first (lowest numbered) task: every other member matches THAT task, so titles cannot drift
    along a chain, and no member is a dismissed pair with a task already in the group."""
    dismissed = set(dismissed)
    items = sorted((tid, _words(title)) for tid, title in tasks)
    used, groups = set(), []
    for i, (seed, sw) in enumerate(items):
        if seed in used or not sw:
            continue
        ids, low = [seed], 1.0
        for other, ow in items[i + 1:]:
            if other in used or not ow:
                continue
            s = _similar_words(sw, ow)
            if s < threshold or any((m, other) in dismissed for m in ids):
                continue
            ids.append(other)
            low = min(low, s)
        if len(ids) > 1:
            used.update(ids)
            groups.append({'ids': ids, 'score': round(low, 2)})
    return groups


# ---- merge ----

MERGE_MAX = 20
_RANK = {s: n for n, s in enumerate(OPEN_STAGES)}
_APPROVED = _RANK['approved']


def default_keeper(tasks):
    """tasks: [{id, stage}] (open) -> the id to keep: the most advanced stage, then the lowest number.
    So an Approved (or later) task is never the one that is closed by default."""
    return max(tasks, key=lambda t: (_RANK.get(t['stage'], -1), -t['id']))['id']


def check_merge(keeper, others, actor):
    """Refuse (RuleError) a merge of `others` (task dicts) into `keeper`. Open stages may differ, but an owner's approval is never lost:
    a task that is Approved or further on cannot be merged into a task in an earlier stage."""
    check_actor(actor)
    if actor == 'check':
        raise RuleError('forbidden', 'a check cannot merge tasks')
    if not others:
        raise RuleError('bad', 'merge needs at least one other task')
    if len(others) > MERGE_MAX:
        raise RuleError('bad', 'at most %d tasks in one merge' % MERGE_MAX)
    ids = [t['id'] for t in others]
    if keeper['id'] in ids or len(set(ids)) != len(ids):
        raise RuleError('bad', 'each task can be named once; the task to keep is not in the merge list')
    for t in [keeper] + list(others):
        if t['stage'] not in OPEN_STAGES:
            raise RuleError('conflict', 'task %d is %s; only open tasks can be merged' % (t['id'], STAGE_LABELS[t['stage']]))
    for t in others:
        if _RANK[t['stage']] >= _APPROVED and _RANK[t['stage']] > _RANK[keeper['stage']]:
            raise RuleError('conflict', 'task %d is %s and task %d is only %s: keep task %d instead, so the approval is not lost'
                            % (t['id'], STAGE_LABELS[t['stage']], keeper['id'], STAGE_LABELS[keeper['stage']], t['id']))


def check_child(parent, actor, has_children_of_its_own=False):
    """Refuse (RuleError) a new child task under `parent` (task dict). Children inherit the parent's approval, so the parent must already be
    Approved or Implementing; the owner or a worker may add one. Only one level: a child cannot have children."""
    check_actor(actor)
    if actor not in ('owner', 'worker'):
        raise RuleError('forbidden', 'only the owner or a worker can add a child task')
    if parent['stage'] not in ('approved', 'implementing'):
        raise RuleError('conflict', 'child tasks inherit the parent\'s approval, so the parent must be Approved or Implementing (it is %s)' % STAGE_LABELS[parent['stage']])
    if parent.get('parent_id'):
        raise RuleError('conflict', 'task %d is itself a child task; child tasks have no children of their own' % parent['id'])
