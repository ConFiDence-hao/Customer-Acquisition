import json
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import accounts, ai_jobs, db, main, subscriptions, workbench
from app.analysis import analyze


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data');monkeypatch.setattr(db,'ROOT',tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD','testing-only-password')
    monkeypatch.setenv('SESSION_SECRET','test-session-secret-32-characters-long')
    monkeypatch.setenv('SCHEDULER_ENABLED','false')
    monkeypatch.setattr(ai_jobs,'start',lambda:None)
    monkeypatch.setattr(main.project_analysis,'backfill',lambda:None)
    with TestClient(main.app) as client:
        monkeypatch.setenv('SCHEDULER_ENABLED','true')
        with db.connect() as c:
            c.execute('UPDATE subscriptions SET deleted=1')
            admin=accounts.public(c.execute("SELECT * FROM users WHERE username='admin'").fetchone())
            uid=c.execute("INSERT INTO users(username,display_name,password_hash,role,created_at,updated_at) VALUES(?,?,?,'user',?,?)",('employee','员工',accounts.password_hash('employee-test-password'),db.now(),db.now())).lastrowid
            employee=accounts.public(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
        yield client,admin,employee


def module():
    from app import subscription_diagnosis
    return subscription_diagnosis


def topic(user,**changes):
    config={'name':'控制主题','industry':'控制','offering':'优化','keywords':['控制'],'exclude_keywords':[], 'provinces':[], 'stages':[], 'source_ids':[],**changes}
    with db.connect() as c:
        ident=c.execute('INSERT INTO subscriptions(user_id,config,enabled,created_at,updated_at) VALUES(?,?,?,?,?)',(user['id'],db.dumps(config),changes.get('enabled',True),db.now(),db.now())).lastrowid
        workbench.request_subscription_check(c,user['id'])
    return subscriptions.get(ident,user)


def notice(n,**changes):
    raw={'title':f'控制系统采购招标 {n}','url':f'https://example.com/diagnosis/{n}', 'source_id':'cecep','source_name':'公开来源','content':'采购控制系统','published_at':db.now(),**changes}
    item=analyze(raw,[],db.DEFAULT_SETTINGS)
    if 'province' in changes:item['province']=changes['province']
    db.upsert(item)
    with db.connect() as c:return db.unpack(c.execute('SELECT * FROM opportunities WHERE url=?',(raw['url'],)).fetchone())


def test_funnel_reuses_actual_matching_and_previews_single_removal(setup):
    _,admin,_=setup
    t=topic(admin,source_ids=['cecep'],provinces=['江苏'],stages=['招标'],exclude_keywords=['维修'])
    items=[notice(1,province='江苏'),notice(2,source_id='bjx',province='江苏'),notice(3,province='浙江'),notice(4,province='江苏',title='控制系统中标结果'),notice(5,province='江苏',title='厂房采购招标',content='土建'),notice(6,province='江苏',title='控制系统维修招标')]
    result=module().collect(t['id'],admin)
    assert [s['remaining'] for s in result['funnel']]==[5,4,3,2,1]
    assert result['counts']['matching']==sum(subscriptions.match(i,t) is not None for i in items)==1
    assert all(s['without_condition']==2 for s in result['funnel'])


def test_body_hit_and_observed_source_are_real_matches(setup):
    _,admin,_=setup;t=topic(admin,source_ids=['bjx'])
    item=notice(1,title='设备招标',content='采购控制系统',observed_source_ids=['bjx'])
    decision=subscriptions.match_decision(item,t)
    assert decision['allowed'] and decision['hits']==['控制']
    assert module().collect(t['id'],admin)['counts']['matching']==1


def test_other_theme_delivery_pending_association_is_distinct_from_zero_matches(setup):
    _,admin,_=setup;t=topic(admin);other=topic(admin,name='旧主题');a=notice(1);b=notice(2);notice(3)
    with db.connect() as c:
        for item,associations in [(a,[{'id':t['id'],'name':t['name']}]),(b,[{'id':other['id'],'name':other['name']}])]:
            c.execute('INSERT INTO pushes(user_id,opportunity_id,payload,pushed_at,read_at) VALUES(?,?,?,?,?)',(admin['id'],item['id'],db.dumps({'matched_topics':associations,'note':'PRIVATE PUSH BODY'}),db.now(),db.now()))
        c.execute('INSERT INTO automations(user_id,last_run,last_count) VALUES(?,?,0)',(admin['id'],db.now()))
    result=module().collect(t['id'],admin)
    assert result['counts']=={'matching':3,'undelivered':1,'delivered_for_topic':1,'delivered_by_other_topics':1,'pending_topic_association':1,'push_filtered':0}
    assert 'no_matches' not in result['reasons']
    assert 'PRIVATE PUSH BODY' not in json.dumps(result)


def test_pause_first_check_and_source_health_are_separate(setup,monkeypatch):
    _,admin,_=setup;t=topic(admin,enabled=False,source_ids=['cecep']);notice(1)
    monkeypatch.setenv('SCHEDULER_ENABLED','false')
    monkeypatch.setattr(main.collectors if hasattr(main,'collectors') else __import__('app.collectors',fromlist=['source_rows']),'source_rows',lambda:[{'id':'cecep','name':'公开来源','enabled':False,'adapter_available':False,'status':'尚未接入','last_error':'SECRET RAW ERROR'}])
    result=module().collect(t['id'],admin)
    assert {'subscription_paused','scheduler_paused','first_check_pending','source_unavailable'}<=set(result['reasons'])
    assert result['counts']['matching']==1
    assert 'SECRET RAW ERROR' not in json.dumps(result)
    with db.connect() as c:c.execute('INSERT INTO automations(user_id,enabled) VALUES(?,0)',(admin['id'],))
    assert 'automation_paused' in module().collect(t['id'],admin)['reasons']


def test_admin_uses_topic_owner_delivery_and_regular_user_cannot_cross_accounts(setup):
    client,admin,employee=setup;t=topic(employee);a=notice(1)
    with db.connect() as c:
        c.execute('INSERT INTO pushes(user_id,opportunity_id,payload,pushed_at) VALUES(?,?,?,?)',(employee['id'],a['id'],db.dumps({'matched_topics':[{'id':t['id'],'name':t['name']}]}),db.now()))
        c.execute('INSERT INTO followups(user_id,opportunity_id,status,note,updated_at) VALUES(?,?,?,?,?)',(employee['id'],a['id'],'跟进中','EMPLOYEE PRIVATE NOTE',db.now()))
    assert module().collect(t['id'],admin)['counts']['delivered_for_topic']==1
    foreign=topic(admin)
    with pytest.raises(HTTPException) as error:module().collect(foreign['id'],employee)
    assert error.value.status_code==404
    assert client.post('/api/login',json={'username':'employee','password':'employee-test-password'}).status_code==200
    assert client.get(f"/api/subscriptions/{foreign['id']}/diagnosis").status_code==404
    response=client.get(f"/api/subscriptions/{t['id']}/diagnosis")
    assert response.status_code==200 and 'EMPLOYEE PRIVATE NOTE' not in response.text and 'read_at' not in response.text


def test_explanation_payload_safe_and_configuration_change_invalidates_task(setup):
    client,admin,employee=setup;t=topic(employee);notice(1)
    diag=module();diag.register()
    job=diag.enqueue_explanation(t['id'],admin)
    with db.connect() as c:
        stored=ai_jobs.unpack(c.execute('SELECT * FROM ai_jobs WHERE id=?',(job['id'],)).fetchone())
        assert stored['context_user_id']==employee['id'] and stored['visibility']=='private'
        assert 'read_at' not in db.dumps(stored['payload']) and 'note' not in stored['payload']['diagnosis']
        assert diag.valid_context(stored,c)
        config={**t,'keywords':['不存在']};c.execute('UPDATE subscriptions SET config=? WHERE id=?',(db.dumps(config),t['id']))
        assert not diag.valid_context(stored,c)
    assert ai_jobs.get(job['id'],admin)['status']=='stale'
    with pytest.raises(HTTPException):ai_jobs.get(job['id'],employee)


def test_diagnosis_routes_return_verified_statistics_and_queue_only(setup):
    client,admin,_=setup;t=topic(admin);notice(1)
    client.post('/api/login',json={'username':'admin','password':'testing-only-password'})
    response=client.get(f"/api/subscriptions/{t['id']}/diagnosis")
    assert response.status_code==200 and response.json()['counts']['matching']==1
    queued=client.post(f"/api/subscriptions/{t['id']}/diagnosis/explain")
    assert queued.status_code==200 and queued.json()['kind']=='diagnosis'
    assert subscriptions.get(t['id'],admin)['keywords']==['控制']


def test_failed_identical_explanation_can_be_retried_and_live_duplicate_still_dedupes(setup):
    _,admin,_=setup;t=topic(admin);notice(1);diag=module();diag.register()
    old=diag.enqueue_explanation(t['id'],admin)
    with db.connect() as c:c.execute("UPDATE ai_jobs SET status='failed',error='temporary failure' WHERE id=?",(old['id'],))
    fresh=diag.enqueue_explanation(t['id'],admin)
    assert fresh['id']!=old['id'] and fresh['status']=='queued'
    assert diag.enqueue_explanation(t['id'],admin)['id']==fresh['id']
