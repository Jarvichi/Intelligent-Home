"""Publishes the ledger's answers as Home Assistant sensors so dashboards read them live (no page is rebuilt after a receipt).
sensor.lifestyle_spend       state = latest week total in pounds; attributes: weeks (last 12 with a receipt: week, total, savings, categories), receipts, lines
sensor.lifestyle_due_soon    state = number of products due within a week; attributes: items (name, every_days, due, in_days, confidence)
sensor.lifestyle_price_rises state = number of products whose list price changed 5%+ in 90 days; attributes: changes
Pounds, not pence, in the attributes. Sensors made through the state API are not stored by Home Assistant: they are republished at start,
after every stored receipt and every hour."""
import stats as S
from ledger import q

SPEND, DUE, PRICES = 'sensor.lifestyle_spend', 'sensor.lifestyle_due_soon', 'sensor.lifestyle_price_rises'


def _p(v):
    return round(v / 100.0, 2)


def build(d, today):
    ws = S.weeks(d, today)
    weeks = [{'week': w['week'], 'total': _p(w['total_p']), 'savings': _p(w['savings_p']),
              'categories': {k: _p(v) for k, v in sorted(w['categories'].items(), key=lambda kv: -kv[1])}} for w in ws]
    c = q(d, 'SELECT (SELECT COUNT(*) FROM receipts) r, (SELECT COUNT(*) FROM receipt_lines) l')[0]
    due = [{'name': x['name'], 'every_days': x['every_days'], 'due': x['due'], 'in_days': x['in_days'], 'confidence': x['confidence']} for x in S.due_items(d, today)]
    ch = [{'name': x['name'], 'first_day': x['first_day'], 'last_day': x['last_day'], 'first': _p(x['first_p']), 'last': _p(x['last_p']), 'pct': x['pct'], 'net_pct': x['net_pct']}
          for x in S.price_changes(d, today)]
    return {
        SPEND: (weeks[-1]['total'] if weeks else 0, {'friendly_name': 'Grocery spend (latest week)', 'unit_of_measurement': '£', 'icon': 'mdi:cart', 'weeks': weeks, 'receipts': c['r'], 'lines': c['l']}),
        DUE: (len(due), {'friendly_name': 'Groceries due again', 'icon': 'mdi:calendar-refresh', 'items': due}),
        PRICES: (len(ch), {'friendly_name': 'Grocery price changes', 'icon': 'mdi:trending-up', 'changes': ch}),
    }


def publish(d, ha, today):
    """-> number of sensors written. Never raises: a failure is logged by the caller's return value 0."""
    n = 0
    for entity, (state, attrs) in build(d, today).items():
        ha.post('states/' + entity, {'state': state, 'attributes': attrs})
        n += 1
    return n
