"""SQLite-backed AI scheduling. HTTP work never owns a database write transaction."""
import hashlib
import json
import logging
from datetime import datetime,timedelta
from pathlib import Path
from statistics import median
from threading import Event, Lock, Thread
from uuid import uuid4

from fastapi import HTTPException

from . import ai_provider, clock, db, model_config

LOG = logging.getLogger(__name__)
HANDLERS = {}
VALIDATORS = {}
PUBLISHERS = {}
FAILURES = {}
SCAN_INTERVAL = 15.0
LEASE_SECONDS = 180
ESTIMATE_FALLBACKS = {'assist':90,'public_analysis':120,'topic_analysis':120,'project_analysis':180,'connection_test':30}
STATE = None
STATE_LOCK = Lock()


def initialize():
    with db.connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS ai_jobs (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL, requested_by_user_id INTEGER REFERENCES users(id),
            context_user_id INTEGER REFERENCES users(id), visibility TEXT NOT NULL,
            opportunity_id INTEGER REFERENCES opportunities(id), subscription_id INTEGER REFERENCES subscriptions(id),
            owner_revision INTEGER, input_hash TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE,
            priority TEXT NOT NULL, payload TEXT NOT NULL, model_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued', result TEXT, error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            available_at TEXT NOT NULL, lease_until TEXT, lease_token TEXT);
        CREATE INDEX IF NOT EXISTS ai_job_waiting ON ai_jobs(status,priority,available_at,id);
        ''')
        columns={row['name'] for row in c.execute('PRAGMA table_info(ai_jobs)')}
        for name,kind in (('error_status','INTEGER'),('started_at','TEXT'),('finished_at','TEXT')):
            if name not in columns:c.execute(f'ALTER TABLE ai_jobs ADD COLUMN {name} {kind}')


def register_handler(kind, handler, validator=None, publisher=None, failed=None):
    HANDLERS[kind] = handler
    if validator is not None:VALIDATORS[kind] = validator
    else:VALIDATORS.pop(kind, None)
    if publisher is not None:PUBLISHERS[kind]=publisher
    else:PUBLISHERS.pop(kind,None)
    if failed is not None:FAILURES[kind]=failed
    else:FAILURES.pop(kind,None)


def model_hash(config):
    semantic = {key: config.get(key) for key in ('mode', 'base_url', 'model', 'response_format')}
    semantic['key_hash'] = hashlib.sha256(config.get('api_key', '').encode()).hexdigest()
    semantic['version'] = config.get('generation_version', config.get('version', 0))
    return hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest()


def unpack(row):
    value = dict(row)
    value['payload'] = json.loads(value['payload'])
    value['result'] = json.loads(value['result']) if value['result'] else None
    return value


def live_user(c, ident):
    if ident is None:return None
    row = c.execute('SELECT id,role,enabled FROM users WHERE id=?', (ident,)).fetchone()
    return dict(row) if row and row['enabled'] else None


def project_allowed(c, user, ident):
    if not user:return False
    if user['role'] == 'admin':return True
    team = c.execute('SELECT owner_id FROM team_followups WHERE opportunity_id=?', (ident,)).fetchone()
    return bool(team and team['owner_id'] == user['id'])


def project_revision(c, ident):
    row = c.execute('SELECT owner_revision FROM team_followup_revisions WHERE opportunity_id=?', (ident,)).fetchone()
    return row['owner_revision'] if row else 0


def authorized(c, job, user):
    user = live_user(c, user['id']) if user else None
    if not user:return False
    if job['kind']=='assist' and job['payload'].get('target')=='followup' and not project_allowed(c,user,job['opportunity_id']):return False
    if job['visibility'] == 'project':return project_allowed(c, user, job['opportunity_id'])
    if job['visibility'] == 'public':return True
    return user['id'] == job['requested_by_user_id']


def context_valid(c, job):
    actor = live_user(c, job['requested_by_user_id'])
    if not actor:return False
    if job['visibility'] == 'public' and actor['role'] != 'admin':return False
    project = job['visibility'] == 'project' or (job['kind'] == 'assist' and job['payload'].get('target') == 'followup')
    if project:
        if not project_allowed(c, actor, job['opportunity_id']):return False
        if project_revision(c, job['opportunity_id']) != job['owner_revision']:return False
    if job['kind'] == 'assist' and job['payload'].get('target') in ('source', 'settings', 'model', 'user') and actor['role'] != 'admin':return False
    validator = VALIDATORS.get(job['kind'])
    return not validator or validator(job, c)


def dedupe_key(spec, signature):
    owner = None if spec['visibility'] in ('project', 'public') else spec['requested_by_user_id']
    data = [spec['kind'], spec['visibility'], owner, spec.get('context_user_id'),
            spec.get('opportunity_id'), spec.get('subscription_id'), spec.get('owner_revision'), spec['input_hash'], signature]
    return hashlib.sha256(db.dumps(data).encode()).hexdigest()


def _enqueue(c, spec, signature):
    if spec['kind'] not in HANDLERS:raise ValueError('unregistered AI job kind')
    if spec['visibility'] not in ('private', 'public', 'project') or spec['priority'] not in ('foreground', 'background'):
        raise ValueError('invalid job policy')
    job = {**spec, 'payload': spec.get('payload', {})}
    if not context_valid(c, job):raise HTTPException(403, '当前身份或项目版本不能创建此 AI 任务')
    timestamp = db.now()
    key = dedupe_key(spec, signature)
    if job['payload'].get('query_meta'):
        previous=c.execute('''SELECT * FROM ai_jobs WHERE kind=? AND requested_by_user_id=?
            AND input_hash=? AND model_hash=? ORDER BY id DESC LIMIT 1''',
            (spec['kind'],spec['requested_by_user_id'],spec['input_hash'],signature)).fetchone()
        if previous:
            if previous['status'] not in ('failed','stale') and context_valid(c,unpack(previous)):
                if spec['priority']=='foreground' and previous['status']=='queued':c.execute("UPDATE ai_jobs SET priority='foreground' WHERE id=?",(previous['id'],))
                return public_job(unpack(c.execute('SELECT * FROM ai_jobs WHERE id=?',(previous['id'],)).fetchone()),include_result=False,connection=c)
            # A fresh row preserves historical jobs and cannot revive an expired response's lease.
            key=hashlib.sha256(db.dumps([key,previous['id']]).encode()).hexdigest()
    # A new explicit submission retries a failed attempt; passive GET never restarts work.
    # Keep failed history and reuse any live successor rather than spawning duplicates.
    base_key=key
    while (previous:=c.execute('SELECT id,status FROM ai_jobs WHERE dedupe_key=?',(key,)).fetchone()) and previous['status'] in ('failed','stale'):
        if previous['status']=='failed' and spec['priority']=='background':
            return public_job(unpack(c.execute('SELECT * FROM ai_jobs WHERE id=?',(previous['id'],)).fetchone()),include_result=False,connection=c)
        key=hashlib.sha256(db.dumps([base_key,previous['id']]).encode()).hexdigest()
    c.execute('''INSERT OR IGNORE INTO ai_jobs
        (kind,requested_by_user_id,context_user_id,visibility,opportunity_id,subscription_id,
         owner_revision,input_hash,dedupe_key,priority,payload,model_hash,created_at,updated_at,available_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (spec['kind'], spec['requested_by_user_id'], spec.get('context_user_id'), spec['visibility'],
         spec.get('opportunity_id'), spec.get('subscription_id'), spec.get('owner_revision'), spec['input_hash'],
         key, spec['priority'], db.dumps(job['payload']), signature, timestamp, timestamp, timestamp))
    if spec['priority']=='foreground':
        c.execute("UPDATE ai_jobs SET priority='foreground' WHERE dedupe_key=? AND status='queued'",(key,))
    row = c.execute('SELECT * FROM ai_jobs WHERE dedupe_key=?', (key,)).fetchone()
    return public_job(unpack(row), include_result=False,connection=c)


def enqueue(spec, connection=None):
    clock.ensure_writable()
    signature = model_hash(model_config.runtime())
    if connection is not None:return _enqueue(connection, spec, signature)
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        return _enqueue(c, spec, signature)


def _duration(start,end):
    """Missing legacy timing is unknown; updated_at is never an execution clock."""
    try:
        first=datetime.fromisoformat(start) if isinstance(start,str) else start
        last=datetime.fromisoformat(end) if isinstance(end,str) else end
        if not first or not last or first.tzinfo is None or last.tzinfo is None:return None
        return round(max(0,(last-first).total_seconds()),1)
    except (TypeError,ValueError):return None


def _estimate(kind,connection):
    rows=connection.execute("""SELECT started_at,finished_at FROM ai_jobs
        WHERE kind=? AND status='succeeded' AND started_at IS NOT NULL AND finished_at IS NOT NULL
        ORDER BY id DESC LIMIT 21""",(kind,)).fetchall()
    samples=[value for row in rows if (value:=_duration(row['started_at'],row['finished_at'])) is not None and value>0]
    return (round(median(samples),1),'history_median',len(samples)) if samples else (ESTIMATE_FALLBACKS.get(kind,120),'fallback',0)


def _queue_wait(job,connection,now,estimate):
    """Simulate only anonymous durations/priority, using the actual slot and background caps."""
    config=model_config.runtime();signature=model_hash(config)
    limit=max(1,min(8,int(config.get('max_concurrency',1))))
    estimates={job['kind']:estimate}
    def seconds(kind):
        if kind not in estimates:estimates[kind]=_estimate(kind,connection)[0]
        return estimates[kind]
    running=connection.execute("SELECT kind,priority,started_at FROM ai_jobs WHERE status='running'").fetchall()
    finishes=[];background_free=0
    for row in running:
        duration=seconds(row['kind']);elapsed=_duration(row['started_at'],now)
        remaining=max(1,duration-elapsed) if elapsed is not None and elapsed<duration else min(duration,30)
        finishes.append(remaining)
        if row['priority']=='background':background_free=max(background_free,remaining)
    # Lowering concurrency never cancels work: all excess running jobs must finish first.
    slots=sorted(finishes)[max(0,len(finishes)-limit):]+[0]*max(0,limit-len(finishes))
    rows=connection.execute("""SELECT * FROM ai_jobs WHERE status='queued' AND available_at<=?
        ORDER BY CASE WHEN priority='foreground' THEN 0 ELSE 1 END,id""",(now.isoformat(),)).fetchall()
    position=0
    for row in rows:
        candidate=unpack(row)
        if candidate['kind'] not in HANDLERS or not context_valid(connection,candidate):continue
        if candidate['model_hash']!=signature and connection.execute(
            'SELECT 1 FROM ai_jobs WHERE dedupe_key=? AND id!=?',
            (dedupe_key(candidate,signature),candidate['id'])).fetchone():continue
        position+=1
        slot=min(range(limit),key=lambda index:slots[index]);wait=slots[slot]
        if row['priority']=='background':wait=max(wait,background_free)
        if row['id']==job['id']:return position,round(wait,1)
        slots[slot]=wait+seconds(row['kind'])
        if row['priority']=='background':background_free=slots[slot]
    return None,None


def job_progress(job,connection):
    now=clock.now();stage=job['status'];terminal=stage in ('succeeded','failed','stale','waiting_config')
    estimate,source,samples=_estimate(job['kind'],connection)
    queued_at=job.get('available_at') or job.get('created_at')
    elapsed=(_duration(queued_at,now) if stage=='queued' else
             _duration(job.get('started_at'),job.get('finished_at') if terminal else now))
    if elapsed is None and terminal and not job.get('attempts'):elapsed=0
    queued=_duration(queued_at,job.get('started_at') or (job.get('finished_at') if terminal else now))
    position,wait=_queue_wait(job,connection,now,estimate) if stage=='queued' else (None,None)
    exceeded=bool(stage=='running' and elapsed is not None and elapsed>=estimate)
    remaining=0 if terminal else None if exceeded else round(max(0,estimate-(elapsed or 0)),1)
    if stage=='queued':remaining=round(estimate+wait,1) if wait is not None else None
    return {'stage':stage,'elapsed_seconds':elapsed,'estimated_seconds':estimate,'remaining_seconds':remaining,
            'queue_position':position,'queued_seconds':queued,'estimated_wait_seconds':wait,'estimated':True,
            'estimate_source':source,'sample_count':samples,'estimate_exceeded':exceeded,'timing_available':elapsed is not None}


def public_job(job, include_result=True,connection=None):
    if connection is None:
        with db.connect() as c:return public_job(job,include_result,c)
    keys = ('id', 'kind', 'status', 'priority', 'opportunity_id', 'owner_revision', 'attempts', 'created_at', 'updated_at', 'started_at','finished_at','error','error_status')
    meta=job.get('payload',{}).get('query_meta',{})
    return {**{key: job.get(key) for key in keys}, 'result': job.get('result') if include_result else None,
            'server_time':db.now(),'progress':job_progress(job,connection),**{key:meta[key] for key in ('valid_until','reference_date') if key in meta}}


def get(job_id, user):
    with db.connect() as c:
        row = c.execute('SELECT * FROM ai_jobs WHERE id=?', (job_id,)).fetchone()
        if not row:raise HTTPException(404, '未找到此 AI 任务')
        job = unpack(row)
        if not authorized(c, job, user):raise HTTPException(403, '当前账号不能读取此 AI 任务')
        valid = context_valid(c, job)
    if not valid or (job['status']!='queued' and job['model_hash'] != model_hash(model_config.runtime())):
        job.update(status='stale', result=None, error='项目、权限或模型配置已变化，请重新生成')
    return public_job(job)


class Runtime:
    def __init__(self):
        self.path = Path(db.DATA).resolve()
        self.stop = Event()
        self.lock = Lock()
        self.active = {}
        self.workers = []
        self.monitor = None

    def current(self):return self.path == Path(db.DATA).resolve()

    def recover(self):
        if self.stop.is_set() or not self.current() or not clock.writable():return
        with self.lock:active = dict(self.active)
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for token, job in active.items():
                c.execute('''UPDATE ai_jobs SET lease_until=? WHERE id=? AND lease_token=? AND status='running' ''',
                          ((clock.now() + timedelta(seconds=LEASE_SECONDS)).isoformat(), job['id'], token))
            c.execute('''UPDATE ai_jobs SET status='queued',lease_token=NULL,lease_until=NULL,started_at=NULL,finished_at=NULL,available_at=?,updated_at=?
                WHERE status='running' AND lease_until<=?''', (db.now(),db.now(), db.now()))

    def claim(self):
        if self.stop.is_set() or not self.current() or not clock.writable():return None
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            config = model_config.runtime()
            limit = max(1, min(8, int(config.get('max_concurrency', 1))))
            signature = model_hash(config)
            running = c.execute("SELECT COUNT(*),COALESCE(SUM(priority='background'),0) FROM ai_jobs WHERE status='running'").fetchone()
            if running[0] >= limit:return None
            eligible = "status='queued' AND available_at<=?"
            if running[1] >= 1:eligible += " AND priority!='background'"
            ordering="CASE WHEN priority='foreground' THEN 0 ELSE 1 END,id"
            rows = c.execute('SELECT * FROM ai_jobs WHERE '+eligible+' ORDER BY '+ordering+' LIMIT 20',
                (db.now(),)).fetchall()
            for row in rows:
                job = unpack(row)
                if job['kind'] not in HANDLERS:continue
                if not context_valid(c, job):
                    c.execute("UPDATE ai_jobs SET status='stale',error=?,finished_at=?,updated_at=? WHERE id=?", ('项目、权限或模型配置已变化，请重新生成',db.now(), db.now(), job['id']))
                    continue
                if job['model_hash']!=signature:
                    key=dedupe_key(job,signature)
                    duplicate=c.execute('SELECT id FROM ai_jobs WHERE dedupe_key=? AND id!=?',(key,job['id'])).fetchone()
                    if duplicate:
                        c.execute("UPDATE ai_jobs SET status='stale',error=?,finished_at=?,updated_at=? WHERE id=?",('模型配置已更新，新的同内容任务已登记，请重新打开查看',db.now(),db.now(),job['id']))
                        continue
                    c.execute('UPDATE ai_jobs SET model_hash=?,dedupe_key=? WHERE id=?',(signature,key,job['id']))
                    job.update(model_hash=signature,dedupe_key=key)
                token = uuid4().hex
                lease = (clock.now() + timedelta(seconds=LEASE_SECONDS)).isoformat()
                timestamp=db.now()
                c.execute("UPDATE ai_jobs SET status='running',lease_token=?,lease_until=?,attempts=attempts+1,started_at=?,finished_at=NULL,updated_at=? WHERE id=? AND status='queued'",
                          (token, lease,timestamp,timestamp, job['id']))
                job.update(lease_token=token, attempts=job['attempts'] + 1, status='running',started_at=timestamp,finished_at=None)
                job['_config'] = config
                return job
        return None

    def publish(self, job, result=None, error=None, waiting=False, error_status=None):
        if not self.current() or not clock.writable():return
        status = 'waiting_config' if waiting else 'failed' if error else 'succeeded'
        if self.stop.is_set():status, result, error = 'queued', None, None
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute("SELECT id FROM ai_jobs WHERE id=? AND status='running' AND lease_token=?",(job['id'],job['lease_token'])).fetchone()
            if not current:return
            if not context_valid(c, job) or job['model_hash'] != model_hash(model_config.runtime()):
                status, result, error = 'stale', None, '项目、权限或模型配置已变化，请重新生成'
            if status=='succeeded' and job['kind'] in PUBLISHERS:
                PUBLISHERS[job['kind']](job,result,c)
            if status=='failed' and job['kind'] in FAILURES:
                FAILURES[job['kind']](job,status,c)
            c.execute('''UPDATE ai_jobs SET status=?,result=?,error=?,error_status=?,updated_at=?,finished_at=?,started_at=?,available_at=?,lease_until=NULL,lease_token=NULL
                WHERE id=? AND status='running' AND lease_token=?''',
                (status, db.dumps(result) if result is not None else None, error, error_status, db.now(),None if status=='queued' else db.now(),
                 None if status=='queued' else job.get('started_at'),db.now() if status=='queued' else job['available_at'],job['id'], job['lease_token']))

    def worker(self):
        while not self.stop.is_set() and self.current():
            job = None
            try:
                job = self.claim()
                if job is None:
                    self.stop.wait(min(SCAN_INTERVAL, 0.5))
                    continue
                with self.lock:self.active[job['lease_token']] = job
                with ai_provider.worker_context(job['_config'],self.stop):
                    result = HANDLERS[job['kind']](job)
                self.publish(job, result=result)
            except ai_provider.ProviderError as error:
                if job:self.publish(job, error=str(error), waiting=error.waiting_config,error_status=503 if error.waiting_config or error.retryable else 502)
            except HTTPException as error:
                if job:self.publish(job,error=str(error.detail),error_status=error.status_code)
            except Exception as error:
                LOG.warning('AI job %s did not complete (%s)', job['id'] if job else 'claim', type(error).__name__)
                if job:
                    try:self.publish(job, error='任务未完成，请重试；原有内容已保留')
                    except Exception:pass
                else:self.stop.wait(min(SCAN_INTERVAL, 0.5))
            finally:
                if job:
                    with self.lock:self.active.pop(job['lease_token'], None)

    def monitor_loop(self):
        while not self.stop.wait(SCAN_INTERVAL) and self.current():
            try:self.recover()
            except Exception as error:LOG.warning('AI lease check deferred (%s)', type(error).__name__)


def start():
    global STATE
    with STATE_LOCK:
        if STATE and STATE.current() and not STATE.stop.is_set():return
        STATE = Runtime()
        STATE.recover()
        for number in range(8):
            thread = Thread(target=STATE.worker, name=f'ai-worker-{number}', daemon=True)
            STATE.workers.append(thread)
            thread.start()
        STATE.monitor = Thread(target=STATE.monitor_loop, name='ai-job-leases', daemon=True)
        STATE.monitor.start()


def stop():
    global STATE
    with STATE_LOCK:
        runtime = STATE
        if not runtime:return
        runtime.stop.set()
    for thread in runtime.workers:
        if thread.is_alive():thread.join(timeout=2)
    if runtime.monitor and runtime.monitor.is_alive():runtime.monitor.join(timeout=1)
    with STATE_LOCK:
        if STATE is runtime:STATE = None
