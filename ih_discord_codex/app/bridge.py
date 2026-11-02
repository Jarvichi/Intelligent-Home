"""Offline Codex/IH bridge candidate. No default credentials, channels or service.
Transport must authenticate signed events before calling accept(). Explicit ACLs
are also enforced here. Model calls and posting are dependency-injected.
"""
import fcntl,json,math,os,re,sqlite3,threading,time,secrets
from pathlib import Path

class Bridge:
    def __init__(self,path,channels=(),users=(),clock=time.time):
        self.clock=clock;self.channels=set(channels);self.users=set(users)
        self.lock=threading.RLock()
        self._active_context=None
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        # One process owns the database for its whole lifetime. Acquire before
        # recovery: another opener must never relabel an active turn as crashed.
        self._owner_fd=os.open(str(path)+'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        os.fchmod(self._owner_fd,0o600)
        try:
            fcntl.flock(self._owner_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
            try:os.fchmod(fd,0o600)
            finally:os.close(fd)
            self.db=sqlite3.connect(path,check_same_thread=False)
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS jobs(channel TEXT, message TEXT, author TEXT,
              text TEXT, state TEXT, created REAL, thread TEXT, reason TEXT,
              PRIMARY KEY(channel,message));
            CREATE TABLE IF NOT EXISTS threads(channel TEXT PRIMARY KEY, thread TEXT);
            CREATE TABLE IF NOT EXISTS outbox(channel TEXT,message TEXT,part INTEGER,
              text TEXT,state TEXT,receipt TEXT, PRIMARY KEY(channel,message,part));
            CREATE TABLE IF NOT EXISTS action_plans(id TEXT PRIMARY KEY,channel TEXT NOT NULL,
              author TEXT NOT NULL,message TEXT NOT NULL,entity_id TEXT NOT NULL,domain TEXT NOT NULL,
              service TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS claude_handoffs(message TEXT PRIMARY KEY,summary TEXT NOT NULL,
              url TEXT,state TEXT NOT NULL,created REAL NOT NULL,receipt TEXT);
            ''')
            cutoff=self.clock()-30*86400
            with self.db:
                self.db.execute("DELETE FROM outbox WHERE state IN ('sent','abandoned') AND EXISTS (SELECT 1 FROM jobs j WHERE j.channel=outbox.channel AND j.message=outbox.message AND j.created<?)",(cutoff,))
                self.db.execute("DELETE FROM jobs WHERE created<? AND state IN ('answered','rejected','abandoned')",(cutoff,))
                self.db.execute("DELETE FROM claude_handoffs WHERE created<?",(cutoff,))
            # Process crashes cannot establish whether tools or Discord sends ran.
            with self.db:
                self.db.execute("UPDATE jobs SET state='needs_review',reason='interrupted model turn' WHERE state='running'")
                self.db.execute("UPDATE outbox SET state='needs_review' WHERE state='sending'")
                self.db.execute("UPDATE action_plans SET state='uncertain' WHERE state='executing'")
                self.db.execute("UPDATE claude_handoffs SET state='needs_review' WHERE state='sending'")
        except BaseException:
            if hasattr(self,'db'):self.db.close()
            os.close(self._owner_fd);self._owner_fd=None
            raise
    def close(self):
        with self.lock:
            if self._owner_fd is not None:
                try:self.db.close()
                finally:
                    os.close(self._owner_fd);self._owner_fd=None
    def accept(self,event):
        if not isinstance(event,dict):return False
        ch,uid,mid=event.get('channel'),event.get('author'),event.get('id')
        text=event.get('text')
        if (not isinstance(ch,str) or ch not in self.channels
            or not isinstance(mid,str) or not mid or len(mid)>128
            or not isinstance(uid,str) or not uid):return False
        with self.lock,self.db:
            allowed=uid in self.users and event.get('bot') is False and isinstance(text,str) and bool(text.strip()) and len(text)<=8000
            reason='authorization or input rejected'
            control=allowed and (text.strip().lower() in ('/help','/status','/new')
                or text.strip().lower().startswith('/approve ') or text.strip().lower().startswith('/deny ')
                or text.strip().lower().startswith('/code '))
            if allowed and not control:
                pending=self.db.execute("SELECT count(*) FROM jobs WHERE channel=? AND state IN ('queued','running','needs_review')",(ch,)).fetchone()[0]
                if pending>=8:
                    allowed=False;reason='queue limit'
            cur=self.db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?,?,?,?,?,?)',
              (ch,mid,uid,text if allowed else '', 'queued' if allowed else 'rejected',self.clock(),None,
               None if allowed else reason))
            if cur.rowcount==1 and reason=='queue limit':
                self.db.execute('INSERT OR IGNORE INTO outbox VALUES(?,?,?,?,?,?)',
                  (ch,mid,0,'The Codex queue is full. Send /status or /new to continue.','pending',None))
            # The signed transport event is durably represented before ACK. A
            # duplicate Discord ID is also safe to ACK because its first copy
            # is already persisted.
            return cur.rowcount==1 or self.db.execute('SELECT 1 FROM jobs WHERE channel=? AND message=?',(ch,mid)).fetchone() is not None
    def thread(self,ch):
        r=self.db.execute('SELECT thread FROM threads WHERE channel=?',(ch,)).fetchone()
        return r[0] if r else None
    def run_one(self,runner):
        with self.lock:
            # One in-flight turn per channel; an uncertain turn blocks its followers.
            row=self.db.execute("""SELECT channel,message,text,author FROM jobs j WHERE state='queued'
             AND (j.text IN ('/help','/status','/new') OR j.text LIKE '/approve %' OR j.text LIKE '/deny %' OR j.text LIKE '/code %' OR NOT EXISTS(SELECT 1 FROM jobs x WHERE x.channel=j.channel AND x.state IN ('running','needs_review')))
             AND (j.text IN ('/help','/status','/new') OR j.text LIKE '/approve %' OR j.text LIKE '/deny %' OR j.text LIKE '/code %' OR NOT EXISTS(SELECT 1 FROM outbox o WHERE o.channel=j.channel AND o.state IN ('sending','needs_review')))
             ORDER BY j.rowid LIMIT 1""").fetchone()
            if not row:return False
            ch,mid,text,author=row
            if ch not in self.channels or author not in self.users:
                with self.db:self.db.execute("UPDATE jobs SET state='rejected',reason='authorization revoked',text='' WHERE channel=? AND message=?",(ch,mid))
                return True
            with self.db:self.db.execute("UPDATE jobs SET state='running' WHERE channel=? AND message=?",(ch,mid))
            with self.lock:self._active_context={'channel':ch,'message':mid,'author':author}
            def save_thread(thread):
                if not isinstance(thread,str) or not thread:raise ValueError('invalid thread')
                with self.db:
                    self.db.execute('INSERT OR REPLACE INTO threads VALUES(?,?)',(ch,thread))
                    self.db.execute('UPDATE jobs SET thread=? WHERE channel=? AND message=?',(thread,ch,mid))
            try:
                answer=runner.run(self.thread(ch),text,save_thread)
                if not isinstance(answer,str) or not answer.strip():raise ValueError('empty answer')
                if len(answer)>18000:raise ValueError('answer exceeds bounded outbox')
                with self.db:
                    for part,offset in enumerate(range(0,len(answer),1800)):
                        self.db.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?)',(ch,mid,part,answer[offset:offset+1800],'pending',None))
                    self.db.execute("UPDATE jobs SET state='answered',text='' WHERE channel=? AND message=?",(ch,mid))
            except Exception:
                with self.db:self.db.execute("UPDATE jobs SET state='needs_review',reason='model outcome uncertain' WHERE channel=? AND message=?",(ch,mid))
            finally:
                with self.lock:self._active_context=None
            return True
    def deliver_one(self,sender):
        with self.lock:
            row=self.db.execute("""SELECT o.channel,o.message,o.part,o.text,j.author FROM outbox o JOIN jobs j ON j.channel=o.channel AND j.message=o.message WHERE o.state='pending'
            AND NOT EXISTS(SELECT 1 FROM outbox x WHERE x.channel=o.channel AND
              (x.state IN ('sending','needs_review') OR (x.message=o.message AND x.part<o.part AND x.state!='sent')))
            ORDER BY o.rowid LIMIT 1""").fetchone()
            if not row:return False
            ch,mid,part,text,author=row
            if ch not in self.channels or author not in self.users:
                with self.db:
                    self.db.execute("UPDATE outbox SET state='needs_review' WHERE channel=? AND message=? AND part=?",(ch,mid,part))
                    self.db.execute("UPDATE jobs SET reason='delivery authorization revoked' WHERE channel=? AND message=?",(ch,mid))
                return True
            with self.db:self.db.execute("UPDATE outbox SET state='sending' WHERE channel=? AND message=? AND part=?",(ch,mid,part))
        try:
            receipt=sender(ch,text,reply_to=mid,dedupe_key=ch+':'+mid+':'+str(part))
            if receipt=='dry':
                with self.lock,self.db:self.db.execute("UPDATE outbox SET state='dry',text='' WHERE channel=? AND message=? AND part=?",(ch,mid,part))
                return True
            if not isinstance(receipt,str) or not receipt:raise ValueError('receipt required')
            with self.lock,self.db:self.db.execute("UPDATE outbox SET state='sent',receipt=?,text='' WHERE channel=? AND message=? AND part=?",(receipt,ch,mid,part))
        except Exception:
            with self.lock,self.db:self.db.execute("UPDATE outbox SET state='needs_review' WHERE channel=? AND message=? AND part=?",(ch,mid,part))
        return True
    def status(self):
        with self.lock:
            return {'jobs':dict(self.db.execute('SELECT state,count(*) FROM jobs GROUP BY state')),
                    'delivery':dict(self.db.execute('SELECT state,count(*) FROM outbox GROUP BY state')),
                    'claude_handoffs':dict(self.db.execute('SELECT state,count(*) FROM claude_handoffs GROUP BY state'))}
    def queue_claude_handoff(self,context,summary,url=None):
        if not context or not isinstance(context.get('message'),str):
            raise ValueError('handoff needs an active authorized task')
        if not isinstance(summary,str) or not summary.strip() or len(summary)>900:
            raise ValueError('handoff summary must be between 1 and 900 characters')
        summary=' '.join(summary.split())
        private_shapes=(r'\b(?:192\.168\.|10\.\d{1,3}\.|172\.(?:1[6-9]|2\d|3[01])\.)\d{1,3}\.\d{1,3}\b',
                        r'\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b',r'\b\d{17,20}\b',
                        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
                        r'\b(?:password|secret|api[_-]?token|signing[_-]?key)\s*[:=]',
                        r'\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})\b',
                        r'(?i)(?:/homeassistant|/config|/share/|brain/|secrets\.yaml)',
                        r'\b(?:light|switch|sensor|binary_sensor|climate|cover|lock)\.[a-z0-9_]+\b')
        if any(re.search(pattern,summary,re.I) for pattern in private_shapes):
            raise ValueError('handoff summary contains a private-looking value or Home Assistant entity ID')
        if url is not None and (not isinstance(url,str) or not url.startswith('https://github.com/') or len(url)>300):
            raise ValueError('invalid review URL')
        with self.lock,self.db:
            self.db.execute("INSERT OR IGNORE INTO claude_handoffs(message,summary,url,state,created) VALUES(?,?,?,'pending',?)",
                            (context['message'],summary,url,self.clock()))
            row=self.db.execute('SELECT state FROM claude_handoffs WHERE message=?',(context['message'],)).fetchone()
            return row[0] if row else 'unknown'
    def deliver_claude_handoff(self,sender):
        with self.lock:
            row=self.db.execute("SELECT message,summary,url FROM claude_handoffs WHERE state='pending' ORDER BY created LIMIT 1").fetchone()
            if not row:return False
            message,summary,url=row
            with self.db:self.db.execute("UPDATE claude_handoffs SET state='sending' WHERE message=? AND state='pending'",(message,))
        text='Codex asks Claude to review or finish this IH code task: ' + summary
        if url:text+=' Public source PR: ' + url
        text+=' No Home Assistant state, entity IDs, or Brain content was forwarded.'
        try:
            receipt=sender(text)
            if receipt=='dry': state='dry'
            elif isinstance(receipt,str) and receipt: state='sent'
            else: raise ValueError('delivery receipt required')
            with self.lock,self.db:self.db.execute('UPDATE claude_handoffs SET state=?,receipt=? WHERE message=? AND state=\'sending\'',(state,str(receipt)[:128],message))
        except Exception:
            with self.lock,self.db:self.db.execute("UPDATE claude_handoffs SET state='needs_review' WHERE message=? AND state='sending'",(message,))
        return True
    def claude_handoffs_for(self,message_id):
        with self.lock:
            return [row[0] for row in self.db.execute("SELECT state FROM claude_handoffs WHERE message=?",(message_id,))]
    def active_context(self):
        with self.lock:return dict(self._active_context) if self._active_context else None
    def create_action_plan(self,context,entity_id,service):
        with self.lock,self.db:
            now=self.clock()
            self.db.execute("UPDATE action_plans SET state='expired' WHERE state='pending' AND expires<=?",(now,))
            if self.db.execute("SELECT 1 FROM action_plans WHERE channel=? AND state='pending' LIMIT 1",(context['channel'],)).fetchone():
                raise RuntimeError('another device action is awaiting approval')
            pid=secrets.token_hex(8).upper()
            self.db.execute('INSERT INTO action_plans VALUES(?,?,?,?,?,?,?,?,?,?)',
              (pid,context['channel'],context['author'],context['message'],entity_id,entity_id.split('.',1)[0],service,'pending',now,now+300))
            return {'id':pid,'entity_id':entity_id,'service':service,'expires':now+300}
    def action_plans_for(self,message_id):
        with self.lock:
            return [dict(zip(('id','entity_id','domain','service','expires'),row)) for row in self.db.execute(
              "SELECT id,entity_id,domain,service,expires FROM action_plans WHERE message=? AND state='pending' AND expires>? ORDER BY created",(message_id,self.clock()))]
    def claim_action_plan(self,context,proposal_id):
        if not context:return None
        with self.lock,self.db:
            row=self.db.execute("SELECT id,entity_id,domain,service FROM action_plans WHERE id=? AND channel=? AND author=? AND state='pending' AND expires>?",
              (str(proposal_id).upper(),context['channel'],context['author'],self.clock())).fetchone()
            if not row:return None
            changed=self.db.execute("UPDATE action_plans SET state='executing' WHERE id=? AND state='pending'",(row[0],)).rowcount
            return dict(zip(('id','entity_id','domain','service'),row)) if changed==1 else None
    def finish_action_plan(self,proposal_id,state):
        if state not in ('done','uncertain'):raise ValueError('invalid action outcome')
        with self.lock,self.db:self.db.execute("UPDATE action_plans SET state=? WHERE id=? AND state='executing'",(state,proposal_id))
    def reject_action_plan(self,context,proposal_id):
        if not context:return False
        with self.lock,self.db:
            return self.db.execute("UPDATE action_plans SET state='denied' WHERE id=? AND channel=? AND author=? AND state='pending' AND expires>?",
              (str(proposal_id).upper(),context['channel'],context['author'],self.clock())).rowcount==1
    def clear_thread(self,channel):
        with self.lock,self.db:
            self.db.execute('DELETE FROM threads WHERE channel=?',(channel,))
    def ensure_context_version(self,version,channels=()):
        """Start a fresh model thread once when its available tools/policy change."""
        with self.lock:
            self.db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            row=self.db.execute('SELECT value FROM settings WHERE key=?',('context_version',)).fetchone()
            if row and row[0]==str(version):return False
            with self.db:
                selected=[str(ch) for ch in channels if isinstance(ch,str) and ch]
                if selected:
                    marks=','.join('?' for _ in selected)
                    self.db.execute('DELETE FROM threads WHERE channel IN ('+marks+')',selected)
                self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('context_version',str(version)))
            return True
    def abandon_uncertain(self,channel):
        """Explicit owner command: retain receipts but clear ambiguous content and unblock a fresh thread."""
        with self.lock,self.db:
            self.db.execute("UPDATE jobs SET state='abandoned',text='',reason='owner started a new thread' WHERE channel=? AND state='needs_review'",(channel,))
            self.db.execute("UPDATE outbox SET state='abandoned',text='' WHERE channel=? AND state='needs_review'",(channel,))
            self.db.execute('DELETE FROM threads WHERE channel=?',(channel,))


def usage_view(result):
    """Allowlist account-limit metadata only; unknown/unavailable is not zero use."""
    if not isinstance(result,dict):return {'available':False,'limits':[]}
    buckets=result.get('rateLimitsByLimitId')
    if not isinstance(buckets,dict):buckets={'default':result.get('rateLimits')}
    out=[]
    for key,bucket in buckets.items():
        if not isinstance(bucket,dict):continue
        row={'limit':str(key)[:60]}
        for name in ('primary','secondary'):
            window=bucket.get(name)
            if isinstance(window,dict):
                used=window.get('usedPercent')
                if (not isinstance(used,(int,float)) or isinstance(used,bool)
                    or not math.isfinite(used) or not 0<=used<=100):continue
                row[name]={'usedPercent':used}
                for field in ('windowDurationMins','resetsAt'):
                    value=window.get(field)
                    if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0:
                        row[name][field]=value
        if len(row)>1:out.append(row)
    return {'available':bool(out),'limits':out}
