"""First installation of a planned event, with collision checks and a durable journal.

This module is called only from the authenticated administrator page. It never adopts
existing entities or retries a partially completed installation automatically.
"""
import threading
import urllib.error
import copy
import hashlib
import json

import install_plan
import model as M
import update_installation

_LOCK = threading.Lock()


def review_token(plan, installation):
    """Bind confirmation to the plan, current rule contents and installation journal."""
    current = [[kind, key, value] for (kind, key), value in sorted(plan.get('current', {}).items())]
    data = {'event': plan['event'], 'operation': plan.get('operation', 'install'), 'issues': plan['issues'],
            'unverified': plan.get('unverified', False), 'current': current, 'installation': installation}
    encoded = json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def verify_available(store, ha, slug):
    """Check submitted config and entities exist; this is not proof of execution."""
    with _LOCK:
        record = store.installation(slug)
        if not record or record['status'] not in ('submitted', 'available'):
            raise M.RuleError('conflict', 'Finish applying the event before checking availability.')
        try:
            states = ha.get('states')
            registry = ha.ws({'type': 'config/entity_registry/list'})
            if not isinstance(states, list) or not isinstance(registry, list):
                raise ValueError('invalid Home Assistant response')
            state_map = {item.get('entity_id'): item.get('state') for item in states if isinstance(item, dict)}
            for entry in record['journal']:
                kind, key = entry.get('kind'), entry.get('id')
                if kind == 'helper' and entry.get('state') == 'created':
                    entity = entry['domain'] + '.' + key
                    if state_map.get(entity) in (None, 'unavailable', 'unknown'):
                        raise M.RuleError('conflict', 'An event helper is not available yet. Try checking again shortly.')
                    if not any(x.get('entity_id') == entity and x.get('unique_id') == key and x.get('platform') == entry['domain'] for x in registry if isinstance(x, dict)):
                        raise M.RuleError('conflict', 'An event helper could not be verified.')
                elif kind in ('script', 'automation') and entry.get('state') == 'created':
                    if 'config' not in entry:
                        raise M.RuleError('conflict', 'Review and reapply this older event before checking its rules.')
                    if update_installation._read(ha, kind, key) != entry['config']:
                        raise M.RuleError('conflict', 'An event rule differs from the applied plan. Review it before turning the event on.')
                    matches = [x for x in registry if isinstance(x, dict) and x.get('platform') == kind and x.get('unique_id') == key]
                    if not matches or state_map.get(matches[0].get('entity_id')) in (None, 'unavailable', 'unknown'):
                        raise M.RuleError('conflict', 'An event rule is not available yet. Try checking again shortly.')
                    if kind == 'script' and matches[0].get('entity_id') != 'script.' + key:
                        raise M.RuleError('conflict', 'An event effect was renamed in Home Assistant; restore its original name first.')
        except M.RuleError:
            raise
        except Exception as exc:
            raise M.RuleError('conflict', 'Could not check the event in Home Assistant. Try again.') from exc
        store.mark_available(slug, record['journal'])
        return {'status': 'available'}


def _missing(ha, path):
    try:
        ha.get(path)
        return False
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True
        raise


def _ensure_off_if_present(ha, slug):
    try:
        mode = ha.get('states/input_boolean.%s_mode' % slug)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        return
    if not isinstance(mode, dict) or mode.get('state') != 'off':
        raise M.RuleError('conflict', 'Turn the event mode off before applying changes.')


def preview(store, ha, slug):
    if ha is None:
        raise M.RuleError('conflict', 'Home Assistant is unavailable.')
    event = store.record(slug)
    previous = store.installation(slug)
    if previous:
        if any(item.get('kind') == 'initial_transaction' for item in previous['journal']):
            return {'issues': [], 'event': store.record(slug), 'helpers': {}, 'scripts': {}, 'automations': [],
                    'mode': 'input_boolean.%s_mode' % slug, 'operation': 'resume_initial', 'unverified': False,
                    'message': 'The interrupted installation can be checked and resumed.'}
        if previous['status'] in ('installing', 'needs_review') and not any(item.get('kind') == 'update_transaction' for item in previous['journal']):
            plan = install_plan.build(event)
            plan.update(operation='resume_legacy', unverified=True,
                        message='An older interrupted installation has no saved plan. Review it before resuming.')
            return plan
        try:
            return update_installation.preview(store, ha, slug)
        except M.RuleError:
            raise
        except Exception as exc:
            raise M.RuleError('conflict', 'Could not verify the existing installation. Nothing was applied.') from exc
    try:
        states = ha.get('states')
        registry = ha.ws({'type': 'config/entity_registry/list'})
        if not isinstance(states, list) or not isinstance(registry, list):
            raise ValueError('invalid Home Assistant response')
        entities = {x['entity_id'] for x in states + registry if isinstance(x, dict) and isinstance(x.get('entity_id'), str)}
        plan = install_plan.build(event, entities)
        for key in plan['scripts']:
            if not _missing(ha, 'config/script/config/' + key):
                plan['issues'].append('An existing script uses a name needed by this event. It will not be overwritten.')
        for automation in plan['automations']:
            if not _missing(ha, 'config/automation/config/' + automation['id']):
                plan['issues'].append('An existing automation uses a name needed by this event. It will not be overwritten.')
    except M.RuleError:
        raise
    except Exception as exc:
        raise M.RuleError('conflict', 'Could not verify existing Home Assistant items. Nothing was applied.') from exc
    if previous:
        plan['issues'].append('This event already has an installation record (%s). Updating or recovering it is not supported yet.' % previous['status'])
    return plan


def apply(store, ha, slug, acknowledge_unverified=False, expected_review=None):
    """Install once. Record intent before every remote write; ambiguous failures stop retries."""
    with _LOCK:
        plan = preview(store, ha, slug)
        if expected_review is not None and review_token(plan, store.installation(slug)) != expected_review:
            raise M.RuleError('conflict', 'The event or installed rules changed after review. Review them again before applying.')
        if plan['issues']:
            raise M.RuleError('conflict', ' '.join(plan['issues']))
        if plan.get('operation') in ('update', 'resume'):
            return update_installation.apply(store, ha, slug, plan, acknowledge_unverified)
        if plan.get('operation') == 'resume_initial':
            return _resume_initial(store, ha, slug)
        if plan.get('operation') == 'resume_legacy':
            if not acknowledge_unverified:
                raise M.RuleError('conflict', 'Review and confirm the older installation before resuming.')
            previous = store.installation(slug)
            desired = {('script', key) for key in plan['scripts']}
            desired.update(('automation', value['id']) for value in plan['automations'])
            if any((item['kind'], item['id']) not in desired for item in previous['journal'] if item.get('kind') in ('script', 'automation')):
                raise M.RuleError('conflict', 'The saved event no longer matches the interrupted installation. Review it before resuming.')
            journal = [{'kind': 'initial_transaction', 'event': plan['event'], 'helpers': plan['helpers'],
                        'scripts': plan['scripts'], 'automations': plan['automations']}] + copy.deepcopy(previous['journal'])
            store.save_installation(slug, 'installing', journal)
            return _resume_initial(store, ha, slug)
        journal = [{'kind': 'initial_transaction', 'event': plan['event'], 'helpers': plan['helpers'],
                    'scripts': plan['scripts'], 'automations': plan['automations']}]
        store.save_installation(slug, 'installing', journal)
        if M.clean_event(store.record(slug)) != plan['event']:
            store.discard_unstarted_installation(slug, journal)
            raise M.RuleError('conflict', 'The event changed during preparation. Review it and try again.')
        try:
            for domain, items in plan['helpers'].items():
                for key, config in items.items():
                    _ensure_off_if_present(ha, slug)
                    journal.append({'kind': 'helper', 'domain': domain, 'id': key, 'state': 'starting'})
                    store.save_installation(slug, 'installing', journal)
                    data = dict(config, type=domain + '/create', name=key)
                    if key.endswith('_random_effects'):
                        data['initial'] = True
                    result = ha.ws(data)
                    if isinstance(result, dict) and isinstance(result.get('id'), str):
                        journal[-1]['created_id'] = result['id']
                        store.save_installation(slug, 'installing', journal)
                    if not isinstance(result, dict) or result.get('id') != key:
                        raise RuntimeError('Home Assistant assigned a different helper id')
                    state = ha.get('states/' + domain + '.' + key)
                    if not isinstance(state, dict) or state.get('entity_id') != domain + '.' + key:
                        raise RuntimeError('Home Assistant assigned a different helper entity')
                    journal[-1]['state'] = 'created'
                    store.save_installation(slug, 'installing', journal)
            for key, config in plan['scripts'].items():
                _ensure_off_if_present(ha, slug)
                if not _missing(ha, 'config/script/config/' + key):
                    raise RuntimeError('script appeared during installation')
                journal.append({'kind': 'script', 'id': key, 'state': 'starting', 'config': config})
                store.save_installation(slug, 'installing', journal)
                ha.post('config/script/config/' + key, config)
                journal[-1]['state'] = 'created'
                store.save_installation(slug, 'installing', journal)
            for config in plan['automations']:
                _ensure_off_if_present(ha, slug)
                key = config['id']
                if not _missing(ha, 'config/automation/config/' + key):
                    raise RuntimeError('automation appeared during installation')
                journal.append({'kind': 'automation', 'id': key, 'state': 'starting', 'config': config})
                store.save_installation(slug, 'installing', journal)
                ha.post('config/automation/config/' + key, config)
                journal[-1]['state'] = 'created'
                store.save_installation(slug, 'installing', journal)
        except Exception as exc:
            store.save_installation(slug, 'needs_review', journal)
            raise M.RuleError('conflict', 'Installation stopped partway through. Some items may have been created; review the installation record before retrying.') from exc
        # The HA config endpoints schedule reloads asynchronously; an accepted
        # response is not proof that the resulting entities have loaded.
        journal = [item for item in journal if item.get('kind') != 'initial_transaction']
        journal.append({'kind': 'event_baseline', 'event': plan['event']})
        store.save_installation(slug, 'submitted', journal)
        return {'status': 'submitted', 'mode': plan['mode']}


def _resume_initial(store, ha, slug):
    """Resume known first-install writes; an ambiguous helper is never adopted."""
    previous = store.installation(slug)
    journal = copy.deepcopy(previous['journal'])
    tx = next(item for item in journal if item.get('kind') == 'initial_transaction')
    if M.clean_event(store.record(slug)) != tx['event']:
        raise M.RuleError('conflict', 'The event changed since installation started; review it before retrying.')
    try:
        _ensure_off_if_present(ha, slug)
        store.save_installation(slug, 'installing', journal)
        for domain, items in tx['helpers'].items():
            for key, config in items.items():
                _ensure_off_if_present(ha, slug)
                entry = next((x for x in journal if x.get('kind') == 'helper' and x.get('domain') == domain and x.get('id') == key), None)
                states = ha.get('states')
                registry = ha.ws({'type': 'config/entity_registry/list'})
                exists = any(x.get('entity_id') == domain + '.' + key for x in states + registry if isinstance(x, dict))
                if exists:
                    verified = entry and entry.get('created_id') == key and any(x.get('entity_id') == domain + '.' + key and x.get('unique_id') == key and x.get('platform') == domain for x in registry if isinstance(x, dict))
                    if not verified:
                        raise M.RuleError('conflict', 'A helper may have been created during the interruption. Review it before retrying.')
                    entry['state'] = 'created'
                    store.save_installation(slug, 'installing', journal)
                    continue
                if entry and entry.get('state') == 'created':
                    raise M.RuleError('conflict', 'A previously created helper is now missing.')
                if entry is None:
                    entry = {'kind': 'helper', 'domain': domain, 'id': key, 'state': 'starting'}
                    journal.append(entry)
                entry['state'] = 'starting'
                store.save_installation(slug, 'installing', journal)
                data = dict(config, type=domain + '/create', name=key)
                if key.endswith('_random_effects'):
                    data['initial'] = True
                result = ha.ws(data)
                if isinstance(result, dict) and isinstance(result.get('id'), str):
                    entry['created_id'] = result['id']
                    store.save_installation(slug, 'installing', journal)
                if not isinstance(result, dict) or result.get('id') != key:
                    raise M.RuleError('conflict', 'Home Assistant assigned a different helper name.')
                state = ha.get('states/' + domain + '.' + key)
                if not isinstance(state, dict) or state.get('entity_id') != domain + '.' + key:
                    raise M.RuleError('conflict', 'The new helper could not be verified.')
                entry['state'] = 'created'
                store.save_installation(slug, 'installing', journal)
        desired = [('script', key, value) for key, value in tx['scripts'].items()]
        desired += [('automation', value['id'], value) for value in tx['automations']]
        for kind, key, config in desired:
            _ensure_off_if_present(ha, slug)
            entry = next((x for x in journal if x.get('kind') == kind and x.get('id') == key), None)
            current = update_installation._read(ha, kind, key)
            if current is not None:
                if entry is None or current != config:
                    raise M.RuleError('conflict', 'An existing rule differs from the recorded installation. Nothing further was changed.')
                entry['state'] = 'created'
                entry['config'] = config
                store.save_installation(slug, 'installing', journal)
                continue
            if entry and entry.get('state') == 'created':
                raise M.RuleError('conflict', 'A previously created rule is now missing.')
            if entry is None:
                entry = {'kind': kind, 'id': key, 'state': 'starting', 'config': config}
                journal.append(entry)
            entry['state'] = 'starting'
            entry['config'] = config
            store.save_installation(slug, 'installing', journal)
            ha.post('config/%s/config/%s' % (kind, key), config)
            entry['state'] = 'created'
            store.save_installation(slug, 'installing', journal)
    except Exception as exc:
        store.save_installation(slug, 'needs_review', journal)
        if isinstance(exc, M.RuleError):
            raise
        raise M.RuleError('conflict', 'Installation interrupted. Review and resume the recorded changes.') from exc
    final = [item for item in journal if item.get('kind') != 'initial_transaction']
    final.append({'kind': 'event_baseline', 'event': tx['event']})
    store.save_installation(slug, 'submitted', final)
    return {'status': 'submitted', 'mode': 'input_boolean.%s_mode' % slug}
