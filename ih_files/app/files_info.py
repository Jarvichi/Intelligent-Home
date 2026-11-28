"""Read-only facts about the Files add-on for the UI page.

Reads the generated smb.conf, the Samba user NAMES, `smbstatus` output, disk space and whether smbd runs.
Passwords, hashes and tokens are never read: smb.conf has none, `pdbedit -L` (no -w/-v) prints names only, and only
whitelisted fields are copied into the result. Nothing here is logged.
"""
import os
import re
import shutil
import subprocess

VERSION = '0.2.3'
SMB_CONF = os.environ.get('IHF_SMB_CONF', '/etc/samba/smb.conf')
DATA_ROOT = os.environ.get('IHF_DATA_ROOT', '/share/files')
NAME = re.compile(r'^[A-Za-z0-9_.-]{1,32}$')
DATE = re.compile(r'[A-Z][a-z]{2} [A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2} \d{4}(?: [A-Z]{2,5})?')


def parse_smb_conf(text):
    """Return {'port': int, 'shares': [...]} from smb.conf text. Only share facts are kept."""
    port = 445
    shares = []
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in '#;':
            continue
        if line.startswith('[') and line.endswith(']'):
            name = line[1:-1].strip()
            cur = None if name.lower() == 'global' else {'name': name}
            if cur is not None:
                shares.append(cur)
            continue
        if '=' not in line:
            continue
        key, _, val = line.partition('=')
        key = ' '.join(key.lower().split())
        val = val.strip()
        if cur is None:
            if key == 'smb ports':
                m = re.match(r'\d+', val)
                if m:
                    port = int(m.group())
            continue
        cur[key] = val
    out = []
    for s in shares:
        users = [u for u in s.get('valid users', '').replace(',', ' ').split() if NAME.match(u)]
        read_only = s.get('read only', 'yes').lower() in ('yes', 'true', '1')
        w = s.get('writeable', s.get('writable'))
        if w is not None:
            read_only = w.lower() not in ('yes', 'true', '1')
        readers = [u for u in s.get('read list', '').replace(',', ' ').split() if NAME.match(u)]
        writers = [u for u in s.get('write list', '').replace(',', ' ').split() if NAME.match(u)]
        access = []
        for u in users:
            if u in writers:
                mode = 'read-write'
            elif u in readers:
                mode = 'read-only'
            else:
                mode = 'read-only' if read_only else 'read-write'
            access.append({'user': u, 'mode': mode})
        out.append({
            'name': s['name'],
            'comment': s.get('comment', '')[:120],
            'path': s.get('path', ''),
            'access': access,
            'open_to_all': not users,
        })
    return {'port': port, 'shares': out}


def parse_user_names(text):
    """`pdbedit -L` lines are name:uid:full name. Keep the name only."""
    names = []
    for line in text.splitlines():
        n = line.split(':', 1)[0].strip()
        if NAME.match(n) and n not in names:
            names.append(n)
    return names


def parse_smbstatus(procs_text, shares_text):
    """Join `smbstatus -p -b` (pid, user, machine) with `smbstatus -S -b` (share, pid, machine, connected at)."""
    users = {}
    for line in procs_text.splitlines():
        p = line.split()
        if len(p) >= 4 and p[0].isdigit() and NAME.match(p[1]):
            users[p[0]] = {'user': p[1], 'machine': p[3][:64]}
    sessions = []
    in_rows = False
    for line in shares_text.splitlines():
        if line.startswith('Service') and 'pid' in line:
            in_rows = True
            continue
        if not in_rows or not line.strip() or set(line.strip()) <= {'-'}:
            continue
        p = line.split()
        if len(p) < 3 or not p[1].isdigit() or not NAME.match(p[0]):
            continue
        m = DATE.search(line)
        info = users.get(p[1], {})
        sessions.append({
            'share': p[0],
            'user': info.get('user', ''),
            'machine': (info.get('machine') or p[2])[:64],
            'since': m.group(0) if m else '',
        })
    return sessions


def _run(argv, timeout=5):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def smbd_running(proc='/proc'):
    try:
        for d in os.listdir(proc):
            if d.isdigit():
                try:
                    with open(os.path.join(proc, d, 'comm')) as f:
                        if f.read().strip() == 'smbd':
                            return True
                except OSError:
                    pass
    except OSError:
        pass
    return False


def disk(path=None):
    path = path or DATA_ROOT
    for p in (path, '/share', '/'):
        try:
            u = shutil.disk_usage(p)
            return {'total': u.total, 'used': u.used, 'free': u.free}
        except OSError:
            continue
    return None


def build_status(conf_text=None, names_text=None, procs_text=None, shares_text=None, running=None, disk_info=None):
    """Every input can be injected (tests); None means read the real thing."""
    if conf_text is None:
        try:
            with open(SMB_CONF) as f:
                conf_text = f.read()
        except OSError:
            conf_text = ''
    conf = parse_smb_conf(conf_text)
    if names_text is None:
        names_text = _run(['pdbedit', '-L'])
    login_names = parse_user_names(names_text or '')
    if procs_text is None:
        procs_text = _run(['smbstatus', '-p', '-b'])
    if shares_text is None:
        shares_text = _run(['smbstatus', '-S', '-b'])
    sessions = parse_smbstatus(procs_text or '', shares_text or '')
    people = []
    for s in conf['shares']:
        for a in s['access']:
            if a['user'] not in people:
                people.append(a['user'])
    for n in login_names:
        if n not in people:
            people.append(n)
    shares = []
    for s in conf['shares']:
        folder = s['path']
        shares.append({**s, 'folder_exists': bool(folder) and os.path.isdir(folder)})
    return {
        'healthy': bool(smbd_running() if running is None else running),
        'checked_at': None,
        'version': VERSION,
        'smb_port': conf['port'],
        'shares': shares,
        'logins': [{'name': n, 'login_enabled': n in login_names} for n in people],
        'sessions': sessions,
        'sessions_known': procs_text is not None and shares_text is not None,
        'disk': disk() if disk_info is None else disk_info,
    }
