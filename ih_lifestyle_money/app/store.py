"""Private contract store and deterministic projections."""
import calendar
import datetime as dt
import json
import re
import sqlite3
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP


def open_store(path):
    db = sqlite3.connect(path, check_same_thread=False)
    db.execute("CREATE TABLE IF NOT EXISTS contracts (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS sent (key TEXT PRIMARY KEY, sent_date TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS deliveries (key TEXT NOT NULL, recipient TEXT NOT NULL, status TEXT NOT NULL, PRIMARY KEY (key, recipient))")
    db.execute("CREATE TABLE IF NOT EXISTS planner (id INTEGER PRIMARY KEY CHECK (id=1), revision INTEGER NOT NULL, data TEXT NOT NULL)")
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
    if not isinstance(c, dict) or not all(isinstance(c.get(k), str) and c[k] for k in ('id', 'name', 'kind')):
        raise ValueError('invalid contract')
    start = dt.date.fromisoformat(c['start']) if c.get('start') else None
    end = dt.date.fromisoformat(c['end']) if c.get('end') else None
    if start and end and end <= start:
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
    if 'observed_balance' in c:
        money(c['observed_balance']['amount'])
        date = c['observed_balance']['as_of']
        if not isinstance(date, str) or not re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?', date):
            raise ValueError('invalid balance date')
        dt.date.fromisoformat(date if len(date) == 10 else date + '-01')
    if 'interest_rate_percent' in c:
        try:
            rate = Decimal(str(c['interest_rate_percent']))
            if not rate.is_finite() or rate < 0 or rate > 1000:
                raise ValueError('invalid interest rate')
        except (InvalidOperation, TypeError):
            raise ValueError('invalid interest rate') from None
    if c.get('interest_rate_until'):
        dt.date.fromisoformat(c['interest_rate_until'])
    if c.get('mortgage_term_end'):
        dt.date.fromisoformat(c['mortgage_term_end'])
    changes = c.get('loan_changes', [])
    if not isinstance(changes, list) or len(changes) > 500:
        raise ValueError('invalid loan changes')
    for change in changes:
        if not isinstance(change, dict) or change.get('type') not in ('balance', 'rate', 'payment', 'overpayment', 'recurring_overpayment', 'provider', 'account'):
            raise ValueError('invalid loan change')
        dt.date.fromisoformat(change.get('date', ''))
        if change['type'] in ('provider', 'account'):
            if not isinstance(change.get('value'), str) or not change['value'].strip() or len(change['value']) > 160:
                raise ValueError('invalid provider')
        elif change['type'] == 'rate':
            try:
                rate = Decimal(str(change.get('value')))
                if not rate.is_finite() or not 0 <= rate <= 1000:
                    raise ValueError('invalid interest rate')
            except (InvalidOperation, TypeError):
                raise ValueError('invalid interest rate') from None
        else:
            amount = money(change.get('value'))
            if change['type'] in ('payment', 'overpayment') and amount <= 0:
                raise ValueError('invalid payment')
    snapshot = c.get('observed_balance') or c.get('balance_snapshot')
    if changes and snapshot:
        label = snapshot['as_of']
        anchor = dt.date.fromisoformat(label if len(label) == 10 else label + '-01')
        if any(dt.date.fromisoformat(x['date']) <= anchor for x in changes):
            raise ValueError('changes must be after the starting balance date')
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
    if 'loan' in c['kind'].lower() or 'mortgage' in c['kind'].lower():
        return result  # principal and interest need a loan model, not lease subtraction
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
    elif c.get('end'):
        end = dt.date.fromisoformat(c['end'])
        months = max(0, (end.year - today.year) * 12 + end.month - today.month + 1)
        result.update(estimated_payments=months, estimated_scheduled_total=float(payment * months))
    return result


def reminder_date(end, months):
    year, month = divmod(end.year * 12 + end.month - 1 - months, 12)
    return dt.date(year, month + 1, min(end.day, calendar.monthrange(year, month + 1)[1]))


def records(db, today):
    rows = []
    for row in db.execute('SELECT data FROM contracts'):
        c = project(json.loads(row[0]), today)
        start = dt.date.fromisoformat(c['start']) if c.get('start') else None
        end = dt.date.fromisoformat(c['end']) if c.get('end') else None
        total = (end - start).days if start and end else None
        months = c.get('remind_months') or ([6, 3] if c['kind'] == 'car lease' else [1])
        c.update(days_total=total, days_left=(end - today).days if end else None,
                 percent=max(0, min(100, round(100 * (today - start).days / total))) if total else None,
                 months=months, reminders=[reminder_date(end, m).isoformat() for m in months] if end else [])
        rows.append(c)
    return sorted(rows, key=lambda c: c.get('end') or '9999-12-31')


def raw_records(db):
    return [json.loads(row[0]) for row in db.execute('SELECT data FROM contracts ORDER BY id')]


def read_planner(db):
    row = db.execute('SELECT revision, data FROM planner WHERE id=1').fetchone()
    return {'revision': row[0], 'data': json.loads(row[1])} if row else {'revision': 0, 'data': None}


def save_planner(db, data, revision):
    if not isinstance(data, dict) or data.get('format') != 'ih-money-planner-v25' or not isinstance(data.get('anchors'), dict) or not isinstance(data.get('scenario'), dict):
        raise ValueError('invalid planner data')
    if type(revision) is not int or revision < 0:
        raise ValueError('invalid revision')
    encoded = json.dumps(data, allow_nan=False)
    if len(encoded.encode()) > 60000:
        raise ValueError('planner data too large')
    with db:
        current = db.execute('SELECT revision FROM planner WHERE id=1').fetchone()
        if (current[0] if current else 0) != revision:
            raise RuntimeError('planner changed')
        db.execute('INSERT INTO planner (id,revision,data) VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,data=excluded.data', (revision+1, encoded))
    return revision+1


def projection_series(c):
    """One recorded snapshot followed by an explicitly illustrative schedule.

    This is not payment history. Never apply lease subtraction to loans or
    fabricate a series when the balance, payment or payment day is missing.
    """
    validate(c)
    kind = c['kind'].lower()
    if 'loan' in kind or 'mortgage' in kind:
        return loan_projection_series(c)
    if 'lease' not in kind or not c.get('balance_snapshot'):
        return []
    snapshot = c['balance_snapshot']
    anchor = dt.date.fromisoformat(snapshot['as_of'])
    balance = money(snapshot['amount'])
    payment = money(c['monthly_payment'])
    count = int((balance / payment).to_integral_value(rounding=ROUND_CEILING))
    if count == 0 or count > 600:
        return []
    points = [{'date': anchor.isoformat(), 'balance': float(balance)}]
    month = anchor.replace(day=1)
    try:
        while balance > 0:
            due = month.replace(day=min(c['payment_day'], calendar.monthrange(month.year, month.month)[1]))
            if due > anchor:
                balance = max(Decimal(0), balance - payment)
                points.append({'date': due.isoformat(), 'balance': float(balance)})
            if balance:
                month = (month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    except (ValueError, OverflowError):
        return []
    return points


def loan_projection_series(c):
    """Monthly nominal interest and fixed payment; no fees or rate changes.

    The snapshot is assumed to be after that month's payment. A monthly point
    is only included when its month end is within the known rate/term period.
    """
    if c.get('loan_changes'):
        return loan_changes_projection(c)
    snapshot = c.get('observed_balance') or c.get('balance_snapshot')
    if not snapshot or 'interest_rate_percent' not in c or not c.get('monthly_payment'):
        return []
    balance = money(snapshot['amount'])
    if not balance:
        return []
    rate = Decimal(str(c['interest_rate_percent'])) / Decimal(1200)
    payment = money(c['monthly_payment'])
    label = snapshot['as_of']
    anchor = dt.date.fromisoformat(label if len(label) == 10 else label + '-01')
    limits = [dt.date.fromisoformat(c[k]) for k in ('interest_rate_until', 'end') if c.get(k)]
    limit = min(limits) if limits else None
    points = [{'date': label, 'balance': float(balance)}]
    month = anchor.replace(day=1)
    try:
        for i in range(600):
            month = (month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            month_end = month.replace(day=calendar.monthrange(month.year, month.month)[1])
            if limit and month_end > limit:
                break
            interest = (balance * rate).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            balance = max(Decimal(0), balance + interest - payment)
            points.append({'date': month.strftime('%Y-%m'), 'balance': float(balance)})
            if balance == 0 or (i >= 11 and points[1]['balance'] >= points[0]['balance']):
                break
    except (ValueError, OverflowError, InvalidOperation):
        return []
    return points if len(points) > 1 else []


def loan_changes_projection(c):
    """Illustrate monthly payments with dated changes; snapshot months replace the calculation."""
    snapshot = c.get('observed_balance') or c.get('balance_snapshot')
    if not snapshot or 'interest_rate_percent' not in c or not c.get('monthly_payment'):
        return []
    label = snapshot['as_of']
    anchor = dt.date.fromisoformat(label if len(label) == 10 else label + '-01')
    balance, rate, payment = money(snapshot['amount']), Decimal(str(c['interest_rate_percent'])), money(c['monthly_payment'])
    changes = sorted(c['loan_changes'], key=lambda x: x['date'])
    if any(dt.date.fromisoformat(x['date']) <= anchor for x in changes):
        return []  # avoid silently ignoring entries before the initial snapshot
    expiry = dt.date.fromisoformat(c['interest_rate_until']) if c.get('interest_rate_until') else None
    end = dt.date.fromisoformat(c['end']) if c.get('end') else None
    if end and any(x['type'] in ('rate', 'balance') and dt.date.fromisoformat(x['date']) >= end for x in changes):
        end = None  # a later balance or rate explicitly continues the loan beyond this deal
    points = [{'date': label, 'balance': float(balance)}]
    month = anchor.replace(day=1)
    idx = 0
    recurring = Decimal(0)
    try:
        for _ in range(600):
            month = (month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            month_end = month.replace(day=calendar.monthrange(month.year, month.month)[1])
            if end and month_end > end:
                break
            batch = []
            while idx < len(changes) and dt.date.fromisoformat(changes[idx]['date']) <= month_end:
                batch.append(changes[idx]); idx += 1
            if expiry and month_end > expiry and not any(x['type'] == 'rate' and dt.date.fromisoformat(x['date']) <= expiry + dt.timedelta(days=1) for x in batch):
                break
            recorded = False
            for x in batch:
                if x['type'] == 'balance': balance, recorded = money(x['value']), True
                elif x['type'] == 'rate': rate, expiry = Decimal(str(x['value'])), None
                elif x['type'] == 'payment': payment = money(x['value'])
                elif x['type'] == 'overpayment': balance = max(Decimal(0), balance - money(x['value']))
                elif x['type'] == 'recurring_overpayment': recurring = money(x['value'])
            if not recorded:
                balance = max(Decimal(0), balance - recurring)
                interest = (balance * rate / Decimal(1200)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
                balance = max(Decimal(0), balance + interest - payment)
            points.append({'date': month.strftime('%Y-%m'), 'balance': float(balance), **({'recorded': True} if recorded else {})})
            if balance == 0 and idx >= len(changes):
                break
    except (ValueError, OverflowError, InvalidOperation):
        return []
    return points if len(points) > 1 else []


def mortgage_term_series(c, include_overpayments=True):
    """Illustrate the remaining term, carrying the last known rate forward."""
    if 'mortgage' not in c['kind'].lower() or not c.get('mortgage_term_end') or not (c.get('interest_rate_until') or c.get('end')):
        return []
    scenario = dict(c)
    scenario.pop('interest_rate_until', None)
    scenario['end'] = c['mortgage_term_end']
    scenario['loan_changes'] = [x for x in c.get('loan_changes', []) if x['date'] <= c['mortgage_term_end'] and (include_overpayments or x.get('type') not in ('overpayment', 'recurring_overpayment'))]
    return projection_series(scenario)


def markdown(rows):
    lines = ['| Contract | Start | End | Progress | Left |', '|---|---|---|---|---|']
    for c in rows:
        n = c['percent'] // 10 if c['percent'] is not None else None
        name = c['name'].replace('|', '\\|').replace('\n', ' ')
        progress = '%s%s %s%%' % ('█' * n, '░' * (10 - n), c['percent']) if n is not None else '—'
        left = '%s d' % c['days_left'] if c['days_left'] is not None else '—'
        lines.append('| %s | %s | %s | %s | %s |' % (
            name, c.get('start') or '—', c.get('end') or '—', progress, left))
    return '\n'.join(lines)


def due(db, today):
    sent = {row[0] for row in db.execute('SELECT key FROM sent')}
    out = []
    for c in records(db, today):
        if not c.get('end'):
            continue
        end = dt.date.fromisoformat(c['end'])
        for months in c['months']:
            key = '%s:%s' % (c['id'], months)
            if reminder_date(end, months) <= today <= end and key not in sent:
                out.append({'key': key, 'id': c['id'], 'months': months, 'end': c['end'], 'who': c.get('who', [])})
    return out
