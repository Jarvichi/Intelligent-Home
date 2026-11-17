"""Reminder delivery with a durable, per-recipient uncertain state."""
import json
import urllib.parse
import urllib.request
import store


def send(url, token, persona, text):
    request = urllib.request.Request(url.rstrip('/') + '/persona/' + urllib.parse.quote(persona, safe='') + '/reply',
        data=json.dumps({'text': text}).encode(), method='POST',
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.load(response)
    return result.get('ok') is True and result.get('sent') is True and result.get('dry') is False


def run(db, today, personas, sender):
    contracts = {c['id']: c for c in store.records(db, today)}
    for reminder in store.due(db, today):
        c = contracts[reminder['id']]
        recipients = c.get('who', [])
        if not isinstance(recipients, list) or not recipients or any(not isinstance(w, str) or not personas.get(w) for w in recipients):
            continue
        for recipient in dict.fromkeys(recipients):
            # Commit before the network call. An interruption or ambiguous response
            # requires review, rather than silently sending the same DM twice.
            with db:
                cursor = db.execute('INSERT OR IGNORE INTO deliveries VALUES (?, ?, ?)',
                                    (reminder['key'], recipient, 'uncertain'))
            if cursor.rowcount == 0:
                continue
            months = reminder['months']
            action = 'time to organise a new car' if c['kind'] == 'car lease' else 'check renewal or switch'
            message = 'Contract reminder: %s ends %s (%s month%s to go) - %s.' % (
                c['name'], c['end'], months, 's' if months > 1 else '', action)
            try:
                delivered = sender(personas[recipient], message)
            except Exception:
                delivered = False
            if delivered:
                with db:
                    db.execute("UPDATE deliveries SET status='sent' WHERE key=? AND recipient=?", (reminder['key'], recipient))
        statuses = dict(db.execute('SELECT recipient, status FROM deliveries WHERE key=?', (reminder['key'],)))
        if all(statuses.get(w) == 'sent' for w in recipients):
            with db:
                db.execute('INSERT OR IGNORE INTO sent VALUES (?, ?)', (reminder['key'], today.isoformat()))
