#!/usr/bin/env python3
"""Dry run: generate an event's files and compare them with what is live in a Home Assistant config folder (read-only).
usage: python3 tools/compare_live.py halloween /homeassistant
Compares by the Home Assistant ids each automation mentions (no YAML library needed). Prints what only the live file has and what only the generated file has."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'app'))
import generator as G
import model as M
import seed as SEED

ID_RE = re.compile(r'\b(?:input_boolean|input_text|input_select|light|switch|media_player|automation|script|scene)\.[a-z0-9_]+')


def block(text, auto_id):
    """The text of one automation in an automations.yaml (from '- id: <auto_id>' to the next '- id:')."""
    m = re.search(r'^- id: %s\s*$' % re.escape(auto_id), text, re.M)
    if not m:
        return ''
    n = re.search(r'^- id: ', text[m.end():], re.M)
    return text[m.start(): m.end() + n.start()] if n else text[m.start():]


def main(slug, config_dir):
    ev = M.clean_event({'halloween': SEED.HALLOWEEN, 'nye': SEED.NYE}[slug])
    files = G.generate(ev)
    live = open(os.path.join(config_dir, 'automations.yaml')).read()
    for auto_id in (slug + '_mode_switch', slug + '_safety'):
        generated = block(files['automations.yaml'].replace('\n- id:', '\n- id:'), auto_id)
        want, have = set(ID_RE.findall(block(live, auto_id))), set(ID_RE.findall(generated))
        print('== %s' % auto_id)
        print('   live only     :', ', '.join(sorted(want - have)) or '(none)')
        print('   generated only:', ', '.join(sorted(have - want)) or '(none)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2]) if len(sys.argv) == 3 else print(__doc__) or 1)
