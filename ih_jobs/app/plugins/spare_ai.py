"""spare_ai: each night, spend the unused part of each FREE AI provider's daily allowance (held by the separate ih_ai_layer add-on) on queued, low-priority,
NON-PRIVATE tasks, plus one built-in task: a free second-opinion review of the previous day's commits to a public GitHub repository.
Each task is a fan-out: up to `fanout` different providers answer the same question, then one more provider compares the answers (agreed / only-one-source / best answer).
Answers are UNVERIFIED AI output. Only non-private text may go in the queue; the host that writes queue files does its own privacy check and sets "guard": "passed".
Settings (JSON in plugins[].settings; defaults in brackets):
  at ['22:30']              daily start (add-on timezone)
  folder ['/share/ih/spare_ai']   holds queue/ (queue/processed, queue/refused, queue/failed), done/ and reports/
  providers_order ['gemini','mistral','groq','openrouter','pollinations']   provider NAMES allowed, in order of use. Any provider not listed is never used.
  reserve_per_provider [5]  calls left unused per provider      max_calls [120]  hard ceiling per night      fanout [3]  providers per task (1..4)
  repo ['']                 'owner/name' of a public GitHub repository to review (empty = no repo review)
  repo_hours [24]           look back this many hours           max_diff_chars [2800]  size of the diff text sent per commit
Add-on options: ai_layer_url (default http://172.30.32.1:8801) and ai_layer_token (the layer's api_token). With no token the plugin skips with a log line.
Queue file: <folder>/queue/<name>.json = {"id": str, "kind": "research|review|draft|summary", "text": str (max 3500 chars), "fanout": 1-4 optional, "guard": "passed"}.
Dry (default): reads the layer's /status and the GitHub commit list, logs what WOULD run; makes no /request calls and writes/moves nothing.
Logs hold metadata only (ids, provider names, sizes, ok, ms): never the token, the request text or any answer."""
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta, timezone

NAME = 'spare_ai'
SCHEDULE = {'kind': 'daily', 'at': '22:30'}
DEFAULTS = {'at': '22:30', 'folder': '/share/ih/spare_ai', 'providers_order': ['gemini', 'mistral', 'groq', 'openrouter', 'pollinations'],
            'reserve_per_provider': 5, 'max_calls': 120, 'fanout': 3, 'repo': '', 'repo_hours': 24, 'max_diff_chars': 2800}

DEFAULT_URL = 'http://172.30.32.1:8801'
KINDS = ('research', 'review', 'draft', 'summary')
MAX_TASK_CHARS = 3500
COMPARE_LIMIT = 3800
SUFFIX = '\n\nKeep the answer under 200 words.'
ID_RE = re.compile(r'^[A-Za-z0-9._-]{1,48}$')
REPO_RE = re.compile(r'^[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}$')
UA = 'intelligent-home-jobs'
REVIEW_PROMPT = ("Review this code change from a public open-source Home Assistant add-on repository. List real bugs and risks first, then minor points. "
                 "Say 'No problems found' if none.\n\n")
COMPARE_HEAD = ("You compare answers from %d different AIs to the same question. Do not add new facts.\nReply in three short parts:\n"
                "1. AGREED: points most answers share.\n"
                "2. DISAGREE / ONLY ONE SOURCE: points, names, numbers or links that appear in only one answer or conflict (say which answer). Treat these as unverified.\n"
                "3. ANSWER: the best answer using only the agreed points.\n\nQUESTION:\n%s\n\n")
KEEP_REVIEWED = 500


def _int(v, d, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return d


def _settings(s):
    order = [p for p in (s.get('providers_order') or []) if isinstance(p, str) and p.strip()]
    return {'folder': str(s.get('folder') or DEFAULTS['folder']).rstrip('/') or DEFAULTS['folder'], 'order': order,
            'reserve': _int(s.get('reserve_per_provider'), 5, 0, 1000), 'max_calls': _int(s.get('max_calls'), 120, 0, 2000),
            'fanout': _int(s.get('fanout'), 3, 1, 4), 'repo': str(s.get('repo') or '').strip(), 'repo_hours': _int(s.get('repo_hours'), 24, 1, 720),
            'max_diff': _int(s.get('max_diff_chars'), 2800, 200, 3400)}


class Layer:
    """Thin client for ih_ai_layer. opener is injectable. Nothing sensitive is ever logged."""
    def __init__(self, url, token, opener=None):
        self.url, self.token, self.opener = url.rstrip('/'), token, opener or urllib.request.urlopen

    def _hdr(self):
        return {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': UA}

    def status(self):
        req = urllib.request.Request(self.url + '/status', headers=self._hdr())
        with self.opener(req, timeout=15) as r:
            return json.loads(r.read(2000000))

    def ask(self, text, model, rid):
        """-> (ok, answer, ms). A '[dry]' (shadow) answer counts as a failure."""
        t0 = time.time()
        body = json.dumps({'text': text, 'model': model, 'request_id': rid, 'caller': 'ih_jobs_spare_ai'}).encode()
        try:
            req = urllib.request.Request(self.url + '/request', data=body, method='POST', headers=self._hdr())
            with self.opener(req, timeout=150) as r:
                d = json.loads(r.read(2000000))
            ans = d.get('answer') if isinstance(d, dict) else None
            ok = bool(isinstance(d, dict) and d.get('ok') and isinstance(ans, str) and ans.strip() and not ans.lstrip().startswith('[dry]'))
            return ok, (ans.strip() if ok else ''), int((time.time() - t0) * 1000)
        except Exception:
            return False, '', int((time.time() - t0) * 1000)


def budget(status, order, reserve, max_calls):
    """-> {provider: {'spare': n, 'model': str}} (ordered), total. Only listed, configured, free, live, not-stopped providers count."""
    by = {}
    for p in (status.get('providers') or []):
        if isinstance(p, dict) and isinstance(p.get('provider'), str):
            by[p['provider']] = p
        elif isinstance(p, dict) and isinstance(p.get('name'), str):
            by.setdefault(p['name'].replace('ih_ai_layer_', '', 1), p)
    out = {}
    for name in order:
        p = by.get(name)
        if not p or not p.get('model') or p.get('configured') is False or p.get('kill_switch') or p.get('shadow') or p.get('free') is not True:
            continue
        spare = max(0, _int(p.get('daily_cap'), 0, 0, 10 ** 6) - _int(p.get('used_today'), 0, 0, 10 ** 6) - reserve)
        out[name] = {'spare': spare, 'model': str(p['model'])}
    total = min(sum(v['spare'] for v in out.values()), max_calls)
    return out, total


class Run:
    def __init__(self, ctx, st, layer, provs, total):
        self.ctx, self.st, self.layer, self.provs, self.left = ctx, st, layer, provs, total
        self.calls = {}
        self.lock = threading.Lock()
        self.seq = 0

    def pick(self, n, exclude=()):
        names = [p for p, v in self.provs.items() if v['spare'] > 0 and p not in exclude]
        return names[:max(0, min(n, self.left))]

    def call(self, prov, text, tag):
        with self.lock:
            self.provs[prov]['spare'] -= 1
            self.left -= 1
            self.calls[prov] = self.calls.get(prov, 0) + 1
            self.seq += 1
            rid = 'spare-%s-%s-%d' % (time.strftime('%d%H%M%S'), re.sub(r'[^A-Za-z0-9]', '', tag)[:20], self.seq)
        ok, ans, ms = self.layer.ask(text, self.provs[prov]['model'], rid)
        self.ctx.log('call id=%s provider=%s chars_in=%d chars_out=%d ok=%s ms=%d' % (rid, prov, len(text), len(ans), ok, ms))
        return ok, ans

    def task(self, tid, kind, text, fanout):
        """-> result dict {id, kind, asked, failed, compared_by, comparison, answers} (answers = [(provider, text)]) or None if no budget."""
        asked = self.pick(fanout)
        if not asked:
            return None
        q = text + SUFFIX
        res = {}

        def work(p):
            res[p] = self.call(p, q, tid)
        threads = [threading.Thread(target=work, args=(p,)) for p in asked]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        answers = [(p, res[p][1]) for p in asked if p in res and res[p][0]]
        failed = [p for p in asked if not (p in res and res[p][0])]
        out = {'id': tid, 'kind': kind, 'asked': asked, 'failed': failed, 'compared_by': '', 'comparison': '', 'answers': answers}
        if len(answers) >= 2:
            cands = self.pick(1, exclude=asked) or self.pick(1)
            if cands:
                ok, ans = self.call(cands[0], compare_text(text, answers), tid + 'c')
                if ok:
                    out['compared_by'], out['comparison'] = cands[0], ans
        return out


def compare_text(q, answers):
    head = COMPARE_HEAD % (len(answers), q[:800])
    per = max(100, (COMPARE_LIMIT - len(head) - 40 * len(answers)) // len(answers))
    return (head + '\n\n'.join('ANSWER %d:\n%s' % (i + 1, a[:per]) for i, (_, a) in enumerate(answers)))[:COMPARE_LIMIT]


def write_done(folder, date, r):
    os.makedirs(os.path.join(folder, 'done'), exist_ok=True)
    path = os.path.join(folder, 'done', '%s-%s.md' % (date, r['id']))
    lines = ['task: %s | kind: %s | asked: %s | failed: %s | compared by: %s' % (r['id'], r['kind'], ', '.join(r['asked']) or '-', ', '.join(r['failed']) or '-', r['compared_by'] or '-'),
             'UNCHECKED: AI output, review before use', '']
    if r['comparison']:
        lines += ['## Comparison (%s)' % r['compared_by'], r['comparison'], '']
    for i, (p, a) in enumerate(r['answers']):
        lines += ['## Answer %d (%s)' % (i + 1, p), a, '']
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return path


def read_queue(folder):
    """-> (good [(path, obj)], refused [(path, why)]) oldest first."""
    qd = os.path.join(folder, 'queue')
    try:
        files = sorted((os.path.join(qd, n) for n in os.listdir(qd) if n.endswith('.json') and os.path.isfile(os.path.join(qd, n))), key=lambda p: (os.path.getmtime(p), p))
    except OSError:
        return [], []
    good, bad = [], []
    for p in files:
        try:
            with open(p, encoding='utf-8') as f:
                o = json.load(f)
        except (OSError, ValueError):
            bad.append((p, 'invalid JSON'))
            continue
        if not isinstance(o, dict):
            bad.append((p, 'not an object'))
        elif o.get('guard') != 'passed':
            bad.append((p, 'guard not passed'))
        elif not isinstance(o.get('text'), str) or not o['text'].strip():
            bad.append((p, 'no text'))
        elif len(o['text']) > MAX_TASK_CHARS:
            bad.append((p, 'text too long'))
        elif not isinstance(o.get('id'), str) or not ID_RE.match(o['id']):
            bad.append((p, 'bad id'))
        elif o.get('kind') not in KINDS:
            bad.append((p, 'bad kind'))
        else:
            good.append((p, o))
    return good, bad


def move(path, sub):
    d = os.path.join(os.path.dirname(path), sub)
    os.makedirs(d, exist_ok=True)
    shutil.move(path, os.path.join(d, os.path.basename(path)))


def gh_get(ctx, url):
    """-> (json or None, note). Handles 403/429 rate limits without raising."""
    try:
        return ctx.http.get_json(url, headers={'User-Agent': UA, 'Accept': 'application/vnd.github+json'}, timeout=20, min_interval=0.5), ''
    except urllib.error.HTTPError as e:
        return None, 'rate limited or refused (HTTP %s)' % e.code if e.code in (403, 429) else 'HTTP %s' % e.code
    except Exception as e:
        return None, type(e).__name__


def repo_commits(ctx, repo, hours, seen):
    """-> (list of commit dicts to review oldest first, note). Merge commits and already reviewed SHAs are left out."""
    since = (ctx.now.astimezone(timezone.utc) - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
    data, note = gh_get(ctx, 'https://api.github.com/repos/%s/commits?per_page=30&since=%s' % (repo, urllib.parse.quote(since)))
    if not isinstance(data, list):
        return [], note or 'unexpected answer'
    out = [c for c in data if isinstance(c, dict) and isinstance(c.get('sha'), str) and len(c.get('parents') or []) <= 1 and c['sha'] not in seen]
    return list(reversed(out)), ''


def commit_text(ctx, repo, sha, max_chars):
    d, note = gh_get(ctx, 'https://api.github.com/repos/%s/commits/%s' % (repo, sha))
    if not isinstance(d, dict):
        return None, note
    msg = str(((d.get('commit') or {}).get('message')) or '').splitlines()
    text = 'Commit %s: %s\n' % (sha[:7], msg[0] if msg else '')
    for f in (d.get('files') or []):
        if isinstance(f, dict) and f.get('patch'):
            text += '--- %s ---\n%s\n' % (f.get('filename', '?'), f['patch'])
        if len(text) >= max_chars:
            break
    return text[:max_chars], ''


def run(ctx):
    s = _settings(ctx.settings)
    token = ctx.option('ai_layer_token').strip()
    if not token:
        ctx.log('skipped: option ai_layer_token is empty (set the AI layer api_token in the add-on options)')
        return True
    layer = Layer(ctx.option('ai_layer_url').strip() or DEFAULT_URL, token, ctx.http.opener)
    try:
        status = layer.status()
    except Exception as e:
        ctx.log('error: AI layer /status failed (%s)' % type(e).__name__)
        return False
    provs, total = budget(status, s['order'], s['reserve'], s['max_calls'])
    ctx.log('budget: %d calls (%s), max_calls=%d' % (total, ', '.join('%s=%d' % (k, v['spare']) for k, v in provs.items()) or 'no usable provider', s['max_calls']))
    folder, date = s['folder'], ctx.now.strftime('%Y%m%d')
    state = ctx.state.load('state')
    seen = list(state.get('reviewed') or [])
    good, bad = read_queue(folder)
    for p, why in bad:
        ctx.log('%s queue file %s: %s' % ('[DRY] would refuse' if ctx.dry else 'refused', os.path.basename(p), why))
        if not ctx.dry:
            move(p, 'refused')
    commits, note = [], ''
    if s['repo']:
        if REPO_RE.match(s['repo']):
            commits, note = repo_commits(ctx, s['repo'], s['repo_hours'], seen)
            ctx.log('repo %s: %d commits to review%s' % (s['repo'], len(commits), (' (' + note + ')') if note else ''))
        else:
            ctx.log('repo setting is not owner/name: repo review skipped')
    if ctx.dry:
        ctx.log('[DRY] would run %d queued tasks (%s) and %d repo commits (%s); no AI calls made, nothing written'
                % (len(good), ', '.join(o['id'] for _, o in good) or '-', len(commits), ', '.join(c['sha'][:7] for c in commits) or '-'))
        return True
    r = Run(ctx, s, layer, provs, total)
    done = failed = 0
    reviewed = []
    for p, o in good:
        if r.left <= 0:
            break
        fo = _int(o.get('fanout'), s['fanout'], 1, 4) if 'fanout' in o else s['fanout']
        res = r.task(o['id'], o['kind'], o['text'], fo)
        if res is None:
            break
        if res['answers']:
            write_done(folder, date, res)
            move(p, 'processed')
            done += 1
        else:
            move(p, 'failed')
            failed += 1
    for c in commits:
        if r.left <= 0:
            break
        txt, why = commit_text(ctx, s['repo'], c['sha'], s['max_diff'])
        if txt is None:
            ctx.log('repo commit %s skipped (%s)' % (c['sha'][:7], why))
            if 'rate' in why:
                break
            continue
        res = r.task('repo-' + c['sha'][:7], 'review', REVIEW_PROMPT + txt, 2)
        if res is None:
            break
        if res['answers']:
            write_done(folder, date, res)
            reviewed.append(c['sha'])
            done += 1
        else:
            failed += 1
    used = sum(r.calls.values())
    os.makedirs(os.path.join(folder, 'reports'), exist_ok=True)
    report = {'date': date, 'budget': total, 'calls_used': used, 'calls_by_provider': r.calls, 'tasks_done': done, 'tasks_failed': failed,
              'tasks_refused': len(bad), 'repo_commits_reviewed': [c[:7] for c in reviewed]}
    with open(os.path.join(folder, 'reports', date + '.json'), 'w') as f:
        json.dump(report, f)
    state = ctx.state.load('state')
    ctx.state.save('state', {'reviewed': (seen + reviewed)[-KEEP_REVIEWED:], 'runs': int(state.get('runs') or 0) + 1,
                             'tasks_done': int(state.get('tasks_done') or 0) + done, 'calls_used': int(state.get('calls_used') or 0) + used,
                             'last_budget': total, 'last_activity': ctx.now.isoformat(timespec='seconds')})
    ctx.log('done: %d tasks done, %d failed, %d refused, %d calls used of %d' % (done, failed, len(bad), used, total))
    return True
