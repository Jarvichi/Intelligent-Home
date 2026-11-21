"""Client for the IH Tasks API: the only place IH Work gets its tasks from (stdlib only, opener injectable for tests).
Only tasks in the stage Approved are handed out, so the approval gate stays with the owner. No fallback to any other list."""
import json
import urllib.error
import urllib.parse
import urllib.request

MAX_TASKS = 25          # approved tasks read per check (each one is a second request for its plan text)
TIMEOUT_S = 15


class TasksError(RuntimeError):
    """The tasks API could not be used. The message never contains the token or any task text."""


class TasksClient:
    def __init__(self, url, token, opener=None):
        self.url = (url or '').strip().rstrip('/')
        self.token = (token or '').strip()
        self.opener = opener or urllib.request.urlopen

    @property
    def configured(self):
        return bool(self.url and self.token)

    def _call(self, method, path, body=None):
        if not self.configured:
            raise TasksError('tasks_api_url and tasks_api_token are not set')
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with self.opener(req, timeout=TIMEOUT_S) as r:
                return json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as e:
            raise TasksError('IH Tasks answered %s' % e.code)
        except (OSError, ValueError) as e:
            raise TasksError('IH Tasks could not be reached (%s)' % type(e).__name__)

    def approved(self):
        """-> items in the shape feed.pick expects: uid = task number, summary = title, description = plan, due, priority, approved = True."""
        tasks = (self._call('GET', '/tasks?stage=approved') or {}).get('tasks')
        if not isinstance(tasks, list):
            raise TasksError('IH Tasks gave an unexpected answer')
        out = []
        for t in tasks[:MAX_TASKS]:
            if not isinstance(t, dict) or not isinstance(t.get('id'), int) or t.get('stage') != 'approved' or t.get('needs_input'):
                continue
            full = self._call('GET', '/tasks/%d' % t['id'])
            if not isinstance(full, dict) or full.get('stage') != 'approved':
                continue
            out.append(dict(uid=str(t['id']), summary=str(full.get('title') or ''), description=str(full.get('plan') or ''),
                            due=full.get('due') or None, priority=full.get('priority') or 'normal', approved=True))
        return out

    def move(self, number, to, reason='', result=''):
        """Move a task to another stage as the worker. -> '' when done, else a short problem text. Never raises (a refused move is not fatal)."""
        body = {'to': to}
        if reason:
            body['reason'] = reason[:1900]
        if result:
            body['result'] = result[:19000]
        try:
            self._call('POST', '/tasks/%d/move' % int(number), body)
            return ''
        except (TasksError, ValueError, TypeError) as e:
            return str(e)[:120]
