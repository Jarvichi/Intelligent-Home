"""Update only the Home Assistant objects recorded as created by this add-on.

Every change has its previous and intended contents journalled before the request.
A retry accepts only the previous or intended value; an outside edit stops it.
"""
import copy
import urllib.error

import install_plan
import model as M


def _read(ha, kind, key):
    try:
        return ha.get('config/%s/config/%s' % (kind, key))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _mode_off(ha, slug):
    state = ha.get('states/input_boolean.%s_mode' % slug)
    if not isinstance(state, dict) or state.get('state') != 'off':
        raise M.RuleError('conflict', 'Turn the event mode off before applying changes.')


def _owned(journal):
    result = {}
    for entry in journal:
        if entry.get('kind') in ('helper', 'script', 'automation') and entry.get('state') == 'created':
            key = (entry['kind'], entry.get('domain', ''), entry['id'])
            result[key] = entry
    return result


def preview(store, ha, slug):
    previous = store.installation(slug)
    if not previous or previous['status'] not in ('submitted', 'available', 'needs_apply', 'updating', 'needs_review'):
        raise M.RuleError('conflict', 'This installation cannot be updated automatically.')
    transaction = next((x for x in previous['journal'] if x.get('kind') == 'update_transaction'), None)
    if transaction:
        _mode_off(ha, slug)
        return {'issues': [], 'event': store.record(slug), 'helpers': {}, 'scripts': {}, 'automations': [],
                'mode': 'input_boolean.%s_mode' % slug, 'operation': 'resume', 'unverified': False,
                'message': 'An interrupted update can be resumed after checking the recorded changes.'}
    if previous['status'] not in ('submitted', 'available', 'needs_apply'):
        raise M.RuleError('conflict', 'The first installation was interrupted and needs review before changes can be applied.')
    _mode_off(ha, slug)
    owned = _owned(previous['journal'])
    if not owned:
        raise M.RuleError('conflict', 'No owned items are recorded for this event.')
    states = ha.get('states')
    registry = ha.ws({'type': 'config/entity_registry/list'})
    if not isinstance(states, list) or not isinstance(registry, list):
        raise M.RuleError('conflict', 'Could not verify existing Home Assistant items.')
    entities = {x['entity_id'] for x in states + registry if isinstance(x, dict) and isinstance(x.get('entity_id'), str)}
    owned_entities = {domain + '.' + key if kind == 'helper' else 'script.' + key
                      for kind, domain, key in owned if kind in ('helper', 'script')}
    plan = install_plan.build(store.record(slug), entities - owned_entities, existing_owned=True)
    current = {}
    unverified = False
    for (kind, domain, key), entry in owned.items():
        if kind == 'helper':
            if not any(x.get('entity_id') == domain + '.' + key and x.get('unique_id') == key and x.get('platform') == domain for x in registry if isinstance(x, dict)):
                plan['issues'].append('An owned helper is missing; restore it before applying changes.')
            continue
        if kind == 'script':
            matches = [x for x in registry if isinstance(x, dict) and x.get('platform') == 'script' and x.get('unique_id') == key]
            if matches and any(x.get('entity_id') != 'script.' + key for x in matches):
                plan['issues'].append('An owned effect was renamed in Home Assistant; restore its original entity name before applying.')
        value = _read(ha, kind, key)
        if value is None:
            plan['issues'].append('An owned rule is missing; restore it before applying changes.')
        elif 'config' in entry and value != entry['config']:
            unverified = True
            plan.setdefault('review_warnings', []).append('An add-on-owned rule was changed outside this page. Applying will replace those outside changes.')
        elif 'config' not in entry:
            unverified = True
        current[(kind, key)] = value
    desired = {('script', key): value for key, value in plan['scripts'].items()}
    desired.update({('automation', value['id']): value for value in plan['automations']})
    for kind, key in desired:
        if (kind, key) not in current and _read(ha, kind, key) is not None:
            plan['issues'].append('An existing rule uses a name needed by this event. It will not be overwritten.')
    baseline = next((x.get('event') for x in reversed(previous['journal']) if x.get('kind') == 'event_baseline'), None)
    plan.update(operation='update', unverified=unverified, current=current, owned=owned, desired=desired, previous_event=baseline)
    return plan


def _run_transaction(store, ha, slug, journal, tx):
    for operation in tx['operations']:
        _mode_off(ha, slug)
        if operation['kind'] == 'helper':
            domain, key = operation['domain'], operation['id']
            states = ha.get('states')
            registry = ha.ws({'type': 'config/entity_registry/list'})
            entities = {x.get('entity_id') for x in states + registry if isinstance(x, dict)}
            if domain + '.' + key in entities:
                verified = operation.get('created_id') == key and any(x.get('entity_id') == domain + '.' + key and x.get('unique_id') == key and x.get('platform') == domain for x in registry if isinstance(x, dict))
                if not verified:
                    raise M.RuleError('conflict', 'A helper may have been created during the interrupted update. Review it before retrying.')
                operation['state'] = 'done'
                store.save_installation(slug, 'updating', journal)
                continue
            if operation['state'] == 'done':
                raise M.RuleError('conflict', 'A previously created helper is now missing.')
            operation['state'] = 'starting'
            store.save_installation(slug, 'updating', journal)
            config = dict(operation['config'], type=domain + '/create', name=key)
            if key.endswith('_random_effects'):
                config['initial'] = True
            result = ha.ws(config)
            if isinstance(result, dict) and isinstance(result.get('id'), str):
                operation['created_id'] = result['id']
                store.save_installation(slug, 'updating', journal)
            if not isinstance(result, dict) or result.get('id') != key:
                raise M.RuleError('conflict', 'Home Assistant assigned a different helper name. Review it before retrying.')
            state = ha.get('states/' + domain + '.' + key)
            if not isinstance(state, dict) or state.get('entity_id') != domain + '.' + key:
                raise M.RuleError('conflict', 'The new helper could not be verified.')
        else:
            kind, key = operation['kind'], operation['id']
            current = _read(ha, kind, key)
            if current == operation['after']:
                if operation['state'] == 'pending' and current != operation['before']:
                    raise M.RuleError('conflict', 'A rule changed before the update reached it. Nothing further was changed.')
                operation['state'] = 'done'
                store.save_installation(slug, 'updating', journal)
                continue
            if operation['state'] == 'done':
                raise M.RuleError('conflict', 'A completed rule changed outside this add-on. Nothing further was changed.')
            if current != operation['before']:
                raise M.RuleError('conflict', 'A rule changed outside this add-on during the update. Nothing further was changed.')
            operation['state'] = 'starting'
            store.save_installation(slug, 'updating', journal)
            path = 'config/%s/config/%s' % (kind, key)
            if operation['after'] is None:
                ha._rest('DELETE', path)
            else:
                ha.post(path, operation['after'])
        operation['state'] = 'done'
        store.save_installation(slug, 'updating', journal)
    final = copy.deepcopy(tx['owned_after'])
    store.save_installation(slug, 'submitted', final)
    return {'status': 'submitted', 'mode': 'input_boolean.%s_mode' % slug}


def apply(store, ha, slug, plan, acknowledge_unverified=False):
    previous = store.installation(slug)
    tx = next((x for x in previous['journal'] if x.get('kind') == 'update_transaction'), None)
    if tx:
        if M.clean_event(store.record(slug)) != tx['event']:
            raise M.RuleError('conflict', 'Finish the interrupted update before applying newer edits.')
        journal = previous['journal']
    else:
        if plan['issues']:
            raise M.RuleError('conflict', ' '.join(plan['issues']))
        if plan['unverified'] and not acknowledge_unverified:
            raise M.RuleError('conflict', 'Review and confirm the existing installation before updating it.')
        owned = plan['owned']
        operations = []
        final = [copy.deepcopy(x) for (kind, domain, key), x in owned.items() if kind == 'helper']
        for domain, items in plan['helpers'].items():
            for key, config in items.items():
                if ('helper', domain, key) not in owned:
                    operations.append({'kind': 'helper', 'domain': domain, 'id': key, 'config': config, 'state': 'pending'})
                    final.append({'kind': 'helper', 'domain': domain, 'id': key, 'created_id': key, 'state': 'created'})
        for kind in ('script', 'automation'):
            for (item_kind, key), config in plan['desired'].items():
                if item_kind != kind:
                    continue
                before = plan['current'].get((kind, key))
                if before != config:
                    operations.append({'kind': kind, 'id': key, 'before': before, 'after': config, 'state': 'pending'})
                final.append({'kind': kind, 'id': key, 'state': 'created', 'config': config})
        # Stop removed automations before removing scripts they may call.
        for kind in ('automation', 'script'):
            for (old_kind, key), before in plan['current'].items():
                if old_kind == kind and (kind, key) not in plan['desired']:
                    operations.append({'kind': kind, 'id': key, 'before': before, 'after': None, 'state': 'pending'})
        final.append({'kind': 'event_baseline', 'event': plan['event']})
        tx = {'kind': 'update_transaction', 'event': plan['event'], 'operations': operations, 'owned_after': final}
        journal = copy.deepcopy(previous['journal']) + [tx]
        store.save_installation(slug, 'updating', journal)
        if M.clean_event(store.record(slug)) != tx['event']:
            store.save_installation(slug, 'needs_apply', previous['journal'])
            raise M.RuleError('conflict', 'The event changed during preparation. Review it and try again.')
    try:
        return _run_transaction(store, ha, slug, journal, tx)
    except Exception as exc:
        store.save_installation(slug, 'needs_review', journal)
        if isinstance(exc, M.RuleError):
            raise
        raise M.RuleError('conflict', 'Update interrupted. Review and resume the recorded changes.') from exc
