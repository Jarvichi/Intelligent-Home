"""Private store layouts, including a conflict-safe legacy import.

This module does not touch the live shopping list. Household layout values are
read from the owner's migration input and are never bundled with the add-on.
"""
import hashlib
import json
import re
import sqlite3

MAX_BYTES = 512 * 1024


class LayoutError(ValueError):
    pass


class LayoutConflict(LayoutError):
    pass


def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS layout_document (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL, revision TEXT NOT NULL)')
    db.commit()
    return db


def revision(doc):
    encoded = json.dumps(doc, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    if len(encoded) > MAX_BYTES:
        raise LayoutError('layout data too large')
    return hashlib.sha256(encoded).hexdigest()


def validate(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get('stores'), dict) or not isinstance(doc.get('aisles'), dict):
        raise LayoutError('store and aisle documents required')
    stores, aisles = doc['stores'], doc['aisles']
    entries = stores.get('stores')
    if not isinstance(entries, list) or not entries or len(entries) > 100:
        raise LayoutError('one or more stores required')
    ids = []
    for entry in entries:
        if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) and entry[k].strip() for k in ('id', 'name')):
            raise LayoutError('store id and name required')
        if len(entry['id']) > 100 or len(entry['name']) > 160:
            raise LayoutError('store id or name too long')
        if not isinstance(entry.get('order', []), list) or not all(isinstance(x, str) and x for x in entry.get('order', [])):
            raise LayoutError('invalid department order')
        if 'rank' in entry and not isinstance(entry['rank'], dict):
            raise LayoutError('invalid store ranks')
        ids.append(entry['id'])
    if len(set(ids)) != len(ids) or stores.get('default') not in ids:
        raise LayoutError('duplicate store id or invalid default')
    if not isinstance(aisles.get('order'), list) or not all(isinstance(x, str) and x for x in aisles['order']):
        raise LayoutError('invalid aisle order')
    if not isinstance(aisles.get('rank', {}), dict) or not isinstance(aisles.get('match', []), list):
        raise LayoutError('invalid aisle rules')
    for item in aisles.get('match', []):
        if not isinstance(item, list) or len(item) != 2 or not all(isinstance(v, str) for v in item):
            raise LayoutError('invalid aisle rule')
        try:
            re.compile(item[1])
        except re.error:
            raise LayoutError('invalid aisle rule') from None
    revision(doc)
    return doc


def read(db):
    row = db.execute('SELECT data, revision FROM layout_document WHERE id=1').fetchone()
    return (json.loads(row[0]), row[1]) if row else (None, None)


def save(db, doc, expected=None):
    """Create once, or update only the exact revision the caller reviewed."""
    validate(doc)
    encoded = json.dumps(doc, ensure_ascii=False, separators=(',', ':'))
    rev = revision(doc)
    try:
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT revision FROM layout_document WHERE id=1').fetchone()
        if (current[0] if current else None) != expected:
            raise LayoutConflict('layout changed; reload before saving')
        db.execute('INSERT INTO layout_document VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data, revision=excluded.revision', (encoded, rev))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return rev
