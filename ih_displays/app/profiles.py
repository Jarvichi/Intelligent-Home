"""Private display capabilities, with optimistic concurrency for edits."""
import json
import re
import sqlite3
import uuid

KINDS = {'eink', 'wall', 'other'}
COLOURS = {'monochrome', 'grayscale', 'colour'}
POWER = {'battery', 'mains', 'both'}
TRANSPORTS = {'image', 'web', 'data'}
FIELDS = {'name', 'kind', 'width', 'height', 'colour', 'touch', 'power', 'transport', 'refresh_seconds'}
ID = re.compile(r'^[a-f0-9]{32}$')

class Invalid(ValueError): pass
class Conflict(ValueError): pass
class Missing(ValueError): pass

def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, data TEXT NOT NULL)')
    db.commit()
    return db

def validate(doc):
    if not isinstance(doc, dict) or set(doc) != FIELDS: raise Invalid('Check the profile fields.')
    name = doc['name']
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80 or any(ord(c) < 32 for c in name): raise Invalid('Enter a display name.')
    if any(not isinstance(doc[k], str) or doc[k] not in allowed for k, allowed in (('kind', KINDS), ('colour', COLOURS), ('power', POWER), ('transport', TRANSPORTS))): raise Invalid('Check the display options.')
    if type(doc['touch']) is not bool: raise Invalid('Check touch support.')
    for key, low, high in (('width',1,8192), ('height',1,8192), ('refresh_seconds',5,86400)):
        if type(doc[key]) is not int or not low <= doc[key] <= high: raise Invalid('Check dimensions and refresh interval.')
    return dict(doc, name=name.strip())

def listing(db):
    return [dict(json.loads(data), id=ident, revision=revision) for ident, revision, data in db.execute('SELECT id,revision,data FROM profiles ORDER BY id')]

def get(db, ident):
    if not isinstance(ident,str) or not ID.fullmatch(ident): raise Missing()
    row=db.execute('SELECT revision,data FROM profiles WHERE id=?',(ident,)).fetchone()
    if not row: raise Missing()
    return dict(json.loads(row[1]),id=ident,revision=row[0])

def create(db, doc):
    doc=validate(doc); ident=uuid.uuid4().hex
    with db: db.execute('INSERT INTO profiles VALUES (?,1,?)',(ident,json.dumps(doc,ensure_ascii=False)))
    return get(db,ident)

def update(db, ident, doc, revision):
    doc=validate(doc)
    if not isinstance(ident,str) or not ID.fullmatch(ident) or type(revision) is not int or revision<1: raise Invalid('Invalid revision.')
    with db:
        result=db.execute('UPDATE profiles SET data=?,revision=revision+1 WHERE id=? AND revision=?',(json.dumps(doc,ensure_ascii=False),ident,revision))
        if result.rowcount!=1: raise Conflict('Profile changed. Reload before saving.')
    return get(db,ident)

def delete(db, ident, revision):
    if not isinstance(ident,str) or not ID.fullmatch(ident) or type(revision) is not int or revision<1: raise Invalid('Invalid revision.')
    with db:
        result=db.execute('DELETE FROM profiles WHERE id=? AND revision=?',(ident,revision))
        if result.rowcount!=1: raise Conflict('Profile changed. Reload before removing.')
