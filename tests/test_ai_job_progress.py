"""Observable timing and estimates use an isolated SQLite queue and a fixed clock."""
from datetime import datetime,timedelta

import pytest

from app import clock,db
from test_ai_jobs import queue,spec,state


@pytest.fixture
def instant(monkeypatch):
    value=[datetime(2026,10,15,10,0,tzinfo=db.TZ)]
    monkeypatch.setattr(clock,'now',lambda:value[0])
    return value


def complete(jobs,runtime,user,instant,seconds,name,**outcome):
    submitted=jobs.enqueue(spec(user,name))
    job=runtime.claim()
    assert job['id']==submitted['id']
    instant[0]+=timedelta(seconds=seconds)
    runtime.publish(job,**(outcome or {'result':{'answer':name}}))
    return submitted['id']


def test_queue_position_and_elapsed_are_real_without_disclosing_other_users_jobs(queue,instant):
    jobs,admin,users,_=queue;jobs.register_handler('test_job',lambda job:{})
    jobs.enqueue(spec(users[1],'other-private-background','background'))
    jobs.enqueue(spec(users[1],'other-private-foreground'))
    mine=jobs.enqueue(spec(users[0],'my-query'))
    instant[0]+=timedelta(seconds=17)
    data=jobs.get(mine['id'],users[0])
    assert 'progress' in data,'public queue responses do not report progress'
    progress=data['progress']
    assert progress['stage']=='queued' and progress['queue_position']==2
    assert progress['elapsed_seconds']==17 and progress['queued_seconds']==17
    assert progress['estimated_seconds']>0 and progress['estimated'] is True
    assert progress['remaining_seconds']>=progress['estimated_seconds']
    assert 'other-private' not in str(data)
    assert not any(key in progress for key in ('payload','user_id','requested_by_user_id','title','job_ids'))


@pytest.mark.parametrize('reason',['disabled_user','owner_revision'])
def test_queue_estimate_skips_contexts_that_claim_will_reject(queue,instant,reason):
    jobs,admin,users,settings=queue;settings['max_concurrency']=1
    jobs.register_handler('test_job',lambda job:{})
    request=spec(users[0],'private-invalid-context')
    if reason=='owner_revision':
        from app.analysis import analyze
        db.upsert(analyze({'title':'控制系统采购公告','url':'https://example.com/estimate-revision',
            'source_id':'cecep','content':'公开项目正文。'},[],db.DEFAULT_SETTINGS))
        request={**spec(admin,'private-invalid-context'),'visibility':'project','opportunity_id':1,'owner_revision':0}
    old=jobs.enqueue(request)
    mine=jobs.enqueue(spec(admin,'valid-human-task'))
    if reason=='disabled_user':
        with db.connect() as c:c.execute('UPDATE users SET enabled=0 WHERE id=?',(users[0]['id'],))
    else:
        from app import team_followups
        team_followups.assign(1,team_followups.Assignee(user_id=users[0]['id']),admin)
    progress=jobs.get(mine['id'],admin)['progress']
    assert progress['queue_position']==1
    assert progress['estimated_wait_seconds']==0
    assert progress['remaining_seconds']==progress['estimated_seconds']
    assert 'private-invalid-context' not in str(progress)
    assert jobs.Runtime().claim()['id']==mine['id']
    assert state(old['id'])['status']=='stale'


def test_queue_estimate_skips_old_model_job_when_current_duplicate_will_supersede_it(queue,instant):
    jobs,admin,_,settings=queue;settings['max_concurrency']=1
    jobs.register_handler('test_job',lambda job:{})
    old=jobs.enqueue(spec(admin,'same evidence'))
    settings['version']+=1
    current=jobs.enqueue(spec(admin,'same evidence'))
    mine=jobs.enqueue(spec(admin,'separate human request'))
    progress=jobs.get(mine['id'],admin)['progress']
    assert progress['queue_position']==2 and progress['estimated_wait_seconds']==120
    assert jobs.Runtime().claim()['id']==current['id']
    assert state(old['id'])['status']=='stale'


def test_successful_same_kind_history_supplies_median_not_failed_or_unrelated_time(queue,instant):
    jobs,admin,users,_=queue;jobs.register_handler('test_job',lambda job:{})
    runtime=jobs.Runtime()
    for duration,name in [(10,'private-history-one'),(30,'private-history-two'),(200,'private-history-three')]:
        complete(jobs,runtime,users[1],instant,duration,name)
    complete(jobs,runtime,users[1],instant,900,'private-failed',error='safe failure')
    jobs.register_handler('another_kind',lambda job:{})
    other=jobs.enqueue(spec(admin,'unrelated',kind='another_kind'));job=runtime.claim()
    instant[0]+=timedelta(seconds=1000);runtime.publish(job,result={})
    mine=jobs.enqueue(spec(users[0],'new query'))
    progress=jobs.get(mine['id'],users[0])['progress']
    assert progress['estimated_seconds']==30
    assert progress['estimate_source']=='history_median' and progress['sample_count']==3
    assert 'private-history' not in str(progress) and 'unrelated' not in str(progress)


def test_queue_estimates_respect_running_slots_and_the_one_background_limit(queue,instant):
    jobs,admin,_,settings=queue;settings['max_concurrency']=2
    jobs.register_handler('test_job',lambda job:{})
    runtime=jobs.Runtime();complete(jobs,runtime,admin,instant,40,'historical duration')
    jobs.enqueue(spec(admin,'running background','background'));runtime.claim()
    background=jobs.enqueue(spec(admin,'waiting background','background'))
    first=jobs.enqueue(spec(admin,'first human'));second=jobs.enqueue(spec(admin,'second human'))
    assert jobs.get(first['id'],admin)['progress']['estimated_wait_seconds']==0
    assert jobs.get(second['id'],admin)['progress']['estimated_wait_seconds']==40
    assert jobs.get(background['id'],admin)['progress']['estimated_wait_seconds']==40
    human=runtime.claim();instant[0]+=timedelta(seconds=9)
    settings['max_concurrency']=1
    data=jobs.get(second['id'],admin)['progress']
    assert data['queue_position']==1 and data['estimated_wait_seconds']==31
    assert data['remaining_seconds']==71 and data['elapsed_seconds']==9
    assert runtime.claim() is None


@pytest.mark.parametrize('status',['succeeded','failed','waiting_config','stale'])
def test_running_and_terminal_elapsed_use_execution_timestamps_not_updated_at(queue,instant,status):
    jobs,admin,_,settings=queue;jobs.register_handler('test_job',lambda job:{})
    submitted=jobs.enqueue(spec(admin));instant[0]+=timedelta(seconds=50)
    runtime=jobs.Runtime();job=runtime.claim()
    instant[0]+=timedelta(seconds=6)
    with db.connect() as c:c.execute("UPDATE ai_jobs SET updated_at='2000-01-01T00:00:00+08:00' WHERE id=?",(job['id'],))
    running=jobs.get(job['id'],admin)
    assert running['progress']['stage']=='running' and running['progress']['elapsed_seconds']==6
    assert running['started_at']=='2026-10-15T10:00:50+08:00'
    assert running['progress']['queue_position'] is None
    if status=='stale':settings['version']+=1
    runtime.publish(job,**({'result':{}} if status in ('succeeded','stale') else {'error':'safe failure','waiting':status=='waiting_config'}))
    instant[0]+=timedelta(seconds=700)
    final=jobs.get(job['id'],admin)
    assert final['status']==status and final['progress']['stage']==status
    assert final['progress']['elapsed_seconds']==6 and final['progress']['remaining_seconds']==0
    assert final['finished_at']=='2026-10-15T10:00:56+08:00'


def test_new_retry_has_fresh_timing_and_recovered_leases_restart_attempt_timing(queue,instant):
    jobs,admin,_,_=queue;jobs.register_handler('test_job',lambda job:{})
    runtime=jobs.Runtime()
    failed=complete(jobs,runtime,admin,instant,25,'retry me',error='safe failure')
    retry=jobs.enqueue(spec(admin,'retry me'))
    assert retry['id']!=failed and retry['progress']['elapsed_seconds']==0
    assert retry['started_at'] is None and retry['finished_at'] is None
    abandoned=runtime.claim();instant[0]+=timedelta(seconds=181)
    runtime.recover()
    recovered=jobs.get(retry['id'],admin)
    assert recovered['status']=='queued' and recovered['started_at'] is None
    current=runtime.claim();instant[0]+=timedelta(seconds=13)
    runtime.publish(abandoned,result={'obsolete':'lease'})
    assert state(retry['id'])['status']=='running'
    runtime.publish(current,result={})
    assert jobs.get(retry['id'],admin)['progress']['elapsed_seconds']==13


def test_legacy_running_time_is_unknown_and_estimate_overrun_does_not_claim_zero_remaining(queue,instant):
    jobs,admin,_,_=queue;jobs.register_handler('test_job',lambda job:{})
    submitted=jobs.enqueue(spec(admin));runtime=jobs.Runtime();runtime.claim()
    with db.connect() as c:c.execute('UPDATE ai_jobs SET started_at=NULL WHERE id=?',(submitted['id'],))
    progress=jobs.get(submitted['id'],admin)['progress']
    assert progress['elapsed_seconds'] is None and progress['timing_available'] is False
    with db.connect() as c:c.execute('UPDATE ai_jobs SET started_at=? WHERE id=?',(instant[0].isoformat(),submitted['id']))
    instant[0]+=timedelta(seconds=progress['estimated_seconds']+10)
    progress=jobs.get(submitted['id'],admin)['progress']
    assert progress['stage']=='running' and progress['estimate_exceeded'] is True
    assert progress['remaining_seconds'] is None


def test_initialize_migrates_legacy_queue_without_restarting_or_losing_job(queue,instant):
    jobs,admin,_,_=queue;jobs.register_handler('test_job',lambda job:{})
    submitted=jobs.enqueue(spec(admin))
    with db.connect() as c:
        columns=[row['name'] for row in c.execute('PRAGMA table_info(ai_jobs)') if row['name'] not in ('started_at','finished_at')]
        c.execute('CREATE TABLE legacy_jobs AS SELECT '+','.join(columns)+' FROM ai_jobs')
        c.execute('DROP TABLE ai_jobs');c.execute('ALTER TABLE legacy_jobs RENAME TO ai_jobs')
    jobs.initialize();jobs.initialize()
    runtime=jobs.Runtime();job=runtime.claim();instant[0]+=timedelta(seconds=4);runtime.publish(job,result={})
    data=jobs.get(submitted['id'],admin)
    assert data['status']=='succeeded' and data['progress']['elapsed_seconds']==4
