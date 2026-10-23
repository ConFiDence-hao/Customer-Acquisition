"""Persistent queue scheduling, ownership and lease recovery use real SQLite and threads."""
import importlib
import importlib.util
from threading import Event, Lock
from time import monotonic, sleep

import pytest
from fastapi import HTTPException

from app import accounts, db, model_config


def jobs_module():
    assert importlib.util.find_spec('app.ai_jobs') is not None, 'persistent shared AI queue is not implemented'
    return importlib.import_module('app.ai_jobs')


@pytest.fixture
def queue(tmp_path,monkeypatch):
    if importlib.util.find_spec('app.ai_jobs') is None:
        yield None,None,[],{}
        return
    module=jobs_module()
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    monkeypatch.setattr(db,'ROOT',tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD','jobs-test-password')
    monkeypatch.setenv('SESSION_SECRET','jobs-test-secret-32-characters-long')
    settings={'mode':'local','base_url':'http://test.invalid/v1','model':'test-model','api_key':'',
              'response_format':'json_object','version':1,'max_concurrency':2}
    monkeypatch.setattr(model_config,'runtime',lambda:dict(settings))
    monkeypatch.setattr(module,'SCAN_INTERVAL',0.03)
    db.initialize()
    module.initialize()
    with db.connect() as c:
        admin=accounts.public(c.execute('SELECT * FROM users').fetchone())
        users=[]
        for name in ['alice','bobby']:
            ident=c.execute("INSERT INTO users(username,display_name,password_hash,role,created_at,updated_at) VALUES(?,?,?,'user',?,?)",
                (name,name,accounts.password_hash('jobs-test-user-password'),db.now(),db.now())).lastrowid
            users.append(accounts.public(c.execute('SELECT * FROM users WHERE id=?',(ident,)).fetchone()))
    yield module,admin,users,settings
    module.stop()
    module.HANDLERS.clear()


def spec(user,hash='one',priority='foreground',kind='test_job'):
    return {'kind':kind,'requested_by_user_id':user['id'],'context_user_id':user['id'],'visibility':'private',
            'opportunity_id':None,'subscription_id':None,'owner_revision':None,'input_hash':hash,
            'priority':priority,'payload':{'synthetic':hash}}


def wait_until(check,timeout=3):
    end=monotonic()+timeout
    while monotonic()<end:
        value=check()
        if value:return value
        sleep(0.01)
    pytest.fail('queue did not reach the expected observable state')


def state(ident):
    with db.connect() as c:return dict(c.execute('SELECT * FROM ai_jobs WHERE id=?',(ident,)).fetchone())


def test_publication_side_effects_require_current_lease_and_valid_version(queue):
    module,admin,users,settings=queue
    jobs_module()
    assert hasattr(module,'PUBLISHERS'), 'queue cannot atomically publish validated report side effects'
    published=[]
    module.register_handler('test_job',lambda job:{'safe':'result'},publisher=lambda job,result,c:published.append(job['id']))
    submitted=module.enqueue(spec(admin))
    runtime=module.Runtime();job=runtime.claim()
    with db.connect() as c:c.execute("UPDATE ai_jobs SET lease_token='new-token' WHERE id=?",(submitted['id'],))
    runtime.publish(job,result={'safe':'old'})
    assert published==[]
    with db.connect() as c:c.execute('UPDATE ai_jobs SET lease_token=? WHERE id=?',(job['lease_token'],submitted['id']))
    settings['version']=2
    runtime.publish(job,result={'safe':'obsolete-config'})
    assert published==[] and state(submitted['id'])['status']=='stale'


def test_manual_request_promotes_existing_queued_background_task(queue):
    module,admin,users,settings=queue
    jobs_module();module.register_handler('test_job',lambda job:{})
    first=module.enqueue(spec(admin,priority='background'))
    same=module.enqueue(spec(admin,priority='foreground'))
    assert same['id']==first['id'] and same['priority']=='foreground'
    assert state(first['id'])['priority']=='foreground'


def test_failed_background_work_waits_for_explicit_retry_or_changed_input(queue):
    module,admin,_,_=queue;module.register_handler('test_job',lambda job:{})
    first=module.enqueue(spec(admin,priority='background'))
    with db.connect() as c:c.execute("UPDATE ai_jobs SET status='failed',error='format failure' WHERE id=?",(first['id'],))
    for _ in range(3):
        repeated=module.enqueue(spec(admin,priority='background'))
        assert repeated['id']==first['id'] and repeated['status']=='failed'
    with db.connect() as c:assert c.execute('SELECT COUNT(*) FROM ai_jobs').fetchone()[0]==1
    manual=module.enqueue(spec(admin,priority='foreground'))
    assert manual['id']!=first['id'] and manual['status']=='queued'
    changed=module.enqueue(spec(admin,hash='changed evidence',priority='background'))
    assert changed['id'] not in (first['id'],manual['id']) and changed['status']=='queued'


def test_queued_job_uses_latest_saved_model_before_it_starts(queue):
    module,admin,users,settings=queue
    jobs_module();seen=[]
    from app import ai_provider
    module.register_handler('test_job',lambda job:seen.append(ai_provider.current_config()['model']) or {'answer':'latest'})
    submitted=module.enqueue(spec(admin))
    settings.update(model='replacement-model',version=2)
    assert module.get(submitted['id'],admin)['status']=='queued'
    module.start();wait_until(lambda:state(submitted['id'])['status']=='succeeded')
    assert seen==['replacement-model']


def test_default_two_concurrent_jobs_and_at_most_one_background(queue):
    jobs,admin,users,settings=queue
    assert jobs is not None, 'persistent queue is not implemented'
    gate=Event();lock=Lock();active=[];peak=[];started=[]
    def handle(job):
        with lock:
            active.append(job['priority']);started.append(job['input_hash']);peak.append(list(active))
        gate.wait(2)
        with lock:active.remove(job['priority'])
        return {'answer':job['input_hash']}
    jobs.register_handler('test_job',handle)
    ids=[jobs.enqueue(spec(users[0],name,priority))['id'] for name,priority in
         [('bg-1','background'),('bg-2','background'),('fg-1','foreground'),('fg-2','foreground')]]
    jobs.start()
    try:
        wait_until(lambda:len(started)>=2)
        assert max(map(len,peak))==2
        gate.set()
        wait_until(lambda:all(state(ident)['status']=='succeeded' for ident in ids))
        assert all(values.count('background')<=1 for values in peak)
    finally:gate.set()


def test_single_execution_prioritizes_foreground_even_before_old_background_work(queue):
    jobs,admin,users,settings=queue
    assert jobs is not None, 'persistent queue is not implemented'
    settings['max_concurrency']=1
    visited=[]
    jobs.register_handler('test_job',lambda job:(visited.append(job['input_hash']),{'answer':'ok'})[1])
    background=jobs.enqueue(spec(users[0],'background','background'))['id']
    foreground=jobs.enqueue(spec(users[0],'foreground'))['id']
    jobs.start()
    wait_until(lambda:state(background)['status']=='succeeded' and state(foreground)['status']=='succeeded')
    assert visited==['foreground','background']
    jobs.stop();visited.clear()
    old=jobs.enqueue(spec(users[0],'aged','background'))['id']
    with db.connect() as c:c.execute('UPDATE ai_jobs SET created_at=? WHERE id=?',('2000-01-01T00:00:00+08:00',old))
    fresh=jobs.enqueue(spec(users[0],'fresh'))['id']
    jobs.start()
    wait_until(lambda:state(fresh)['status']=='succeeded' and state(old)['status']=='succeeded')
    assert visited==['fresh','aged']


@pytest.mark.parametrize('limit',[1,2,4,8])
def test_foreground_fifo_precedes_aged_background_at_every_concurrency(queue,limit):
    jobs,admin,_,settings=queue;settings['max_concurrency']=limit
    jobs.register_handler('test_job',lambda job:{})
    background=jobs.enqueue(spec(admin,'old background','background'))['id']
    with db.connect() as c:c.execute('UPDATE ai_jobs SET created_at=? WHERE id=?',('2000-01-01T00:00:00+08:00',background))
    foreground=[jobs.enqueue(spec(admin,name))['id'] for name in ('first human','second human')]
    runtime=jobs.Runtime();claimed=[]
    for _ in range(3):
        job=runtime.claim();claimed.append(job['id']);runtime.publish(job,result={})
    assert claimed==foreground+[background]


def test_human_arrival_keeps_running_background_lease_and_uses_next_free_slot(queue):
    jobs,admin,_,settings=queue;settings['max_concurrency']=2
    jobs.register_handler('test_job',lambda job:{})
    running_id=jobs.enqueue(spec(admin,'already running','background'))['id']
    runtime=jobs.Runtime();running=runtime.claim()
    jobs.enqueue(spec(admin,'second background','background'))
    first=jobs.enqueue(spec(admin,'human one'))['id'];second=jobs.enqueue(spec(admin,'human two'))['id']
    assert runtime.claim()['id']==first
    assert runtime.claim() is None
    assert state(running_id)['lease_token']==running['lease_token'] and state(running_id)['attempts']==1
    assert jobs.enqueue(spec(admin,'already running'))['id']==running_id
    runtime.publish(running,result={'completed':'once'})
    assert runtime.claim()['id']==second


def test_duplicate_submission_reuses_job_without_duplicate_provider_work(queue):
    jobs,admin,users,settings=queue
    assert jobs is not None, 'persistent queue is not implemented'
    visits=[]
    jobs.register_handler('test_job',lambda job:(visits.append(job['id']),{'answer':'ok'})[1])
    first=jobs.enqueue(spec(users[0]))
    assert jobs.enqueue(spec(users[0]))['id']==first['id']
    jobs.start();wait_until(lambda:state(first['id'])['status']=='succeeded')
    assert jobs.enqueue(spec(users[0]))['id']==first['id']
    assert visits==[first['id']]


def test_private_task_results_do_not_cross_accounts_or_grant_admin_override(queue):
    jobs,admin,users,settings=queue
    assert jobs is not None, 'persistent queue is not implemented'
    jobs.register_handler('test_job',lambda job:{'answer':'private-result-token'})
    ident=jobs.enqueue(spec(users[0]))['id'];jobs.start()
    wait_until(lambda:state(ident)['status']=='succeeded')
    assert jobs.get(ident,users[0])['result']=={'answer':'private-result-token'}
    for user in [admin,users[1]]:
        with pytest.raises(HTTPException) as error:jobs.get(ident,user)
        assert error.value.status_code==403
    with db.connect() as c:c.execute('UPDATE users SET enabled=0 WHERE id=?',(users[0]['id'],))
    with pytest.raises(HTTPException):jobs.get(ident,users[0])


def test_expired_process_lease_is_recovered_after_restart(queue):
    jobs,admin,users,settings=queue
    assert jobs is not None, 'persistent queue is not implemented'
    visits=[]
    jobs.register_handler('test_job',lambda job:(visits.append(job['id']),{'answer':'recovered'})[1])
    ident=jobs.enqueue(spec(users[0]))['id']
    with db.connect() as c:c.execute("UPDATE ai_jobs SET status='running',lease_until=?,lease_token='old-process' WHERE id=?",('2000-01-01T00:00:00+08:00',ident))
    jobs.start();wait_until(lambda:state(ident)['status']=='succeeded')
    assert jobs.get(ident,users[0])['result']=={'answer':'recovered'} and visits==[ident]


def test_model_change_during_work_cannot_publish_an_old_result(queue):
    jobs,admin,users,settings=queue
    assert jobs is not None, 'persistent queue is not implemented'
    gate=Event();started=Event()
    def handle(job):started.set();gate.wait(2);return {'answer':'obsolete-model-output'}
    jobs.register_handler('test_job',handle)
    ident=jobs.enqueue(spec(users[0]))['id'];jobs.start()
    try:
        assert started.wait(2)
        settings.update(model='replacement-model',version=2)
        gate.set();wait_until(lambda:state(ident)['status']=='stale')
        assert jobs.get(ident,users[0]).get('result') is None
    finally:gate.set()


def test_handler_network_work_does_not_hold_a_sqlite_write_transaction(queue):
    jobs,admin,users,settings=queue
    assert jobs is not None, 'persistent queue is not implemented'
    gate=Event();started=Event()
    def handle(job):started.set();gate.wait(2);return {'answer':'done'}
    jobs.register_handler('test_job',handle)
    ident=jobs.enqueue(spec(users[0]))['id'];jobs.start()
    try:
        assert started.wait(2)
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT INTO audit(created_at,action,target,detail) VALUES(?,?,?,?)',(db.now(),'queue-test','','independent write'))
        gate.set();wait_until(lambda:state(ident)['status']=='succeeded')
    finally:gate.set()


def test_authentication_failure_waits_for_config_instead_of_hammering_provider(queue):
    jobs,admin,users,settings=queue
    assert jobs is not None, 'persistent queue is not implemented'
    provider=importlib.import_module('app.ai_provider')
    visits=[]
    def handle(job):
        visits.append(job['id'])
        raise provider.ProviderError('authentication','safe authentication error',waiting_config=True)
    jobs.register_handler('test_job',handle)
    ident=jobs.enqueue(spec(users[0]))['id'];jobs.start()
    wait_until(lambda:state(ident)['status']=='waiting_config')
    sleep(0.12)
    assert visits==[ident]
    assert jobs.get(ident,users[0])['error']=='safe authentication error'


def test_project_job_is_shared_only_with_admin_and_current_owner_and_revision(queue):
    from app import team_followups
    from app.analysis import analyze
    jobs,admin,users,settings=queue
    assert jobs is not None, 'persistent queue is not implemented'
    db.upsert(analyze({'title':'智慧焚烧控制项目招标','url':'https://example.com/job-project',
        'source_id':'cecep','content':'公开项目证据。'},[],db.DEFAULT_SETTINGS))
    assigned=team_followups.assign(1,team_followups.Assignee(user_id=users[0]['id']),admin)
    revision=assigned['team_followup']['owner_revision']
    jobs.register_handler('test_project',lambda job:{'answer':'shared-project-token'})
    request={**spec(admin,kind='test_project'),'visibility':'project','opportunity_id':1,
             'context_user_id':users[0]['id'],'owner_revision':revision}
    ident=jobs.enqueue(request)['id']
    assert jobs.enqueue({**request,'requested_by_user_id':users[0]['id']})['id']==ident
    jobs.start();wait_until(lambda:state(ident)['status']=='succeeded')
    assert jobs.get(ident,admin)['result']==jobs.get(ident,users[0])['result']=={'answer':'shared-project-token'}
    with pytest.raises(HTTPException):jobs.get(ident,users[1])
    team_followups.assign(1,team_followups.Assignee(user_id=users[1]['id']),admin)
    with pytest.raises(HTTPException):jobs.get(ident,users[0])
    assert jobs.get(ident,admin)['result'] is None
    assert jobs.get(ident,users[1])['result'] is None


def test_reducing_concurrency_lets_inflight_work_finish_before_claiming_more(queue):
    jobs,admin,users,settings=queue
    assert jobs is not None, 'persistent queue is not implemented'
    gates={'one':Event(),'two':Event(),'three':Event()};gates['three'].set()
    started=[]
    def handle(job):
        started.append(job['input_hash'])
        gates[job['input_hash']].wait(2)
        return {'answer':job['input_hash']}
    jobs.register_handler('test_job',handle)
    first=jobs.enqueue(spec(users[0],'one'))['id']
    second=jobs.enqueue(spec(users[0],'two'))['id']
    jobs.start()
    try:
        wait_until(lambda:len(started)==2)
        settings['max_concurrency']=1
        third=jobs.enqueue(spec(users[0],'three'))['id']
        gates['one'].set();wait_until(lambda:state(first)['status']=='succeeded')
        sleep(0.08)
        assert started==['one','two'] and state(third)['status']=='queued'
        gates['two'].set();wait_until(lambda:state(third)['status']=='succeeded')
        assert started==['one','two','three']
    finally:
        for gate in gates.values():gate.set()
