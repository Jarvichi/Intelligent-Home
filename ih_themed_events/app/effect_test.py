"""Run a single validated draft effect on the administrator's explicit request."""
import copy
import threading
import time
import uuid
import urllib.error
from contextlib import contextmanager

import generator as G
import model as M

_LOCK = threading.Lock()
_JOBS = {}
_MAX_SECONDS = 89
_COOLDOWN_UNTIL = 0
_EVENT_CHANGES = set()


class ExecutionFailed(RuntimeError):
    """Home Assistant reported the sequence failed and has finished it."""


def prepare(raw):
    event = M.clean_event(raw)
    if len(event['effects']) != 1 or not event['effects'][0].get('steps'):
        raise M.RuleError('bad', 'Add a sequence of steps before testing this effect.')
    effect = event['effects'][0]
    duration = sum(step.get('seconds', 0) for step in effect['steps']) * effect['repeat']
    if duration > _MAX_SECONDS:
        raise M.RuleError('bad', 'The effect must be shorter than 90 seconds to test.')
    sound = effect['sound']
    if any(step['type'] == 'sound' for step in effect['steps']):
        if not sound or (sound.startswith('media-source://') and not sound.startswith('media-source://media_source/local/')) or '..' in sound:
            raise M.RuleError('bad', 'Choose a local sound before testing.')
        if not sound.startswith('media-source://') and ('/' in sound or not event['sounds_folder']):
            raise M.RuleError('bad', 'Choose a local sound before testing.')
    script = G.built_effect_script(event, effect)
    if script is None:
        raise M.RuleError('bad', 'Choose the lights, speakers and sound needed by this effect.')
    # The compiled script is built by the add-on, never accepted from the browser.
    seq = script['sequence']
    snapshot = next((x['data']['snapshot_entities'] for x in seq if x.get('action') == 'scene.create'), [])
    actions = [copy.deepcopy(x) for x in seq if 'repeat' in x]
    if not actions:
        raise M.RuleError('bad', 'Add a sequence of steps before testing this effect.')
    return event, actions, snapshot


def _execute(ha, actions):
    """Wait for HA's execute_script result, with enough time for a validated effect."""
    ws = ha._open()
    try:
        ws.s.settimeout(150)
        ws.send({'id': 1, 'type': 'execute_script', 'sequence': actions})
        while True:
            message = ws.recv()
            if message.get('id') == 1 and message.get('type') == 'result':
                if not message.get('success'):
                    raise ExecutionFailed('Home Assistant refused or failed the effect')
                return
    finally:
        ws.close()


def _run(job_id, ha, event, actions, snapshot, existing):
    global _COOLDOWN_UNTIL
    scene = 'ihte_test_' + uuid.uuid4().hex
    snapshot_created = False
    attempted_actions = False
    completed = False
    failed = False
    restore_failed = False
    hold_until = 0
    uncertain = False
    try:
        states = ha.get('states')
        if not isinstance(states, list):
            raise M.RuleError('conflict', 'Could not check the selected devices.')
        state_map = {x.get('entity_id'): x.get('state') for x in states if isinstance(x, dict)}
        required = []
        for step in actions[0]['repeat']['sequence']:
            if step.get('action', '').startswith(('light.', 'media_player.')):
                required.extend(step.get('target', {}).get('entity_id', []))
        if any(state_map.get(entity) in (None, 'unavailable', 'unknown') for entity in required):
            raise M.RuleError('conflict', 'A selected light or speaker is unavailable. Check the devices and try again.')
        if existing:
            # Testing an active event could compete with its automatic effects.
            try:
                state = ha.get('states/input_boolean.%s_mode' % event['slug'])
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
                state = None
            if state is not None and (not isinstance(state, dict) or state.get('state') != 'off'):
                raise M.RuleError('conflict', 'Turn the existing event mode off before testing this unsaved sequence.')
        if snapshot:
            ha.call_service('scene', 'create', {'scene_id': scene, 'snapshot_entities': snapshot})
            snapshot_created = True
            saved = ha.get('states/scene.' + scene)
            if not isinstance(saved, dict) or saved.get('entity_id') != 'scene.' + scene:
                raise M.RuleError('conflict', 'The current lighting could not be saved for restoration.')
        attempted_actions = True
        sequence = copy.deepcopy(actions)
        if snapshot_created:
            # Include restoration in the HA-side sequence as well, so it can
            # still run if the browser or add-on connection disappears.
            sequence.append({'action': 'scene.turn_on', 'target': {'entity_id': 'scene.' + scene}})
        _execute(ha, sequence)
        completed = True
    except M.RuleError as exc:
        failed = str(exc)
    except ExecutionFailed:
        failed = 'Home Assistant could not complete the effect. Check the selected devices and try again.'
    except Exception:
        failed = 'The test did not finish normally. Check the selected devices before trying again.'
        if attempted_actions:
            # A disconnected execution could still be running in Home Assistant.
            _COOLDOWN_UNTIL = time.monotonic() + 150
            hold_until = _COOLDOWN_UNTIL
            uncertain = True
            failed = 'The test connection was interrupted; the effect may still be running. Wait before trying again.'
    finally:
        if snapshot_created:
            if attempted_actions and not completed:
                try:
                    ha.call_service('scene', 'turn_on', {'entity_id': 'scene.' + scene})
                except Exception:
                    restore_failed = True
            if not restore_failed and not uncertain:
                try:
                    ha.call_service('scene', 'delete', {'entity_id': 'scene.' + scene})
                except Exception:
                    pass
        if restore_failed:
            failed = 'The test ended, but the previous lighting could not be restored. The snapshot was kept in Home Assistant.'
        elif uncertain and snapshot_created:
            failed += ' A lighting snapshot was kept in Home Assistant.'
        with _LOCK:
            job = _JOBS[job_id]
            job.update(status='error' if failed else 'done', message=failed or 'Effect test finished.', finished=time.monotonic(), hold_until=hold_until)


def start(ha, raw, existing=False):
    event, actions, snapshot = prepare(raw)
    if ha is None:
        raise M.RuleError('conflict', 'Home Assistant is unavailable.')
    with _LOCK:
        now = time.monotonic()
        for key, value in list(_JOBS.items()):
            if value.get('finished') and now - value['finished'] > 600:
                del _JOBS[key]
        if now < _COOLDOWN_UNTIL or any(job['status'] == 'running' for job in _JOBS.values()):
            raise M.RuleError('conflict', 'An effect test is already running or may still be finishing. Try again shortly.')
        if existing and event['slug'] in _EVENT_CHANGES:
            raise M.RuleError('conflict', 'Wait for the event change to finish before testing.')
        job_id = uuid.uuid4().hex
        _JOBS[job_id] = {'status': 'running', 'message': 'Testing the effect…', 'slug': event['slug'] if existing else None}
        worker = threading.Thread(target=_run, args=(job_id, ha, event, actions, snapshot, existing), daemon=True)
        try:
            worker.start()
        except Exception:
            del _JOBS[job_id]
            raise M.RuleError('conflict', 'Could not start the effect test.')
    return job_id


def status(job_id):
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise M.RuleError('missing', 'That effect test is no longer available.')
        return {'status': job['status'], 'message': job['message']}


def running(slug):
    with _LOCK:
        now = time.monotonic()
        return any(job.get('slug') == slug and (job['status'] == 'running' or job.get('hold_until', 0) > now) for job in _JOBS.values())


@contextmanager
def event_change(slug):
    with _LOCK:
        now = time.monotonic()
        if slug in _EVENT_CHANGES or any(job.get('slug') == slug and (job['status'] == 'running' or job.get('hold_until', 0) > now) for job in _JOBS.values()):
            raise M.RuleError('conflict', 'Wait for the effect test or event change to finish first.')
        _EVENT_CHANGES.add(slug)
    try:
        yield
    finally:
        with _LOCK:
            _EVENT_CHANGES.discard(slug)
