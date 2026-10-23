"""Public queue requests derive identities and visibility on the server."""
from time import monotonic, sleep

from app import assist, db
from test_team_followups import client, login, notice, claim, assign


def result(client,ident):
    deadline=monotonic()+3
    while monotonic()<deadline:
        response=client.get(f'/api/ai-jobs/{ident}')
        assert response.status_code==200,response.text
        data=response.json()
        if data['status'] in ('succeeded','failed','stale','waiting_config'):return data
        sleep(0.02)
    raise AssertionError('queued API request did not finish')


def test_assist_queue_derives_private_identity_and_removes_secret_fields(client,monkeypatch):
    seen=[]
    def generate(context,keys):
        seen.append(context)
        return {'fields':[{'field':'name','value':'控制采购'}],'explanation':'测试草稿'}
    monkeypatch.setattr(assist,'generate',generate)
    login(client,'alice')
    body={'kind':'assist','request':{'target':'subscription','instruction':'关注控制系统采购',
          'current':{'password':'private-password-token','api_key':'private-api-token','note':'private-note-token'}}}
    submitted=client.post('/api/ai-jobs',json=body)
    assert submitted.status_code==200,submitted.text
    ident=submitted.json()['id']
    assert result(client,ident)['result']['fields']=={'name':'控制采购'}
    assert all('private-' not in str(context) for context in seen)
    login(client,'admin')
    assert client.get(f'/api/ai-jobs/{ident}').status_code==403
    assert client.post('/api/ai-jobs',json={**body,'visibility':'public','requested_by_user_id':1}).status_code==422


def test_followup_ai_requires_current_project_rights_and_exact_owner_revision(client,monkeypatch):
    ident=notice('assist-project')
    monkeypatch.setattr(assist,'generate',lambda *args:{'fields':[{'field':'note','value':'核实采购范围'}],'explanation':'草稿'})
    alice=login(client,'alice')
    body={'kind':'assist','request':{'target':'followup','page':'followup-form','instruction':'总结核实计划','opportunity_id':ident,'owner_revision':0}}
    assert client.post('/api/ai-jobs',json=body).status_code==403
    owned=claim(client,ident)
    revision=owned['team_followup']['owner_revision']
    assert client.post('/api/ai-jobs',json=body).status_code==409
    body['request']['owner_revision']=revision
    submitted=client.post('/api/ai-jobs',json=body)
    assert submitted.status_code==200,submitted.text
    job=submitted.json()['id']
    assert result(client,job)['status']=='succeeded'
    bobby=login(client,'bobby')
    login(client,'admin');assign(client,ident,bobby)
    login(client,'alice')
    assert client.get(f'/api/ai-jobs/{job}').status_code==403
    assert client.post(f'/api/ai-jobs/{job}/retry').status_code==403


def test_model_config_uses_persistent_versions_and_accepts_concurrency_without_busy_lock(client):
    from app import ai
    original=client.get('/api/model-config').json()
    assert original.get('max_concurrency')==1
    assert ai.LOCK.acquire(blocking=False)
    try:
        response=client.put('/api/model-config',json={'mode':'local','max_concurrency':3,'auto_analyze':False})
        assert response.status_code==200,response.text
    finally:ai.LOCK.release()
    first=response.json()
    second=client.put('/api/model-config',json={'mode':'disabled','max_concurrency':1}).json()
    assert first['max_concurrency']==3 and second['max_concurrency']==1
    assert second['version']>first['version']
    assert client.put('/api/model-config',json={'mode':'local','max_concurrency':9}).status_code==422
    with db.connect() as c:
        assert 'api_key' not in c.execute('SELECT value FROM model_settings').fetchone()[0]
