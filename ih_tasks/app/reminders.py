"""Reminder pass: tasks closed as 'Reminder set' come back to To do on their date, and the Claude inbox is told so Claude can DM the owner.
No clock of its own: `today`, `hour` and the opener come in, so tests can fake them."""
import datetime
import json
import urllib.request

LATE_DAYS = 1   # if the inbox cannot be reached until this many days after the date, reopen the task anyway (it is never lost)


def text_for(r):
    return ('Reminder you set for %s on IH task #%d: %s.%s The task was reopened in To do (high priority). Tell the owner it is a reminder THEY set on the IH Tasks board%s, then offer to restart it.'
            % (r['remind_at'], r['id'], r['title'][:150], (' Your note: ' + r['note'][:300] + '.') if r['note'] else '', (' on ' + r['set_at'][:10]) if r['set_at'] else ''))


def notify(opts, r, opener=urllib.request.urlopen):
    """POST one 'task_reminder' event to the Claude inbox (the doorbell rings it into the Claude session). True when stored. Not configured -> True (nothing to send)."""
    if not opts.get('inbox_url') or not opts.get('inbox_token'):
        return True
    body = json.dumps({'kind': 'task_reminder', 'source': 'ih_tasks', 'ref': 'task-%d-%s' % (r['id'], r['remind_at']), 'text': text_for(r)}).encode()
    req = urllib.request.Request(opts['inbox_url'].rstrip('/') + '/events', data=body, method='POST',
                                 headers={'Authorization': 'Bearer ' + opts['inbox_token'], 'Content-Type': 'application/json'})
    try:
        with opener(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def run(store, opts, hour, today=None, log=print, opener=urllib.request.urlopen):
    """-> number of tasks reopened. Before the reminder hour nothing happens (so it never fires at 00:05)."""
    if hour < opts.get('reminder_hour', 8):
        return 0
    if opts.get('inbox_url') and opts.get('inbox_token'):
        for notification_id, reminder in store.pending_reminder_notifications():
            if not notify(opts, reminder, opener):
                log('queued reminder for task %d: inbox unavailable, will try again' % reminder['id'])
                break
            store.remove_reminder_notification(notification_id)
            log('queued reminder delivered for task %d' % reminder['id'])
    n = 0
    for r in store.due_reminders(today):
        late_days = (store.today() - datetime.date.fromisoformat(r['remind_at'])).days
        delivered = notify(opts, r, opener)
        if not delivered and late_days < LATE_DAYS:
            log('reminder for task %d: the Claude inbox could not be reached, will try again' % r['id'])
            continue
        store.fire_reminder(r['id'], queue_notification=not delivered); n += 1
        log('reminder fired for task %d' % r['id'])
    return n
