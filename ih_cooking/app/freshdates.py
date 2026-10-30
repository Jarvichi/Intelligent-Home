"""Fact cooking/fresh_dates for the shop checklist (ih_discord_shopping): {"<normalised ingredient>": "YYYY-MM-DD"} = for each ingredient of the
planned meals from today on, the date of the FIRST planned meal that uses it. The weekly plan is mon..sun -> meal name; the next 7 days are looked at.
Pushed when it changes and at least every 12 h, TTL 36 h. stdlib only. Never logs values or the key."""
import datetime, json, re, threading, time

DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
NAME = 'fresh_dates'
TTL_S = 36 * 3600
REFRESH_S = 12 * 3600
POLL_S = 600
LIMIT = 4000   # the store allows 4096 bytes of JSON
UNITS = r'(?:kg|g|ml|l|lb|oz|tbsp|tsp|pack|packs|packet|packets|tin|tins|can|cans|jar|jars|bag|bags|bunch|bunches|pot|pots|punnet|punnets|large|small|medium|big|mixed|fresh|ripe|x)'
LEAD = re.compile(r'^(?:[0-9.,/½¼¾]+\s*(?:' + UNITS + r'\b)?\s*)+', re.I)


def norm_key(s):
    """Same rule as ih_discord_shopping factsview.norm_key: 'Meals: 2 chicken breasts (about 450 g)' -> 'chicken breasts'."""
    t = str(s or '').lower().strip()
    t = re.sub(r'^(?:meals|staples):\s*', '', t)
    t = re.sub(r'\([^)]*\)', ' ', t)
    t = re.sub(r'\s+[x×]\s?\d+\s*$', '', t)
    t = re.sub(r'^\d+\s?[x×]\s+', '', t)
    t = LEAD.sub('', t)
    t = re.sub(r'\s+[x×]\s?\d+\s*$', '', t)
    return re.sub(r'\s+', ' ', t).strip(' ,;-')[:60]


def compute(plan, shopping_by_meal, today, days=7):
    """plan {mon: 'Meal'}; shopping_by_meal {meal name lower case: [ingredient text]}; -> {key: 'YYYY-MM-DD'} (first use wins), trimmed to LIMIT bytes
    by dropping the latest dates first."""
    out = {}
    for n in range(days):
        day = today + datetime.timedelta(days=n)
        meal = str((plan or {}).get(DAYS[day.weekday()]) or '').strip().lower()
        for ing in shopping_by_meal.get(meal, []):
            k = norm_key(ing)
            if k and k not in out:
                out[k] = day.isoformat()
    while out and len(json.dumps(out, separators=(',', ':'), ensure_ascii=False).encode()) > LIMIT:
        out.pop(max(out, key=lambda k: (out[k], len(k))))
    return out


class Publisher:
    """Background loop: compute from the store's plan and recipes, PUT cooking/fresh_dates when changed or older than REFRESH_S. Off when facts is None."""
    def __init__(self, store, facts, log=print, clock=time.time, today=datetime.date.today):
        self.store, self.facts, self.log, self.clock, self.today = store, facts, log, clock, today
        self.sig, self.sent = None, -1e12

    def shopping_by_meal(self):
        m = {}
        for r in self.store.recipes():
            full = self.store.recipe(r['slug']) or {}
            for k in (full.get('title'), full.get('name')):
                if k:
                    m[str(k).lower()] = [str(x) for x in full.get('shopping', [])]
        return m

    def once(self):
        """-> 'sent' | 'same' | 'cleared' | 'nothing' | 'failed'."""
        if not self.facts:
            return 'off'
        try:
            plan, _ = self.store.plan()
            d = compute(plan, self.shopping_by_meal(), self.today())
            sig = json.dumps(d, sort_keys=True)
            now = self.clock()
            if sig == self.sig and now - self.sent < REFRESH_S:
                return 'same'
            if not d:
                if self.sig in (None, '{}'):
                    self.sig = '{}'
                    return 'nothing'
                self.facts.delete(NAME)
                self.sig, self.sent = sig, now
                self.log('fact cooking/%s cleared' % NAME)
                return 'cleared'
            self.facts.put(NAME, d, TTL_S)
            self.sig, self.sent = sig, now
            self.log('fact cooking/%s set (%d items)' % (NAME, len(d)))
            return 'sent'
        except Exception as e:
            self.log('fact cooking/%s not sent (%s), will retry' % (NAME, type(e).__name__))
            return 'failed'

    def run(self, sleep=time.sleep):
        while True:
            self.once()
            sleep(POLL_S)

    def start(self):
        if self.facts:
            threading.Thread(target=self.run, daemon=True).start()
