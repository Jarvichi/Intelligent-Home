"""Minimal CalDAV client (stdlib only) for iCloud: find one event by UID, change it, delete it. 0.11.0.
Why: Home Assistant's calendar service can only CREATE events for these calendars. Edit and delete need CalDAV itself.
Never logs the password. opener is injectable for tests. Non-recurring events only: a recurring event is refused (do it in the app)."""
import base64, datetime, re, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

NS = {'d': 'DAV:', 'c': 'urn:ietf:params:xml:ns:caldav'}
TIMEOUT = 20


class CalDAVError(Exception):
    pass


def slug(name):
    return re.sub(r'[^a-z0-9]+', '_', str(name).lower()).strip('_')


BS = chr(92)


def ics_escape(t):
    t = str(t).replace(BS, BS + BS).replace(';', BS + ';').replace(',', BS + ',').replace('\r', '')
    return t.replace('\n', BS + 'n')


def ics_unescape(t):
    out, i = [], 0
    while i < len(t):
        if t[i] == BS and i + 1 < len(t):
            out.append('\n' if t[i + 1] in 'nN' else t[i + 1]); i += 2
        else:
            out.append(t[i]); i += 1
    return ''.join(out)


def unfold(ics):
    out = []
    for ln in ics.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if ln[:1] in (' ', '\t') and out:
            out[-1] += ln[1:]
        else:
            out.append(ln)
    return [l for l in out if l != '']


def prop_name(line):
    return re.split(r'[;:]', line, 1)[0].upper()


def edit_ics(ics, changes, now=None):
    """changes: dict of summary / location / description (text) and start / end ('YYYY-MM-DD' all day, or an aware UTC datetime pair in
    'dtstart' / 'dtend' as ('date', date) or ('utc', datetime)). Returns new ICS text. Raises CalDAVError for recurring or multi-event files."""
    lines = unfold(ics)
    if sum(1 for l in lines if l.upper() == 'BEGIN:VEVENT') != 1:
        raise CalDAVError('not a single event')
    names = {prop_name(l) for l in lines}
    if 'RRULE' in names or 'RECURRENCE-ID' in names or 'RDATE' in names:
        raise CalDAVError('recurring event: change it in the calendar app')
    b = next(i for i, l in enumerate(lines) if l.upper() == 'BEGIN:VEVENT')
    e = next(i for i, l in enumerate(lines) if l.upper() == 'END:VEVENT')
    head, ev, tail = lines[:b + 1], lines[b + 1:e], lines[e:]
    now = now or datetime.datetime.now(datetime.timezone.utc)

    def drop(*ns):
        nonlocal ev
        ev = [l for l in ev if prop_name(l) not in ns]
    for k, n in (('summary', 'SUMMARY'), ('location', 'LOCATION'), ('description', 'DESCRIPTION')):
        if k in changes and changes[k] is not None:
            drop(n)
            if str(changes[k]) != '':
                ev.append('%s:%s' % (n, ics_escape(changes[k])))
    for k, n in (('dtstart', 'DTSTART'), ('dtend', 'DTEND')):
        if k in changes and changes[k] is not None:
            kind, v = changes[k]
            drop(n)
            if n == 'DTEND':
                drop('DURATION')
            ev.append('%s;VALUE=DATE:%s' % (n, v.strftime('%Y%m%d')) if kind == 'date' else '%s:%s' % (n, v.strftime('%Y%m%dT%H%M%SZ')))
    seq = 0
    for l in ev:
        if prop_name(l) == 'SEQUENCE':
            try:
                seq = int(l.split(':', 1)[1])
            except ValueError:
                pass
    drop('SEQUENCE', 'DTSTAMP', 'LAST-MODIFIED')
    stamp = now.strftime('%Y%m%dT%H%M%SZ')
    ev += ['SEQUENCE:%d' % (seq + 1), 'DTSTAMP:' + stamp, 'LAST-MODIFIED:' + stamp]
    return '\r\n'.join(head + ev + tail) + '\r\n'


def ics_prop(ics, name):
    for l in unfold(ics):
        if prop_name(l) == name:
            return l.split(':', 1)[1] if ':' in l else ''
    return ''


class CalDAV:
    def __init__(self, user, password, base='https://caldav.icloud.com', opener=None, clock=time.time):
        self.base, self.opener, self.clock = base.rstrip('/') + '/', opener or urllib.request.urlopen, clock
        self._auth = 'Basic ' + base64.b64encode(('%s:%s' % (user, password)).encode()).decode()
        self._home, self._cals, self._cals_at = None, None, 0

    def _req(self, method, url, body=None, headers=None):
        h = {'Authorization': self._auth, 'User-Agent': 'IntelligentHomeAssistant (local)'}
        h.update(headers or {})
        data = body.encode() if isinstance(body, str) else body
        for _ in range(4):
            req = urllib.request.Request(url, method=method, data=data, headers=h)
            try:
                with self.opener(req, timeout=TIMEOUT) as r:
                    return r.status, dict(r.headers), r.read()
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 307, 308) and e.headers.get('Location'):
                    url = urllib.parse.urljoin(url, e.headers['Location']); continue
                raise CalDAVError('http %d' % e.code)
            except (urllib.error.URLError, OSError) as e:
                raise CalDAVError(type(e).__name__)
        raise CalDAVError('too many redirects')

    def _xml(self, method, url, body, depth):
        st, _, raw = self._req(method, url, body, {'Content-Type': 'application/xml; charset=utf-8', 'Depth': str(depth)})
        try:
            return ET.fromstring(raw)
        except ET.ParseError:
            raise CalDAVError('bad XML')

    def home(self):
        if self._home:
            return self._home
        x = self._xml('PROPFIND', self.base, '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>', 0)
        p = x.find('.//d:current-user-principal/d:href', NS)
        if p is None or not p.text:
            raise CalDAVError('no principal (wrong user or password?)')
        principal = urllib.parse.urljoin(self.base, p.text)
        x = self._xml('PROPFIND', principal, '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-home-set/></d:prop></d:propfind>', 0)
        h = x.find('.//c:calendar-home-set/d:href', NS)
        if h is None or not h.text:
            raise CalDAVError('no calendar home')
        self._home = urllib.parse.urljoin(principal, h.text)
        return self._home

    def calendars(self):
        """-> {slug of display name: calendar URL}. Cached for 10 minutes."""
        if self._cals is not None and self.clock() - self._cals_at < 600:
            return self._cals
        x = self._xml('PROPFIND', self.home(), '<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/><d:resourcetype/></d:prop></d:propfind>', 1)
        out = {}
        for r in x.findall('d:response', NS):
            if r.find('.//c:calendar', NS) is None:
                continue
            n, h = r.find('.//d:displayname', NS), r.find('d:href', NS)
            if n is not None and n.text and h is not None and h.text:
                out[slug(n.text)] = urllib.parse.urljoin(self.home(), h.text)
        self._cals, self._cals_at = out, self.clock()
        return out

    def find(self, entity, uid):
        """-> (href, etag, ics) of the event with this UID in the calendar behind a HA entity id. Raises CalDAVError if not found."""
        cals = self.calendars()
        url = cals.get(entity.split('.', 1)[-1])
        if not url:
            raise CalDAVError('calendar not found on the server: %s' % entity)
        q = ('<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:getetag/><c:calendar-data/></d:prop>'
             '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"><c:prop-filter name="UID"><c:text-match collation="i;octet">%s</c:text-match>'
             '</c:prop-filter></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>') % re.sub(r'[<>&"]', '', uid)
        x = self._xml('REPORT', url, q, 1)
        found = []
        for r in x.findall('d:response', NS):
            d, et, h = r.find('.//c:calendar-data', NS), r.find('.//d:getetag', NS), r.find('d:href', NS)
            if d is not None and d.text and h is not None:
                found.append((urllib.parse.urljoin(url, h.text), (et.text if et is not None else '') or '', d.text))
        if len(found) != 1:
            raise CalDAVError('event not found' if not found else 'more than one event with that id')
        return found[0]

    def put(self, href, ics, etag):
        h = {'Content-Type': 'text/calendar; charset=utf-8'}
        if etag:
            h['If-Match'] = etag
        self._req('PUT', href, ics, h)

    def delete(self, href, etag):
        self._req('DELETE', href, None, {'If-Match': etag} if etag else {})
