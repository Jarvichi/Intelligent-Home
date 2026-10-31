"""brain_stats: every hour, count the brain (notes, words, links, orphans, areas, density) and time a fixed set of brain searches, then publish the result as a
sensor (port of the host brain_stats.py script that fed a command_line sensor). Read-only on the brain folder.
Counting rules follow the host script: inbox/, templates/, attachments/, hidden folders and _log.md are not notes; a link is a [[wikilink]] that resolves to a
note (by path, file name or title). density = links per note; orphans = notes nothing links to (README and INDEX excluded); search_ms = median ms of a fixed
set of searches against the live search index (read-only).
Every house value comes from settings (JSON in plugins[].settings); defaults in brackets:
  folder           brain folder (/share/brain; needs the share map, which ih_jobs has)
  index_db         search index file (<folder>/.index.sqlite)
  bench            list of FTS queries timed for search_ms (five general words; pass the old queries to keep the history comparable)
  publish_entity   entity written through the HA state API, live only (sensor.brain_notes; empty = do not publish)
  minutes          run every N minutes (60)
The state is the note count; attributes: words, links, orphans, areas, density, search_ms (omitted when the index is missing) + unit_of_measurement notes,
state_class measurement, icon, friendly_name. Template sensors 'Brain words / links ...' read these attributes unchanged.
Dry: computes and logs the numbers; publishes nothing. No state is kept.
SWAP: remove the old command_line sensor with the same entity id (command_line reload), then set the plugin live."""
import os
import re
import sqlite3
import statistics
import time

NAME = 'brain_stats'
SCHEDULE = {'kind': 'every', 'minutes': 60}
BENCH = ['"backup"*', '"battery"*', '"calendar"* OR "school"*', '"heating"* OR "boiler"*', '"wifi"*']
DEFAULTS = {'folder': '/share/brain', 'index_db': '', 'bench': BENCH, 'publish_entity': 'sensor.brain_notes', 'minutes': 60}

W = re.compile(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]')


def stats(base):
    notes = {}
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for fn in files:
            if not fn.endswith('.md'):
                continue
            rel = os.path.relpath(os.path.join(root, fn), base).replace(os.sep, '/')
            if rel.startswith(('.', 'attachments/', 'inbox/', 'templates/')) or '/.' in rel or rel == '_log.md':
                continue
            with open(os.path.join(base, rel), encoding='utf-8', errors='replace') as f:
                notes[rel] = f.read()
    keys = {}
    for rel, t in notes.items():
        keys[rel[:-3].lower()] = rel
        keys.setdefault(rel[:-3].split('/')[-1].lower(), rel)
        m = re.search(r'^title:\s*(.+)$', t, re.M)
        if m:
            keys.setdefault(m.group(1).strip().strip('"').lower(), rel)
    inbound = dict.fromkeys(notes, 0)
    links = words = 0
    for rel, t in notes.items():
        body = t.split('\n---', 1)[1] if t.startswith('---\n') and '\n---' in t[4:] else t
        words += len(re.findall(r'\w+', body))
        plain = re.sub(r'`[^`\n]*`', '', re.sub(r'```.*?```', '', t, flags=re.S))
        for w in W.findall(plain):
            tgt = keys.get(w.strip().lower().removesuffix('.md'))
            if tgt and tgt != rel:
                links += 1
                inbound[tgt] += 1
    orphans = sum(1 for r, n in inbound.items() if n == 0 and r not in ('README.md', 'INDEX.md'))
    areas = len({r.split('/')[0] for r in notes if '/' in r})
    n = len(notes)
    return {'notes': n, 'words': words, 'links': links, 'orphans': orphans, 'areas': areas, 'density': round(links / n, 2) if n else 0}


def search_ms(db, bench, clock=time.perf_counter):
    """Median ms of the bench searches (20 rounds), or None when the index cannot be read."""
    try:
        con = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
        runs = []
        for _ in range(20):
            for q in bench:
                t = clock()
                con.execute("select path, title, snippet(notes, 2, '[', ']', '…', 14) from notes where notes match ? "
                            "order by bm25(notes, 5.0, 10.0, 1.0) limit 60", (q,)).fetchall()
                runs.append((clock() - t) * 1000)
        con.close()
        return round(statistics.median(runs), 2)
    except Exception:
        return None


def run(ctx):
    s = ctx.settings
    folder = s['folder']
    if not os.path.isdir(folder):
        ctx.log('brain folder not found, skipped')
        return False
    p = stats(folder)
    ms = search_ms(s['index_db'] or os.path.join(folder, '.index.sqlite'), s['bench'])
    if ms is not None:
        p['search_ms'] = ms
    if ctx.dry:
        ctx.log('[DRY] brain: %s; would publish to %s (nothing published)' % (', '.join('%s=%s' % kv for kv in p.items()), s['publish_entity'] or '(no entity set)'))
        return True
    ctx.log('brain: %d notes, %d links, %d orphans' % (p['notes'], p['links'], p['orphans']))
    if s['publish_entity']:
        attrs = {k: v for k, v in p.items() if k != 'notes'}
        attrs.update({'unit_of_measurement': 'notes', 'state_class': 'measurement', 'icon': 'mdi:brain', 'friendly_name': 'Brain notes'})
        ctx.ha.post('states/' + s['publish_entity'], {'state': str(p['notes']), 'attributes': attrs})
    return True
