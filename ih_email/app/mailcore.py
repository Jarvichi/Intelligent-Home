"""ih_email core: read the house mailbox (IMAP over SSL) and send from it (SMTP over SSL). Standard library only.
Rules built in here, not left to the caller:
- Reading never changes the mailbox (BODY.PEEK, read-only select): a message is not marked as read.
- Attachments are never returned, only their file names. HTML is turned into plain text.
- A send is refused unless confirmed is the JSON value true, refused over max_recipients, refused after max_per_day sends in a day.
- Nothing about a message (subject, body, addresses) is ever logged: the log line holds the time, the kind and a recipient COUNT.
imap_factory / smtp_factory / clock are injectable so the tests use fakes and never touch the network."""
import email, email.policy, email.utils, html.parser, imaplib, re, smtplib, ssl, time
from email.message import EmailMessage

FETCH_LIST_BYTES = 200000          # first bytes of a message used for a list line (snippet, attachment names)
FETCH_READ_BYTES = 1000000         # first bytes of a message used for a full read
SNIPPET_CHARS = 160
BODY_MAX_CHARS = 20000             # longest text returned for one message
SEND_BODY_MAX = 20000
SUBJECT_MAX = 200
MAX_LIMIT = 20
ADDR_RE = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


class MailError(Exception):
    """An error with a short code (safe to show) and an HTTP status."""
    def __init__(self, code, status=400, detail=''):
        super().__init__(code)
        self.code, self.status, self.detail = code, status, detail


# ---------- text helpers ----------
class _Strip(html.parser.HTMLParser):
    BLOCK = {'p', 'div', 'br', 'tr', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'ul', 'ol', 'blockquote'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'head', 'title'):
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head', 'title'):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def strip_html(s):
    p = _Strip()
    try:
        p.feed(s)
        p.close()
    except Exception:
        pass
    return tidy(''.join(p.out))


def tidy(t):
    t = t.replace('\r', '')
    t = re.sub(r'[ \t\xa0]+', ' ', t)
    t = re.sub(r' ?\n ?', '\n', t)
    return re.sub(r'\n{3,}', '\n\n', t).strip()


def _hdr(msg, name):
    v = msg.get(name)
    return ' '.join(str(v).split()) if v is not None else ''


def body_text(msg):
    """Plain text of a parsed message. Prefers text/plain; falls back to stripped HTML. Attachments are skipped."""
    plain = html_part = None
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.is_multipart() or part.get_content_disposition() == 'attachment':
            continue
        ct = part.get_content_type()
        if ct not in ('text/plain', 'text/html'):
            continue
        try:
            txt = part.get_content()
        except Exception:
            try:
                txt = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'replace')
            except Exception:
                continue
        if ct == 'text/plain' and plain is None:
            plain = txt
        elif ct == 'text/html' and html_part is None:
            html_part = txt
    if plain is not None and plain.strip():
        return tidy(plain)
    if html_part is not None:
        return strip_html(html_part)
    return ''


def attachment_names(msg):
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        fn = part.get_filename()
        if fn or part.get_content_disposition() == 'attachment':
            out.append(' '.join(str(fn or '(unnamed)').split())[:120])
    return out


def dmarc_result(msg):
    """'pass', 'fail' or 'none' from the Authentication-Results header the mailbox provider added."""
    for v in msg.get_all('Authentication-Results') or []:
        m = re.search(r'\bdmarc=(\w+)', str(v), re.I)
        if m:
            return m.group(1).lower()
    return 'none'


def parse_raw(raw):
    return email.message_from_bytes(raw, policy=email.policy.default)


def summarize(uid, msg):
    txt = body_text(msg)
    return {'id': str(uid), 'from': _hdr(msg, 'From'), 'subject': _hdr(msg, 'Subject')[:SUBJECT_MAX], 'date': _hdr(msg, 'Date'),
            'snippet': ' '.join(txt.split())[:SNIPPET_CHARS], 'dmarc': dmarc_result(msg), 'attachments': len(attachment_names(msg))}


def full(uid, msg):
    d = summarize(uid, msg)
    d.pop('snippet')
    txt = body_text(msg)
    d.update({'to': _hdr(msg, 'To'), 'reply_to': _hdr(msg, 'Reply-To'), 'message_id': _hdr(msg, 'Message-ID'),
              'text': txt[:BODY_MAX_CHARS], 'truncated': len(txt) > BODY_MAX_CHARS, 'attachment_names': attachment_names(msg)})
    return d


# ---------- address / header checks ----------
def clean_line(v, name, maxlen=SUBJECT_MAX):
    v = str(v if v is not None else '')
    if '\r' in v or '\n' in v or '\x00' in v:
        raise MailError('bad_' + name)
    v = v.strip()
    if not v or len(v) > maxlen:
        raise MailError('bad_' + name)
    return v


def clean_address(a):
    a = str(a or '').strip()
    if '\r' in a or '\n' in a or len(email.utils.getaddresses([a])) != 1:      # exactly ONE address per entry
        raise MailError('bad_address')
    addr = email.utils.parseaddr(a)[1]
    if not ADDR_RE.match(addr):
        raise MailError('bad_address')
    return addr


def reply_subject(s):
    s = ' '.join(str(s or '').split())
    return s if re.match(r'(?i)^re:', s) else 'Re: ' + s


# ---------- the mailbox ----------
class Mailbox:
    def __init__(self, opts, db, imap_factory=None, smtp_factory=None, clock=time.time, log=None):
        self.o, self.db, self.clock = opts, db, clock
        self.log = log or (lambda m: None)
        self.imap_factory = imap_factory or (lambda: imaplib.IMAP4_SSL(opts['imap_host'], 993, ssl_context=ssl.create_default_context(), timeout=30))
        self.smtp_factory = smtp_factory or (lambda: smtplib.SMTP_SSL(opts['smtp_host'], 465, context=ssl.create_default_context(), timeout=30))

    def configured(self):
        return bool(self.o.get('gmail_address') and self.o.get('app_password'))

    def _today(self):
        return time.strftime('%Y-%m-%d', time.gmtime(self.clock()))

    # -- state kept in the add-on's own database (no secrets) --
    def note(self, key, value):
        self.db.execute('INSERT INTO status(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v', (key, str(value)))

    def sent_today(self):
        r = self.db.query('SELECT n FROM sent_per_day WHERE day=?', (self._today(),))
        return r[0][0] if r else 0

    def status(self):
        s = dict(self.db.query('SELECT k, v FROM status'))
        return {'configured': self.configured(), 'sent_today': self.sent_today(), 'max_per_day': self.o['max_per_day'],
                'max_recipients': self.o['max_recipients'], 'last_read': s.get('last_read', ''), 'last_send': s.get('last_send', ''),
                'last_error': s.get('last_error', '')}

    def error(self, kind, exc):
        self.note('last_error', '%s %s: %s' % (time.strftime('%Y-%m-%d %H:%M', time.gmtime(self.clock())), kind, type(exc).__name__))   # type only, never the message
        self.log('error kind=%s type=%s' % (kind, type(exc).__name__))

    # -- IMAP --
    def _imap(self):
        if not self.configured():
            raise MailError('not_configured', 503)
        m = self.imap_factory()
        m.login(self.o['gmail_address'], self.o['app_password'])
        typ, _ = m.select(self.o['mailbox'], readonly=True)
        if typ != 'OK':
            raise MailError('mailbox_unavailable', 502)
        return m

    @staticmethod
    def _payload(data):
        for item in data or []:
            if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], (bytes, bytearray)):
                return bytes(item[1])
        return None

    def _fetch(self, m, uid, nbytes):
        typ, data = m.uid('FETCH', str(uid), '(BODY.PEEK[]<0.%d>)' % nbytes)
        raw = self._payload(data) if typ == 'OK' else None
        if raw is None:
            raise MailError('not_found', 404)
        return parse_raw(raw)

    def search(self, query='', limit=10, unread=False):
        limit = max(1, min(MAX_LIMIT, int(limit or 10)))
        crit = []
        if unread:
            crit.append('UNSEEN')
        q = ' '.join(str(query or '').split())[:200]
        if q:
            if '\r' in q or '\n' in q:
                raise MailError('bad_query')
            crit += ['X-GM-RAW', '"%s"' % q.replace('\\', '\\\\').replace('"', '\\"')]
        m = self._imap()
        try:
            typ, data = m.uid('SEARCH', None, *(crit or ['ALL']))
            if typ != 'OK':
                raise MailError('search_failed', 502)
            uids = (data[0] or b'').split()[-limit:][::-1]          # newest first
            out = [summarize(u.decode(), self._fetch(m, u.decode(), FETCH_LIST_BYTES)) for u in uids]
        finally:
            self._close(m)
        self.note('last_read', time.strftime('%Y-%m-%d %H:%M', time.gmtime(self.clock())))
        self.log('read kind=search count=%d' % len(out))
        return out

    def read(self, uid):
        uid = str(uid)
        if not re.fullmatch(r'\d{1,12}', uid):
            raise MailError('bad_id')
        m = self._imap()
        try:
            msg = self._fetch(m, uid, FETCH_READ_BYTES)
        finally:
            self._close(m)
        self.note('last_read', time.strftime('%Y-%m-%d %H:%M', time.gmtime(self.clock())))
        self.log('read kind=message')
        return full(uid, msg)

    @staticmethod
    def _close(m):
        try:
            m.logout()
        except Exception:
            pass

    # -- SMTP --
    def _check_send(self, recipients, confirmed, draft):
        if len(recipients) < 1:
            raise MailError('bad_address')
        if len(recipients) > self.o['max_recipients']:
            raise MailError('too_many_recipients', 422)
        if confirmed is not True:
            raise MailError('not_confirmed', 409, draft)

    def _reserve(self):
        """Count one send BEFORE it happens (so two requests together cannot pass the limit). -> day. Raises when the day is full."""
        day = self._today()
        with self.db.tx():
            r = self.db.query('SELECT n FROM sent_per_day WHERE day=?', (day,))
            n = r[0][0] if r else 0
            if n >= self.o['max_per_day']:
                raise MailError('daily_limit_reached', 429)
            self.db.execute('INSERT INTO sent_per_day(day, n) VALUES(?, 1) ON CONFLICT(day) DO UPDATE SET n=n+1', (day,))
        return day

    def _refund(self, day):
        self.db.execute('UPDATE sent_per_day SET n=MAX(0, n-1) WHERE day=?', (day,))

    def _deliver(self, msg, kind, count):
        if not self.configured():
            raise MailError('not_configured', 503)
        day = self._reserve()
        try:
            s = self.smtp_factory()
            try:
                s.login(self.o['gmail_address'], self.o['app_password'])
                s.send_message(msg)
            finally:
                try:
                    s.quit()
                except Exception:
                    pass
        except Exception as e:
            self._refund(day)
            self.error('send', e)
            raise MailError('send_failed', 502)
        self.db.execute('INSERT INTO events(at, kind, recipients) VALUES(?, ?, ?)', (int(self.clock()), kind, count))
        self.note('last_send', time.strftime('%Y-%m-%d %H:%M', time.gmtime(self.clock())))
        self.log('sent kind=%s recipients=%d' % (kind, count))

    def _build(self, to, subject, body):
        msg = EmailMessage()
        msg['From'] = self.o['gmail_address']
        msg['To'] = ', '.join(to)
        msg['Subject'] = subject
        msg['Date'] = email.utils.formatdate(self.clock(), localtime=False)
        msg['Message-ID'] = email.utils.make_msgid(domain=self.o['gmail_address'].split('@')[-1])
        msg.set_content(body)
        return msg

    @staticmethod
    def _body(v):
        v = str(v if v is not None else '')
        if not v.strip() or len(v) > SEND_BODY_MAX or '\x00' in v:
            raise MailError('bad_body')
        return v

    def send(self, to, subject, body, confirmed=False):
        if isinstance(to, str):
            to = [to]
        if not isinstance(to, list):
            raise MailError('bad_address')
        recipients = [clean_address(a) for a in to]
        subject, body = clean_line(subject, 'subject'), self._body(body)
        self._check_send(recipients, confirmed, {'to': recipients, 'subject': subject, 'body': body})
        self._deliver(self._build(recipients, subject, body), 'send', len(recipients))
        return {'sent': True, 'recipients': len(recipients), 'sent_today': self.sent_today()}

    def reply(self, uid, body, confirmed=False):
        uid = str(uid)
        if not re.fullmatch(r'\d{1,12}', uid):
            raise MailError('bad_id')
        body = self._body(body)
        m = self._imap()
        try:
            orig = self._fetch(m, uid, FETCH_LIST_BYTES)
        finally:
            self._close(m)
        target = _hdr(orig, 'Reply-To') or _hdr(orig, 'From')
        recipients = [clean_address(target)]
        mid = _hdr(orig, 'Message-ID')
        subject = clean_line(reply_subject(_hdr(orig, 'Subject') or '(no subject)'), 'subject')
        self._check_send(recipients, confirmed, {'to': recipients, 'subject': subject, 'body': body, 'in_reply_to': mid})
        msg = self._build(recipients, subject, body)
        if mid:
            msg['In-Reply-To'] = mid
            refs = _hdr(orig, 'References')
            msg['References'] = (refs + ' ' + mid).strip()
        self._deliver(msg, 'reply', 1)
        return {'sent': True, 'recipients': 1, 'sent_today': self.sent_today()}


MIGRATIONS = [
    (1, 'CREATE TABLE status(k TEXT PRIMARY KEY, v TEXT NOT NULL)'),
    (2, 'CREATE TABLE sent_per_day(day TEXT PRIMARY KEY, n INTEGER NOT NULL)'),
    (3, 'CREATE TABLE events(id INTEGER PRIMARY KEY, at INTEGER NOT NULL, kind TEXT NOT NULL, recipients INTEGER NOT NULL)'),
]
