"""ih_themed_events generator: turns one event record into the standard Home Assistant pieces, as YAML text for review.
It NEVER writes to Home Assistant or to its config folder. The owner reads the text, then pastes or includes it (a later, separate approval).
stdlib only: a small YAML writer is built in (strings are double-quoted, so templates stay safe)."""
import re

_BARE = re.compile(r'^[A-Za-z_][A-Za-z0-9_./-]*$')
_WORDS = {'true', 'false', 'yes', 'no', 'on', 'off', 'null', 'none', '~', 'y', 'n'}


def _scalar(v):
    if v is True or v is False:
        return 'true' if v else 'false'
    if v is None:
        return 'null'
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if _BARE.match(s) and s.lower() not in _WORDS:
        return s
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def dump(obj, indent=0):
    """YAML text for nested dict / list / scalars (block style, dict order kept)."""
    pad = ' ' * indent
    lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                lines.append('%s%s:' % (pad, k))
                lines.append(dump(v, indent + (0 if isinstance(v, list) else 2)))
            elif isinstance(v, (dict, list)):
                lines.append('%s%s: %s' % (pad, k, '{}' if isinstance(v, dict) else '[]'))
            else:
                lines.append('%s%s: %s' % (pad, k, _scalar(v)))
    else:
        for item in obj:
            if isinstance(item, dict) and item:
                inner = dump(item, indent + 2).split('\n')
                lines.append(pad + '- ' + inner[0].lstrip())
                lines.extend(inner[1:])
            elif isinstance(item, (dict, list)):
                lines.append('%s- %s' % (pad, '{}' if isinstance(item, dict) else '[]'))
            else:
                lines.append('%s- %s' % (pad, _scalar(item)))
    return '\n'.join(lines)


def ids(ev):
    """Every Home Assistant id the event owns. One naming rule: <slug>_<part>."""
    s = ev['slug']
    return {'mode': 'input_boolean.%s_mode' % s, 'music': 'input_boolean.%s_music' % s, 'busy': 'input_boolean.%s_busy' % s,
            'random': 'input_boolean.%s_random_effects' % s, 'speaker_pick': 'input_select.%s_effect_speaker' % s,
            'lights_off': 'input_text.%s_lights_off' % s, 'speakers_playing': 'input_text.%s_speakers_playing' % s,
            'paused': 'input_text.%s_paused_automations' % s, 'scene': 'scene.%s_before' % s,
            'look': 'script.%s_look' % s, 'effect_sound': 'script.%s_effect_sound' % s}


def helpers(ev):
    """{'input_boolean': {...}, 'input_text': {...}, 'input_select': {...}} for configuration.yaml (or a package file)."""
    s, n, icon = ev['slug'], ev['name'], ev['icon']
    booleans = {s + '_mode': {'name': n + ' mode', 'icon': icon}, s + '_music': {'name': n + ' music', 'icon': 'mdi:music'},
                s + '_busy': {'name': n + ' effect running', 'icon': 'mdi:timer-sand'}}
    texts = {s + '_lights_off': {'name': n + ' lights off before', 'max': 255}, s + '_speakers_playing': {'name': n + ' speakers playing before', 'max': 100},
             s + '_paused_automations': {'name': n + ' paused automations', 'max': 255}}
    out = {'input_boolean': booleans, 'input_text': texts}
    if any(not e['at'] for e in ev['effects']):
        booleans[s + '_random_effects'] = {'name': n + ' random effects', 'icon': 'mdi:dice-multiple'}
        options = ['Random'] + [_speaker_name(m) for m in ev['speakers'] if not m.endswith('kitchen_2')] + ['Off']
        out['input_select'] = {s + '_effect_speaker': {'name': n + ' effect speaker', 'options': options, 'initial': 'Random'}}
    return out


def _speaker_name(entity):
    return entity.split('.', 1)[1].replace('_', ' ').title()


def _has_look(ev):
    return bool(ev['look'] and (ev['light_group'] or ev['lights']))


def _snapshot_entities(ev):
    # A group can be the only configured light target; include it so mode-off
    # restores its prior state instead of leaving the event look active.
    return list(dict.fromkeys((ev['lights'] or ([ev['light_group']] if ev['light_group'] else [])) + ev['extra_switches']))


def mode_switch(ev):
    """The mode on/off automation: ON snapshots lights, remembers speakers, pauses chosen automations, starts music and the look.
    OFF stops the scripts, restores the lights from the snapshot, pauses speakers that were not playing before, and turns paused automations back on."""
    i, s = ids(ev), ev['slug']
    lights, speakers = ev['lights'], ev['speakers']
    ent = lambda e: {'entity_id': e}
    on = [{'action': 'scene.create', 'data': {'scene_id': s + '_before', 'snapshot_entities': _snapshot_entities(ev)}}] if _snapshot_entities(ev) else []
    if lights:
        on.append({'action': 'input_text.set_value', 'target': ent(i['lights_off']),
                   'data': {'value': "{{ %s | reject('is_state', 'on') | map('replace', 'light.', '') | list | join(',') }}" % _py(lights)}})
    if speakers:
        on.append({'action': 'input_text.set_value', 'target': ent(i['speakers_playing']),
                   'data': {'value': "{{ %s | select('is_state', 'playing') | map('replace', 'media_player.', '') | list | join(',') }}" % _py(speakers)}})
    on.append({'variables': {'was_on': "{{ %s | select('is_state', 'on') | list }}" % _py(ev['pause_automations'])}})
    on.append({'action': 'input_text.set_value', 'target': ent(i['paused']), 'data': {'value': "{{ was_on | join(',') }}"}})
    on.append({'if': [{'condition': 'template', 'value_template': '{{ was_on | length > 0 }}'}],
               'then': [{'action': 'automation.turn_off', 'target': ent('{{ was_on }}'), 'data': {'stop_actions': False}}]})
    on.append({'action': 'input_boolean.turn_off', 'target': ent(i['busy'])})
    on.append({'action': 'input_boolean.turn_on', 'target': ent(i['music'])})
    if _has_look(ev):
        on.append({'action': 'script.turn_on', 'target': ent(i['look'])})
    stop = [x for x, made in ((i['look'], look_script(ev)), (i['effect_sound'], effect_sound_script(ev))) if made] + ['script.%s_%s' % (s, e['id']) for e in ev['effects']]
    off = ([{'action': 'script.turn_off', 'target': ent(stop)}] if stop else []) + [
           {'action': 'input_boolean.turn_off', 'target': ent([i['music'], i['busy']])}, {'delay': 1}]
    off_targets = [ev['light_group']] if ev['light_group'] else lights
    if _snapshot_entities(ev):
        restore = {'if': [{'condition': 'template', 'value_template': "{{ states.scene | selectattr('entity_id', 'eq', '%s') | list | count > 0 }}" % i['scene']}],
                   'then': [{'action': 'scene.turn_on', 'target': ent(i['scene']), 'data': {'transition': 2}}]}
        if off_targets:
            restore['else'] = [{'action': 'light.turn_off', 'target': ent(off_targets)}]
        off.append(restore)
    if speakers:
        off.append({'variables': {'to_pause': "{{ %s | reject('in', states('%s').split(',')) | map('regex_replace', '^', 'media_player.') | select('is_state', 'playing') | list }}"
                                  % (_py([m.split('.', 1)[1] for m in speakers]), i['speakers_playing'])}})
        off.append({'if': [{'condition': 'template', 'value_template': '{{ to_pause | length > 0 }}'}],
                    'then': [{'action': 'media_player.media_pause', 'continue_on_error': True, 'target': ent('{{ to_pause }}')}]})
    off.append({'variables': {'paused': "{{ states('%s').split(',') | select('match', 'automation\\.') | list }}" % i['paused']}})
    off.append({'if': [{'condition': 'template', 'value_template': '{{ paused | length > 0 }}'}],
                'then': [{'action': 'automation.turn_on', 'target': ent('{{ paused }}')}]})
    off.append({'action': 'input_text.set_value', 'target': ent(i['paused']), 'data': {'value': ''}})
    return {'id': s + '_mode_switch', 'alias': '%s - mode on/off' % ev['name'],
            'description': 'Generated by ih_themed_events. ON snapshots the lights, pauses the listed automations and starts the look and music. OFF restores everything.',
            'mode': 'restart',
            'triggers': [{'trigger': 'state', 'entity_id': i['mode'], 'to': 'on', 'id': 'on'}, {'trigger': 'state', 'entity_id': i['mode'], 'to': 'off', 'id': 'off'}],
            'conditions': [],
            'actions': [{'choose': [{'conditions': [{'condition': 'trigger', 'id': 'on'}], 'sequence': on},
                                    {'conditions': [{'condition': 'trigger', 'id': 'off'}], 'sequence': off}]}]}


def _py(items):
    return '[' + ','.join("'%s'" % x for x in items) + ']'


def safety(ev):
    """Auto-off at the event's auto_off time; clear a stuck effect after 90 s; turn the look back on after a Home Assistant restart."""
    i, s = ids(ev), ev['slug']
    ent = lambda e: {'entity_id': e}
    triggers = []
    if ev['auto_off']:
        triggers.append({'trigger': 'time', 'at': ev['auto_off'] + ':00', 'id': 'auto_off'})
    triggers += [{'trigger': 'homeassistant', 'event': 'start', 'id': 'restart'},
                 {'trigger': 'state', 'entity_id': i['busy'], 'to': 'on', 'for': '00:01:30', 'id': 'stuck'}]
    restart = [{'action': 'input_boolean.turn_off', 'target': ent(i['busy'])}]
    stuck = [{'action': 'input_boolean.turn_off', 'target': ent(i['busy'])}]
    if _has_look(ev):
        restart.append({'action': 'script.turn_on', 'target': ent(i['look'])})
        stuck.append({'action': 'script.turn_on', 'target': ent(i['look'])})
    choose = []
    if ev['auto_off']:
        choose.append({'conditions': [{'condition': 'trigger', 'id': 'auto_off'}], 'sequence': [{'action': 'input_boolean.turn_off', 'target': ent(i['mode'])}]})
    choose += [{'conditions': [{'condition': 'trigger', 'id': 'restart'}], 'sequence': restart}, {'conditions': [{'condition': 'trigger', 'id': 'stuck'}], 'sequence': stuck}]
    return {'id': s + '_safety', 'alias': '%s - safety (auto-off, restart, stuck effect)' % ev['name'],
            'description': 'Generated by ih_themed_events. Mode switches itself off at %s; a stuck effect is cleared after 90 s.' % (ev['auto_off'] or 'never'),
            'mode': 'queued', 'triggers': triggers,
            'conditions': [{'condition': 'state', 'entity_id': i['mode'], 'state': 'on'}], 'actions': [{'choose': choose}]}


def random_effects(ev):
    """Choose one eligible effect, respecting each effect's timing and time window."""
    effects = [e for e in ev['effects'] if not e['at']]       # an effect with an `at` time is not random (see timed_effects)
    if not effects:
        return None
    i, s = ids(ev), ev['slug']
    lines = ["{% set ns = namespace(eligible=[], due=[]) %}", "{% set clock = now().strftime('%H:%M') %}"]
    for effect in effects:
        entity = 'script.%s_%s' % (s, effect['id'])
        start, end = effect['from'], effect['to']
        if start and end:
            window = "(clock >= '%s' or clock < '%s')" % (start, end) if start > end else "(clock >= '%s' and clock < '%s')" % (start, end)
        elif start:
            window = "clock >= '%s'" % start
        elif end:
            window = "clock < '%s'" % end
        else:
            window = 'true'
        lines.extend(["{%% set last = as_timestamp(state_attr('%s', 'last_triggered'), as_timestamp(states['%s'].last_changed)) %%}" % (entity, i['mode']),
                      "{% set age = (as_timestamp(now()) - last) / 60 %}",
                      "{%% if %s and age >= %d %%}" % (window, effect['min_gap_minutes']),
                      "{%% set ns.eligible = ns.eligible + ['%s'] %%}" % entity,
                      "{%% if age >= %d %%}{%% set ns.due = ns.due + ['%s'] %%}{%% endif %%}" % (effect['max_gap_minutes'], entity),
                      '{% endif %}'])
    lines.append("{{ ns.due | random if ns.due else (ns.eligible | random if ns.eligible and range(4) | random == 0 else '') }}")
    return {'id': s + '_random_effects', 'alias': '%s - random effects' % ev['name'],
            'description': 'Generated by ih_themed_events. Every minute, select an eligible effect using its own interval and time window.',
            'mode': 'single', 'triggers': [{'trigger': 'time_pattern', 'minutes': '/1'}],
            'conditions': [{'condition': 'state', 'entity_id': i['mode'], 'state': 'on'}, {'condition': 'state', 'entity_id': i['random'], 'state': 'on'},
                           {'condition': 'state', 'entity_id': i['busy'], 'state': 'off'}],
            'actions': [{'variables': {'chosen_effect': '\n'.join(lines)}},
                        {'if': [{'condition': 'template', 'value_template': "{{ chosen_effect | trim != '' }}"}],
                         'then': [{'action': 'script.turn_on', 'target': {'entity_id': '{{ chosen_effect | trim }}'}}]}]}


def timed_effects(ev):
    """One automation per effect that has an `at` time: it calls the hand-written effect script at that time while the mode is on."""
    i, s = ids(ev), ev['slug']
    return [{'id': '%s_%s_at' % (s, e['id']), 'alias': '%s - %s at %s' % (ev['name'], e['name'], e['at']),
             'description': 'Generated by ih_themed_events. Calls script.%s_%s at %s while the mode is on.' % (s, e['id'], e['at']),
             'mode': 'single', 'triggers': [{'trigger': 'time', 'at': e['at'] + ':00'}],
             'conditions': [{'condition': 'state', 'entity_id': i['mode'], 'state': 'on'}] +
                           ([dict({'condition': 'time'}, **({'after': e['from'] + ':00'} if e['from'] else {}), **({'before': e['to'] + ':00'} if e['to'] else {}))] if e['from'] or e['to'] else []),
             'actions': [{'action': 'script.turn_on', 'target': {'entity_id': 'script.%s_%s' % (s, e['id'])}}]} for e in ev['effects'] if e['at']]


def look_script(ev):
    if not _has_look(ev):
        return None
    data = {k: v for k, v in (('rgb_color', ev['look'].get('rgb')), ('brightness', ev['look'].get('brightness')), ('transition', ev['look'].get('transition'))) if v is not None}
    return {'alias': '%s - look' % ev['name'], 'icon': ev['icon'], 'mode': 'restart',
            'sequence': [{'action': 'light.turn_on', 'target': {'entity_id': ev['light_group'] or ev['lights']}, 'data': data}]}


def effect_sound_script(ev):
    """Plays a file from the event's sounds folder on a speaker. `sound` and `speaker` are script fields."""
    if not ev['effects'] or not ev['speakers']:
        return None
    i = ids(ev)
    return {'alias': '%s - effect sound' % ev['name'], 'icon': 'mdi:speaker', 'mode': 'queued',
            'fields': {'sound': {'description': 'file name inside ' + ev['sounds_folder'], 'example': 'scream.mp3'},
                       'speaker': {'description': 'media player entity id', 'example': ev['speakers'][0]}},
            'sequence': [{'action': 'input_boolean.turn_on', 'target': {'entity_id': i['busy']}},
                         {'action': 'media_player.play_media', 'target': {'entity_id': '{{ speaker }}'},
                          'data': {'media_content_type': 'music', 'media_content_id': 'media-source://media_source' + ev['sounds_folder'][len('/media'):] + '/{{ sound }}'}},
                         {'delay': 10},
                         {'action': 'input_boolean.turn_off', 'target': {'entity_id': i['busy']}}]}


def built_effect_script(ev, effect):
    """Compile explicitly built steps; legacy effects remain untouched."""
    if not effect.get('steps'):
        return None
    defaults = ev['lights'] or ([ev['light_group']] if ev['light_group'] else [])
    targets = list(dict.fromkeys(t for s in effect['steps'] if s['type'] == 'light' for t in (s.get('targets') or defaults)))
    needs_lights = any(s['type'] == 'light' for s in effect['steps'])
    needs_sound = any(s['type'] == 'sound' for s in effect['steps'])
    if any(s['type'] == 'light' and not (s.get('targets') or defaults) for s in effect['steps']) or (needs_sound and (not ev['speakers'] or not effect['sound'])):
        return None
    scene = ev['slug'] + '_' + effect['id'] + '_before'
    seq = [{'action': 'input_boolean.turn_on', 'target': {'entity_id': ids(ev)['busy']}}]
    if needs_lights and effect.get('restore', True):
        seq.append({'action': 'scene.create', 'data': {'scene_id': scene, 'snapshot_entities': targets}})
    actions = []
    for step in effect['steps']:
        if step['type'] == 'light':
            step_targets = step.get('targets') or defaults
            if step['brightness'] == 0:
                actions.append({'action': 'light.turn_off', 'target': {'entity_id': step_targets}})
            else:
                colour = step['colour']
                actions.append({'action': 'light.turn_on', 'target': {'entity_id': step_targets}, 'data': {'rgb_color': [int(colour[n:n+2], 16) for n in (1, 3, 5)], 'brightness_pct': step['brightness']}})
            actions.append({'delay': step['seconds']})
        elif step['type'] == 'wait':
            actions.append({'delay': step['seconds']})
        else:
            sound = effect['sound']
            if not sound.startswith('media-source://'):
                sound = 'media-source://media_source' + ev['sounds_folder'][len('/media'):] + '/' + sound
            actions.append({'action': 'media_player.play_media', 'target': {'entity_id': ev['speakers']}, 'data': {'media_content_type': 'music', 'media_content_id': sound}})
    seq.append({'repeat': {'count': effect.get('repeat', 1), 'sequence': actions}})
    if needs_lights and effect.get('restore', True):
        seq.append({'action': 'scene.turn_on', 'target': {'entity_id': 'scene.' + scene}})
    seq.append({'action': 'input_boolean.turn_off', 'target': {'entity_id': ids(ev)['busy']}})
    return {'alias': ev['name'] + ' - ' + effect['name'], 'mode': 'single', 'sequence': seq}


def warnings(ev):
    """Things the owner must still fill in or write by hand before the generated files are useful."""
    out = []
    if not (ev['light_group'] or ev['lights']):
        out.append('No lights chosen: lights are not snapshotted or restored and there is no look script.')
    if not ev['speakers']:
        out.append('No speakers chosen: no effect sound script and no speaker restore.')
    if not ev['pause_automations']:
        out.append('No automations to pause: motion lights may fight the party lights.')
    if any(e['sound'] and not e['sound'].startswith('media-source://') for e in ev['effects']) and not ev['sounds_folder']:
        out.append('Effects need a sounds folder.')
    for e in ev['effects']:
        if not e.get('steps'):
            out.append('Write script.%s_%s by hand (the generator only calls it).' % (ev['slug'], e['id']))
        elif not built_effect_script(ev, e):
            out.append('Choose the required lights, speakers and sound for %s.' % e['name'])
    return out


def generate(ev):
    """{'helpers.yaml', 'automations.yaml', 'scripts.yaml', 'README.txt'} text for one event."""
    s = ev['slug']
    autos = [a for a in (mode_switch(ev), safety(ev), random_effects(ev)) if a] + timed_effects(ev)
    scripts = {s + '_look': look_script(ev), s + '_effect_sound': effect_sound_script(ev)}
    scripts.update({s + '_' + e['id']: built_effect_script(ev, e) for e in ev['effects'] if e.get('steps')})
    scripts = {k: v for k, v in scripts.items() if v}
    head = '# Generated by ih_themed_events for the event "%s". Review before use. Nothing here is installed by the add-on.\n' % ev['name']
    return {'helpers.yaml': head + dump(helpers(ev)) + '\n', 'automations.yaml': head + dump(autos) + '\n',
            'scripts.yaml': head + (dump(scripts) + '\n' if scripts else '# (no scripts for this event)\n'),
            'README.txt': ('To do first:\n' + ''.join('- %s\n' % w for w in warnings(ev)) + '\nEvent %s (%s)\n1. Add helpers.yaml to configuration.yaml (or a package) and reload helpers.\n'
                           '2. Add automations.yaml and scripts.yaml, run `ha core check`, reload automations and scripts.\n'
                           '3. Effects without steps need their existing hand-written scripts.\n'
                           '4. Rollback: remove the three pieces and reload. Generated again from the same record, the files are identical.\n') % (ev['name'], s)}
