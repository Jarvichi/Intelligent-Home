"""Content-free, local consumer progress evaluation; no alerts or network IO.
An ACK watermark must mean contiguous outcomes completed, not merely fetched.
"""
import json,math,os,re,tempfile
from pathlib import Path

class InvalidSnapshot(ValueError):pass
class StateUnavailable(RuntimeError):pass
KEY=re.compile(r'^[A-Za-z0-9_.-]{1,60}$')

def number(value):
    return isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value)

def evaluate(snapshot,previous,now,*,stall_after=300,startup_grace=60,max_snapshot_age=120,enabled=True):
    """Return (public evidence, next checkpoint). Bad input preserves previous.

    Snapshot keys: epoch, produced_seq, acked_seq, oldest_pending_at,
    observed_at. Optional heartbeat_at is evidence, never proof of consumption.
    UTC timestamps are seconds. All counters are cumulative contiguous sequence
    watermarks within a durable producer epoch. Restarts retain the epoch;
    destructive queue resets require a new epoch.
    """
    previous=dict(previous or {})
    def unknown(reason):return {'status':'unknown','reason':reason},previous
    if not enabled:return {'status':'disabled','reason':'monitor_disabled'},previous
    if not number(now):return unknown('invalid_clock')
    for v in [stall_after,startup_grace,max_snapshot_age]:
        if not number(v) or v<0:return unknown('invalid_policy')
    if not isinstance(snapshot,dict):return unknown('invalid_snapshot')
    if not isinstance(snapshot.get('enabled'),bool):return unknown('invalid_enabled_state')
    if snapshot.get('consistent') is False:return unknown('producer_inconsistent')
    epoch=snapshot.get('epoch');produced=snapshot.get('produced_seq');acked=snapshot.get('acked_seq');observed=snapshot.get('observed_at')
    if not isinstance(epoch,str) or not KEY.fullmatch(epoch):return unknown('invalid_epoch')
    if any(not isinstance(v,int) or isinstance(v,bool) or v<0 for v in [produced,acked]) or acked>produced:
        return unknown('invalid_watermarks')
    if not number(observed) or observed>now:return unknown('invalid_observation_time')
    if now-observed>max_snapshot_age:return unknown('stale_snapshot')
    heartbeat=snapshot.get('heartbeat_at')
    if heartbeat is not None and (not number(heartbeat) or heartbeat>observed):return unknown('invalid_heartbeat')
    oldest=snapshot.get('oldest_pending_at')
    pending=produced-acked
    if pending and (not number(oldest) or oldest>observed):return unknown('missing_or_invalid_pending_age')
    same_epoch=previous.get('epoch')==epoch
    if same_epoch:
        if observed<previous['observed_at']:return unknown('out_of_order_snapshot')
        if produced<previous['produced_seq'] or acked<previous['acked_seq']:
            return unknown('watermark_regression')
        first_seen=previous['first_seen'];last_progress=previous['last_progress']
        if acked>previous['acked_seq']:last_progress=observed
        # An idle-to-pending transition starts a new observation period.
        if previous['produced_seq']==previous['acked_seq'] and pending:
            first_seen=observed;last_progress=observed
    else:first_seen=last_progress=observed
    if now<first_seen or now<last_progress:return unknown('clock_regression')
    state={'epoch':epoch,'produced_seq':produced,'acked_seq':acked,'observed_at':observed,
           'first_seen':first_seen,'last_progress':last_progress}
    result={'pending':pending,'snapshot_age_s':now-observed,
            'heartbeat_age_s':now-heartbeat if heartbeat is not None else None}
    if not pending:
        result.update(status='idle',reason='no_pending_outcomes')
    else:
        result.update(oldest_pending_age_s=now-oldest,no_progress_s=now-last_progress)
        if now-first_seen<startup_grace:
            result.update(status='waiting',reason='observation_grace')
        elif now-oldest>=stall_after and now-last_progress>=stall_after:
            result.update(status='stalled',reason='pending_without_outcome_progress')
        elif now-oldest>=stall_after:
            result.update(status='catching_up',reason='old_backlog_with_recent_progress')
        else:
            result.update(status='waiting',reason='pending_within_deadline')
    return result,state

class Checkpoints:
    """One monitor writer owns this local JSON file. Atomic, mode 600 saves.
    A corrupt file is surfaced, not silently replaced or treated as healthy.
    """
    def __init__(self,path):self.path=Path(path)
    def load(self):
        try:
            with self.path.open() as f:obj=json.load(f)
        except FileNotFoundError:return {}
        except (OSError,ValueError) as e:raise StateUnavailable('Cannot read progress checkpoints') from e
        if not isinstance(obj,dict) or obj.get('version')!=1 or not isinstance(obj.get('consumers'),dict):
            raise StateUnavailable('Invalid progress checkpoint format')
        for key,value in obj['consumers'].items():
            required={'epoch','produced_seq','acked_seq','observed_at','first_seen','last_progress'}
            if not KEY.fullmatch(key) or not isinstance(value,dict) or set(value)!=required:
                raise StateUnavailable('Invalid consumer checkpoint')
            if not KEY.fullmatch(str(value['epoch'])) or any(not number(value[k]) for k in ['observed_at','first_seen','last_progress']):
                raise StateUnavailable('Invalid checkpoint values')
            if any(not isinstance(value[k],int) or isinstance(value[k],bool) or value[k]<0 for k in ['produced_seq','acked_seq']) or value['acked_seq']>value['produced_seq']:
                raise StateUnavailable('Invalid checkpoint watermarks')
        return obj['consumers']
    def save(self,consumers):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='.progress-',dir=self.path.parent)
        try:
            with os.fdopen(fd,'w') as stream:
                os.fchmod(stream.fileno(),0o600)
                json.dump({'version':1,'consumers':consumers},stream,sort_keys=True,allow_nan=False)
                stream.flush();os.fsync(stream.fileno())
            os.replace(tmp,self.path)
            dfd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(dfd)
            finally:os.close(dfd)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)

class Monitor:
    def __init__(self,path,**policy):self.store=Checkpoints(path);self.policy=policy
    def sample(self,consumer,snapshot,now,enabled=True):
        if not isinstance(consumer,str) or not KEY.fullmatch(consumer):raise ValueError('Invalid consumer key')
        try:all_states=self.store.load()
        except StateUnavailable:return {'status':'unknown','reason':'checkpoint_unavailable'}
        result,state=evaluate(snapshot,all_states.get(consumer),now,enabled=enabled,**self.policy)
        if state!=all_states.get(consumer) and state:
            all_states[consumer]=state
            try:self.store.save(all_states)
            except OSError:return {'status':'unknown','reason':'checkpoint_write_failed'}
        return result
