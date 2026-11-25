"""First installation of a planned event, with collision checks and a durable journal.

This module is called only from the authenticated administrator page. It never adopts
existing entities or retries a partially completed installation automatically.
"""
import threading
import urllib.error

import install_plan
import model as M

_LOCK = threading.Lock()


def _missing(ha, path):
    try:
        ha.get(path)
        return False
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True
        raise


def preview(store, ha, slug):
    if ha is None:
        raise M.RuleError('conflict', 'Home Assistant is unavailable.')
    event = store.record(slug)
    previous = store.installation(slug)
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


def apply(store, ha, slug):
    """Install once. Record intent before every remote write; ambiguous failures stop retries."""
    with _LOCK:
        plan = preview(store, ha, slug)
        if plan['issues']:
            raise M.RuleError('conflict', ' '.join(plan['issues']))
        journal = []
        store.save_installation(slug, 'installing', journal)
        try:
            if M.clean_event(store.record(slug)) != plan['event']:
                raise RuntimeError('event changed during preparation')
            for domain, items in plan['helpers'].items():
                for key, config in items.items():
                    journal.append({'kind': 'helper', 'domain': domain, 'id': key, 'state': 'starting'})
                    store.save_installation(slug, 'installing', journal)
                    data = dict(config, type=domain + '/create', name=key)
                    if key.endswith('_random_effects'):
                        data['initial'] = True
                    elif domain == 'input_boolean':
                        data['initial'] = False
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
                if not _missing(ha, 'config/script/config/' + key):
                    raise RuntimeError('script appeared during installation')
                journal.append({'kind': 'script', 'id': key, 'state': 'starting'})
                store.save_installation(slug, 'installing', journal)
                ha.post('config/script/config/' + key, config)
                journal[-1]['state'] = 'created'
                store.save_installation(slug, 'installing', journal)
            for config in plan['automations']:
                key = config['id']
                if not _missing(ha, 'config/automation/config/' + key):
                    raise RuntimeError('automation appeared during installation')
                journal.append({'kind': 'automation', 'id': key, 'state': 'starting'})
                store.save_installation(slug, 'installing', journal)
                ha.post('config/automation/config/' + key, config)
                journal[-1]['state'] = 'created'
                store.save_installation(slug, 'installing', journal)
        except Exception as exc:
            store.save_installation(slug, 'needs_review', journal)
            raise M.RuleError('conflict', 'Installation stopped partway through. Some items may have been created; review the installation record before retrying.') from exc
        # The HA config endpoints schedule reloads asynchronously; an accepted
        # response is not proof that the resulting entities have loaded.
        store.save_installation(slug, 'submitted', journal)
        return {'status': 'submitted', 'mode': plan['mode']}
