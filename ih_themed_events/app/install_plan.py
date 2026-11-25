"""Construct and check a Home Assistant installation plan without making changes."""
import generator as G
import model as M


def build(event, existing_entity_ids=(), existing_automation_ids=(), existing_owned=False):
    """Return a plan and blocking issues; never adopt an existing Home Assistant object."""
    event = M.clean_event(event)
    ids = G.ids(event)
    helpers = G.helpers(event)
    scripts = {event['slug'] + '_look': G.look_script(event), event['slug'] + '_effect_sound': G.effect_sound_script(event)}
    scripts.update({event['slug'] + '_' + e['id']: G.built_effect_script(event, e) for e in event['effects'] if e.get('steps')})
    scripts = {name: value for name, value in scripts.items() if value is not None}
    automations = [x for x in (G.mode_switch(event), G.safety(event), G.random_effects(event)) if x] + G.timed_effects(event)
    issues = []
    if event['status'] == 'live' and not existing_owned:
        issues.append('This event is already live. Existing custom rules cannot be safely taken over automatically.')
    if any(not effect.get('steps') for effect in event['effects']):
        issues.append('Each effect needs a visual sequence before it can be installed.')
    for effect in event['effects']:
        if effect.get('steps') and not G.built_effect_script(event, effect):
            issues.append('Choose the required devices and sound for %s.' % effect['name'])
        if effect.get('steps') and sum(x.get('seconds', 0) for x in effect['steps']) * effect['repeat'] >= 90:
            issues.append('%s runs too long for the current effect safety timer.' % effect['name'])
    entity_ids = {domain + '.' + key for domain, items in helpers.items() for key in items}
    entity_ids.update('script.' + key for key in scripts)
    collisions = sorted(entity_ids.intersection(existing_entity_ids))
    collisions += sorted(set(a['id'] for a in automations).intersection(existing_automation_ids))
    if collisions:
        issues.append('Existing Home Assistant items use names needed by this event. They will not be overwritten.')
    return {'helpers': helpers, 'scripts': scripts, 'automations': automations, 'issues': issues, 'mode': ids['mode'], 'event': event}
