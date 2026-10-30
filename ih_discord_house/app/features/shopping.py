"""Shopping channel: a shopping list in Home Assistant driven from Discord.
Trigger: new messages from allowed users in the shopping channel (setting channel_shopping, polled every 4 s) + reactions on the bot's own messages.
HA calls: todo.get_items / todo.add_item / todo.update_item on the setting shopping_list_entity; optionally the script meal_plan_script {quiet: true}.
Optional files (all settings, relative to root, empty = unused): aisle_order_file, meal_items_file; receipt photos are written to receipts_dir/<msg>/<n>.jpg."""
import collections, json, os, re, sys, time
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import settings as ST

CHAT_RE = re.compile(r"(done|ok|okay|all ok|all good|thanks|thank you|cheers|ta|yes|no|hi|hello|hey|finished|great|nice|cool|lol)")
GOT_RE = re.compile(r"(got|bought|done)\s+(.+)", re.S | re.I)
BATT_RE = re.compile(r"^Batteries: (\d+)\s*[×x]\s*(\S+)")
BATT_LABEL_RE = re.compile(r"^(\S+) batteries × \d+$")
AISLE_MATCH = [tuple(a) for a in ST.DEFAULT_AISLES]   # generic default; a deployment sets the setting "aisles"
AISLE_ORDER = list(ST.DEFAULT_AISLE_ORDER)


def shop_re(extra=''):
    """"shop" / "I'm at the shops" / "at the <extra shop>" ... (extra = setting shop_names, regex alternatives)."""
    names = "shop|shops|shopping|shopping list|supermarket" + (("|" + extra.strip("| ")) if extra and extra.strip("| ") else "")
    return re.compile(r"(i'?m |i am )?(at |in )?(the )?(" + names + ")")


SHOP_RE = shop_re()
KEYS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "\U0001f51f"]
MARK = "\U0001f6d2"
ASK_MARK = "❓"
ASK_WAIT = 180


# ---- pure logic (unit tested) ----
def learned(root='', rel='', base=None):
    """(order, rank): the learned order from the optional file rel (under root), else the base order (default AISLE_ORDER)."""
    base = list(base or AISLE_ORDER)
    p = ST.path_of(root, rel)
    try:
        d = json.load(open(p))
        order = [g for g in d.get('order', []) if g in base]
        return order + [g for g in base if g not in order], d.get('rank', {})
    except Exception:
        return base, {}


def norm_item(s):
    s = s.strip().lower()
    m = re.match(r"^(.*?)\s+[x×]\s?\d+(\s*\(.*\))?\s*$", s)
    if m and m.group(1).strip():
        s = (m.group(1) + (m.group(2) or "")).strip()
    return re.sub(r"^\d+\s?[x×]\s+", "", s)


def sort_aisle(items, rank):
    return sorted(items, key=lambda x: (norm_item(x) not in rank, rank.get(norm_item(x), 0), x.lower()))


def aisle(item, match=None):
    t = item.lower()
    for name, pat in (match or AISLE_MATCH):
        if re.search(r"\b(" + pat + r")", t):
            return name
    return "Other"


def merge_batteries(items):
    out, batt = [], {}
    for i in items:
        m = BATT_RE.match(i)
        if m:
            b = batt.setdefault(m.group(2), [0, []])
            b[0] += int(m.group(1)); b[1].append(i)
        else:
            out.append((i, [i]))
    for kind, (n, names) in sorted(batt.items()):
        out.append((f"{kind} batteries × {n}", names))
    return out


def is_meal(item, names):
    return re.sub(r"^(meals|staples): ", "", item.lower().strip()) in names


def meal_names(root='', rel=''):
    try:
        return set(json.load(open(ST.path_of(root, rel))))
    except Exception:
        return set()


def match_got(items, what):
    """Returns (done, unsure_lines, missing). Same rules as the integration's _got_answer."""
    w = what.strip().lower().rstrip("?!. ")
    if w in ("everything", "all", "it all", "the lot"):
        return list(items), [], []
    done, unsure, missing = [], [], []
    for t in [t.strip() for t in re.split(r",|\band\b|\n|&", w) if t.strip()]:
        open_items = [i for i in items if i not in done]
        exact = [i for i in open_items if i.lower() == t]
        part = [i for i in open_items if all(x in i.lower() for x in t.split())]
        hit = exact or part
        if len(hit) == 1 or exact:
            done.append(hit[0])
        elif hit:
            unsure.append(f"\"{t}\" could be: " + ", ".join(hit))
        else:
            missing.append(t)
    return done, unsure, missing


def got_reply(items, done, unsure, missing):
    left = [i for i in items if i not in done]
    out = []
    if done:
        out.append("Ticked: " + ", ".join(done))
    out += unsure
    if missing:
        out.append("Not on the list: " + ", ".join(missing))
    out.append(f"Left ({len(left)}): " + ", ".join(left) if left else "That's everything! 🎉")
    return "\n".join(out)


def checklist_messages(items, order, rank, match=None):
    """List of message texts (one per aisle chunk of max 10)."""
    groups = {}
    for i in items:
        groups.setdefault(aisle(i, match), []).append(i)
    total = sum(len(v) for g, v in groups.items() if g in order)   # only what is actually listed
    msgs, first = [], True
    for g in order:
        its = sort_aisle(groups.get(g, []), rank)
        for k in range(0, len(its), 10):
            chunk = its[k:k + 10]
            head = f"{MARK} **{g}**" + (f"  ({total} thing{'s' if total != 1 else ''} in all, across the aisles below: tap a number when you've got it)" if first else "")
            first = False
            msgs.append("\n".join([head] + [f"{KEYS[n]} {x}" for n, x in enumerate(chunk)]))
    return msgs


HELP = ("Type items to add them (comma or one per line). **shop** posts the list as a checklist by aisle: tap a number when you've got that thing. "
        "**got milk, bread** also ticks things off, **got everything** at the till.")
CHAT_REPLY = ("That isn't a shopping item, so I haven't added it. Type items to add them, **shop** for the checklist, "
              "**got milk** to tick off.")
CHAT_REPLY_RECEIPT = CHAT_REPLY[:-1] + ", or send a photo of the receipt."


class Feature:
    interval = 4

    def __init__(self, ctx, root=None):
        self.c = ctx
        self.cfg = ST.of(ctx)
        self.root = self.cfg['root'] if root is None else root
        self.list = self.cfg['shopping_list_entity']
        self.aisles = [tuple(a) for a in self.cfg['aisles']]
        self.base_order = list(self.cfg['aisle_order'])
        self.shop_re = shop_re(self.cfg['shop_names'])
        self.schan = self.last = None
        self.pend = None
        self.tries = 0
        self.handled = set()   # (message id, content) already struck: stops dry-run repeating itself
        self.routed = False    # True: the transport's router reads Discord and puts messages in self.inbox (tick() does not poll)
        self.inbox = collections.deque()
        # start from 2 minutes before the service started (Discord ids carry the time), as the integration does
        self.start_id = str(((int(ctx.now() * 1000) - 120000) - 1420070400000) << 22)

    # ---- helpers on HA / Discord ----
    def items(self):
        return [i['summary'] for i in self.c.ha.get_items(self.list)]

    def complete(self, name):
        """Tick ONE open item by its uid (30 Oct 2026: by name, HA picked an already-ticked twin and the owner's ticks were lost mid-shop)."""
        items = [i for i in self.c.ha.get_items(self.list) if i.get('uid')]
        hit = next((i for i in items if i.get('summary') == name), None) or next((i for i in items if (i.get('summary') or '').lower() == name.lower()), None)
        if hit:
            self.c.ha.call('todo', 'update_item', {'entity_id': self.list, 'item': hit['uid'], 'status': 'completed'})
        return bool(hit)

    def learned(self):
        return learned(self.root, self.cfg['aisle_order_file'], self.base_order)

    def add_meal_plan(self):
        if not self.cfg['meal_plan_script']:
            return
        try:
            self.c.ha.call('script', self.cfg['meal_plan_script'], {'quiet': True})
        except Exception as e:
            print('meal plan items not added:', e)

    def send(self, text, chan=None):
        self.c.discord.post(chan or self.schan, text)

    # ---- the flows ----
    def post_checklist(self, extras_only=False):
        d, ch = self.c.discord, self.schan
        me = d.me()
        st, old = d.req('GET', f'/channels/{ch}/messages?limit=100')
        for m in (old or []) if st == 200 else []:
            c = m.get('content') or ''
            if (m.get('author') or {}).get('id') == me.get('id') and (c.startswith(MARK) or c.startswith('☐ ') or c.startswith('~~')):
                d.req('DELETE', f"/channels/{ch}/messages/{m['id']}")
        self.add_meal_plan()
        items = [lbl for lbl, _ in merge_batteries(self.items())]
        if extras_only:
            mn = meal_names(self.root, self.cfg['meal_items_file'])
            items = [i for i in items if not is_meal(i, mn)]
        if not items:
            return self.send("Nothing on the list apart from the meal plan." if extras_only else "The shopping list is empty.")
        order, rank = self.learned()
        for text in checklist_messages(items, order, rank, self.aisles):
            st, msg = d.post(ch, text)
            if st < 300 and msg:
                for n in range(len(re.findall(r"\n", text))):
                    d.req('PUT', f"/channels/{ch}/messages/{msg['id']}/reactions/{quote(KEYS[n])}/@me")

    def ask_scope(self, user_id):
        d = self.c.discord
        st, msg = d.post(self.schan, f"{ASK_MARK} **Which list?** <@{user_id}> tap one:\n1️⃣ Full list (meal plan + extras)\n2️⃣ Only the extras (not the meal plan)\n_No answer in 3 minutes: I post the full list._")
        if st >= 300 or not msg:
            return None
        for k in KEYS[:2]:
            d.req('PUT', f"/channels/{self.schan}/messages/{msg['id']}/reactions/{quote(k)}/@me")
        return {'id': msg['id'], 'user': user_id, 't': self.c.now()}

    def check_scope(self):
        """None while waiting, True = extras only, False = full list."""
        p = self.pend
        for k, extras in ((KEYS[0], False), (KEYS[1], True)):
            st, users = self.c.discord.req('GET', f"/channels/{self.schan}/messages/{p['id']}/reactions/{quote(k)}")
            if st == 200 and any(u.get('id') == p['user'] for u in users or []):
                return extras
        if self.c.now() - p['t'] > ASK_WAIT:
            return False
        return None

    def check_ticks(self):
        d = self.c.discord
        me = d.me().get('id')
        st, msgs = d.req('GET', f'/channels/{self.schan}/messages?limit=50')
        if st != 200 or not msgs:
            return
        for m in msgs:
            c = m.get('content') or ''
            if (m.get('author') or {}).get('id') != me or not c.startswith(MARK):
                continue
            hit = {x.get('emoji', {}).get('name') for x in m.get('reactions') or [] if x.get('count', 0) > 1}
            lines, changed = c.split('\n'), False
            for n, line in enumerate(lines[1:], 1):
                key = KEYS[n - 1] if n <= len(KEYS) else None
                if key in hit and line.startswith(key + ' '):
                    item = line[len(key) + 1:].strip()
                    bm = BATT_LABEL_RE.match(item)
                    names = [item]
                    if bm:
                        names = [x for x in self.items() if (BATT_RE.match(x) or [None, None, None])[2] == bm.group(1)]
                    if (m['id'], line) in self.handled:
                        continue
                    self.handled.add((m['id'], line))
                    for nm in names:
                        try:
                            self.complete(nm)
                        except Exception:
                            pass
                    lines[n] = f"~~{line}~~ ✓"
                    changed = True
            if changed:
                d.req('PATCH', f"/channels/{self.schan}/messages/{m['id']}", {'content': '\n'.join(lines)})

    def handle_message(self, m):
        a, d = m.get('author', {}), self.c.discord
        text = (m.get('content') or '').strip()
        pics = [x for x in (m.get('attachments') or []) if (x.get('content_type') or '').startswith('image/')][:10] if self.cfg['receipts_dir'] else []
        if a.get('bot') or a.get('id') == d.me().get('id') or (not text and not pics) or text.startswith('!'):
            return
        if a.get('id') not in self.c.allowed():
            return
        t = text.lower().rstrip('?!. ')
        if pics:
            n = 0
            for x in pics:
                data = d.get_bytes(x['url'])
                if data:
                    n += 1
                    self.save_receipt(m['id'], f'{n}.jpg', data)
            reply = self.cfg['receipt_reply'].format(n=n, s='s' if n != 1 else '') if n else "Couldn't download that photo, try sending it again."
        elif t in ('list', 'help') or self.shop_re.fullmatch(t):
            if t == 'help':
                reply = HELP
            else:
                if self.pend:
                    d.req('DELETE', f"/channels/{self.schan}/messages/{self.pend['id']}")
                self.pend = self.ask_scope(a.get('id'))
                if self.pend is None:
                    self.post_checklist()
                return
        elif GOT_RE.fullmatch(text):
            items = self.items()
            done, unsure, missing = match_got(items, GOT_RE.fullmatch(text).group(2))
            for i in done:
                self.complete(i)
            reply = got_reply(items, done, unsure, missing)
        elif CHAT_RE.fullmatch(t) or text.endswith('?'):
            reply = CHAT_REPLY_RECEIPT if self.cfg['receipts_dir'] else CHAT_REPLY
        else:
            new = [x.strip() for x in re.split(r",|\n", text) if x.strip()]
            have = {x.lower() for x in self.items()}
            added = [x[:1].upper() + x[1:] for x in new if x.lower() not in have]
            for x in added:
                self.c.ha.call('todo', 'add_item', {'entity_id': self.list, 'item': x})
            reply = ("Added: " + ", ".join(added)) if added else "Already on the list."
        self.send(reply)

    def save_receipt(self, msg_id, name, data):
        if self.c.dry:
            print(f'[DRY] would save receipt {msg_id}/{name} ({len(data)} bytes)')
            return
        d = os.path.join(self.root, self.cfg['receipts_dir'], str(msg_id))
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, name), 'wb').write(data)

    # ---- one poll ----
    def tick(self):
        d = self.c.discord
        if self.schan is None:
            self.schan = d.find_channel(self.cfg['channel_shopping'])
            if self.schan is None:
                self.tries += 1
                time.sleep(30 if self.tries <= 10 else 300)
                return
            print('shopping channel found')
        if self.routed:   # the router already read the channel once: handle what it delivered, in order
            while self.inbox:
                self.handle_message(self.inbox.popleft())
        else:
            st, msgs = d.req('GET', f"/channels/{self.schan}/messages?limit=20" + (f"&after={self.last}" if self.last else ''))
            if st != 200:
                if st in (403, 404):
                    self.schan = self.last = None
                time.sleep(60)
                return
            if self.last is None:
                msgs = [m for m in msgs if int(m['id']) > int(self.start_id)]
                self.last = self.start_id
            for m in sorted(msgs, key=lambda x: int(x['id'])):
                self.last = m['id']
                self.handle_message(m)
        if self.pend:
            ans = self.check_scope()
            if ans is not None:
                d.req('DELETE', f"/channels/{self.schan}/messages/{self.pend['id']}")
                self.pend = None
                self.post_checklist(extras_only=ans)
        self.check_ticks()
