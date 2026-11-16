"""Private contract store and deterministic projections."""
import calendar
import datetime as dt
import json
import sqlite3
from decimal import Decimal, InvalidOperation, ROUND_CEILING


def open_store(path):
    db = sqlite3.connect(path, check_same_thread=False)
    db.execute("CREATE TABLE IF NOT EXISTS contracts (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS sent (key TEXT PRIMARY KEY, sent_date TEXT NOT NULL)")
    db.commit()
    return db


def money(value):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or n != n.quantize(Decimal('.01')):
            raise ValueError('invalid amount')
        return n
    except (InvalidOperation, TypeError):
        raise ValueError('invalid amount') from None


def validate(c):
    if not isinstance(c, dict) or not all(isinstance(c.get(k), str) and c[k] for k in ('id', 'name', 'kind', 'start', 'end')):
        raise ValueError('invalid contract')
    start, end = dt.date.fromisoformat(c['start']), dt.date.fromisoformat(c['end'])
    if end <= start:
        raise ValueError('invalid dates')
    months = c.get('remind_months', [6, 3] if c['kind'] == 'car lease' else [1])
    if not isinstance(months, list) or any(type(m) is not int or not 1 <= m <= 120 for m in months):
        raise ValueError('invalid reminders')
    if 'monthly_payment' in c and money(c['monthly_payment']) <= 0:
        raise ValueError('invalid monthly payment')
    if 'balance_snapshot' in c:
        money(c['balance_snapshot']['amount'])
        dt.date.fromisoformat(c['balance_snapshot']['as_of'])
        if 'monthly_payment' not in c or type(c.get('payment_day')) is not int or not 1 <= c['payment_day'] <= 31:
            raise ValueError('balance needs payment and day')
    if 'settlement_quote' in c:
        money(c['settlement_quote']['amount'])
        dt.date.fromisoformat(c['settlement_quote']['as_of'])
    return c


def import_once(db, contracts, sent):
    if not isinstance(contracts, list) or not isinstance(sent, dict):
        raise ValueError('contracts and sent history required')
    for c in contracts:
        validate(c)
    for key, date in sent.items():
        if not isinstance(key, str):
            raise ValueError('invalid reminder key')
        dt.date.fromisoformat(date)
    with db:
        if db.execute('SELECT COUNT(*) FROM contracts').fetchone()[0] or db.execute('SELECT COUNT(*) FROM sent').fetchone()[0]:
            raise ValueError('store is not empty')
        db.executemany('INSERT INTO contracts VALUES (?, ?)', [(c['id'], json.dumps(c)) for c in contracts])
        db.executemany('INSERT INTO sent VALUES (?, ?)', list(sent.items()))


def save_contract(db, contract):
    validate(contract)
    with db:
        db.execute('INSERT INTO contracts VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                   (contract['id'], json.dumps(contract)))


def project(c, today):
    validate(c)
    result = dict(c)
    if 'monthly_payment' not in c:
        return result
    payment = money(c['monthly_payment'])
    if c.get('balance_snapshot'):
        snapshot = c['balance_snapshot']
        anchor = dt.date.fromisoformat(snapshot['as_of'])
        initial = money(snapshot['amount'])
        elapsed = 0
        month = anchor.replace(day=1)
        while month <= today:
            due = month.replace(day=min(c['payment_day'], calendar.monthrange(month.year, month.month)[1]))
            elapsed += anchor < due <= today
            month = (month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        balance = max(Decimal(0), initial - payment * elapsed)
        result.update(balance_as_of=anchor.isoformat(), balance_snapshot=float(initial),
                      projected_balance=float(balance),
                      remaining_payments=int((balance / payment).to_integral_value(rounding=ROUND_CEILING)))
    else:
        end = dt.date.fromisoformat(c['end'])
        months = max(0, (end.year - today.year) * 12 + end.month - today.month + 1)
        result.update(estimated_payments=months, estimated_scheduled_total=float(payment * months))
    return result


def reminder_date(end, months):
    year, month = divmod(end.year * 12 + end.month - 1 - months, 12)
    return dt.date(year, month + 1, min(end.day, calendar.monthrange(year, month + 1)[1]))


def records(db, today):
    return sorted((project(json.loads(row[0]), today) for row in db.execute('SELECT data FROM contracts')), key=lambda c: c['end'])


def due(db, today):
    sent = {row[0] for row in db.execute('SELECT key FROM sent')}
    out = []
    for c in records(db, today):
        end = dt.date.fromisoformat(c['end'])
        for months in c.get('remind_months', [6, 3] if c['kind'] == 'car lease' else [1]):
            key = '%s:%s' % (c['id'], months)
            if reminder_date(end, months) <= today <= end and key not in sent:
                out.append({'key': key, 'id': c['id'], 'months': months, 'end': c['end'], 'who': c.get('who', [])})
    return out
