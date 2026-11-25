"""Explain whether the current event record can safely be translated to rules.

This module is read-only. It does not install rules or control Home Assistant.
"""
import generator


def inspect(event):
    issues = []
    if event['status'] == 'live':
        issues.append('This event is marked live and may already have independently managed rules. Its existing setup needs to be reviewed before installation.')
    if event['when']:
        issues.append('The event date is saved for planning; automatic start and stop by date are not supported yet.')
    for effect in event['effects']:
        if not effect.get('steps'):
            issues.append('%s uses custom behaviour that cannot be installed from this page yet.' % effect['name'])
        elif not generator.built_effect_script(event, effect):
            issues.append('%s needs its lights, speakers and sound selected before it can be used.' % effect['name'])
        elif sum(step.get('seconds', 0) for step in effect['steps']) * effect['repeat'] >= 90:
            issues.append('%s may run longer than the current 90-second effect safety timeout.' % effect['name'])
    return {'ready': False, 'issues': issues, 'message': 'Use Review and apply to check the current Home Assistant items.'}
