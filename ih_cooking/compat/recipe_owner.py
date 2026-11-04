"""Legacy recipe adapter. The injected client owns authentication; this module never reads credentials.

IH is authoritative. Host and static-page directories are derived caches, with
snapshots before replacement. Failed canonical reads/writes never fall back locally.
"""
import copy
import json
import tempfile
import fcntl
import functools
import os
import pathlib
import re
import time

def cache_lock(fn):
    @functools.wraps(fn)
    def locked(self, *args, **kwargs):
        self.backups.mkdir(parents=True, exist_ok=True)
        with (self.backups / '.cache.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return fn(self, *args, **kwargs)
    return locked

SLUG = re.compile(r'[a-z0-9-]{1,60}\Z')

class RecipeOwner:
    def __init__(self, client, cache, www, backups):
        self.client = client
        self.cache, self.www, self.backups = map(pathlib.Path, (cache, www, backups))

    def listing(self):
        rows = self.client('GET', '/recipes')
        if isinstance(rows, dict): rows = rows.get('recipes')
        if not isinstance(rows, list): raise ValueError('Canonical recipe list unavailable')
        for row in rows:
            slug = row.get('slug') or row.get('id')
            if not isinstance(slug, str) or not SLUG.fullmatch(slug): raise ValueError('Invalid recipe identity')
        return rows

    def get(self, slug):
        if not isinstance(slug, str) or not SLUG.fullmatch(slug): raise ValueError('Invalid recipe identity')
        recipe = copy.deepcopy(self.client('GET', '/recipes/' + slug))
        if not isinstance(recipe, dict) or not re.fullmatch(r'[a-f0-9]{64}', recipe.get('_revision', '')):
            raise ValueError('Revision-aware canonical recipe API required')
        recipe.pop('slug', None)
        for index, step in enumerate(recipe.get('steps', [])):
            step['_original_index'], step['_original_text'] = index, step['text']
        return recipe

    @staticmethod
    def _atomic(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle: json.dump(value, handle, ensure_ascii=False, indent=1)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    @cache_lock
    def refresh(self):
        # One consistent canonical read before touching either local cache.
        raw = self.client('GET', '/recipe-snapshot')
        if not isinstance(raw, dict): raise ValueError('Canonical snapshot unavailable')
        recipes = {}
        for slug, value in raw.items():
            if not isinstance(slug, str) or not SLUG.fullmatch(slug) or not isinstance(value, dict) or not re.fullmatch(r'[a-f0-9]{64}', value.get('_revision', '')): raise ValueError('Invalid canonical snapshot')
            recipe = copy.deepcopy(value)
            recipe.pop('slug', None)
            for index, step in enumerate(recipe.get('steps', [])): step['_original_index'], step['_original_text'] = index, step['text']
            recipes[slug] = recipe
        snapshot = self.backups / ('canonical-cache-' + str(time.time_ns()))
        for slug, recipe in recipes.items():
            for label, folder in (('host', self.cache), ('www', self.www)):
                target = folder / (slug + '.json')
                if target.exists():
                    with target.open(encoding='utf-8') as handle: previous = json.load(handle)
                    if previous == recipe: continue
                    self._atomic(snapshot / label / (slug + '.json'), previous)
                self._atomic(target, recipe)
        self._atomic(self.www / 'index.json', [{'name': slug, 'title': r.get('title') or r.get('name') or slug} for slug, r in recipes.items()])
        return recipes

    def save(self, payload):
        if not isinstance(payload, dict): raise ValueError('Recipe object required')
        body = copy.deepcopy(payload)
        slug = body.pop('id', None)
        body.pop('_edit', None)
        existing = {r.get('slug') or r.get('id') for r in self.listing()}
        if slug in existing:
            if not isinstance(body.get('_revision'), str) or not re.fullmatch(r'[a-f0-9]{64}', body['_revision']):
                raise ValueError('Reload the recipe before saving')
            self.client('POST', '/recipes/' + slug, body)
            result = slug
        else:
            if slug is not None: body['id'] = slug
            reply = self.client('POST', '/recipes', body)
            result = reply.get('slug') or reply.get('created')
            if not isinstance(result, str) or not SLUG.fullmatch(result): raise ValueError('Create response missing recipe identity')
        # A cache refresh failure must be surfaced even though the canonical write succeeded.
        try: self.refresh()
        except Exception: raise RuntimeError('Recipe saved in Cooking, but legacy pages could not refresh. Reload before editing again.') from None
        return result
