import json
import pytest
from fastapi.testclient import TestClient
from app import db, main
from app.analysis import analyze

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'testing-only-password')
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-32-characters-long')
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')
    main.FAILURES.clear()
    with TestClient(main.app) as c:
        yield c

def login(c, username='admin', password='testing-only-password'):
    return c.post('/api/login', json={'username':username,'password':password})

def create_user(c, name='alice'):
    return c.post('/api/users', json={'username':name,'display_name':name,'password':'a-strong-test-password','role':'user'} )

def topic(**changes):
    return dict(name='工业智能',industry='工业',offering='智能控制解决方案',keywords=['智能控制'],exclude_keywords=['食堂'],provinces=[],stages=['招标'],source_ids=['cecep'],enabled=True,**changes)

def notice():
    db.upsert(analyze({'title':'工业智能控制系统招标','url':'https://new.ebidding.cecep.cn/t.html','source_id':'cecep','source_name':'中节能','source_kind':'集团采购','published_at':db.now(),'content':'采购人：某工业企业\n投标截止时间：2099-12-10 12:00'},[],db.DEFAULT_SETTINGS))

def test_accounts_required_and_admin_permissions(client):
    assert client.post('/api/login',json={'password':'testing-only-password'}).status_code==422
    assert login(client).status_code==200
    assert client.get('/api/me').json()['role']=='admin'
    r=create_user(client); assert r.status_code==200
    assert 'password' not in r.text and 'hash' not in r.text
    client.post('/api/logout'); assert login(client,'alice','a-strong-test-password').status_code==200
    assert client.get('/api/me').json()['role']=='user'
    for method,url,body in [('GET','/api/users',None),('PUT','/api/settings',db.DEFAULT_SETTINGS),('POST','/api/collect',None),('GET','/api/model-config',None)]:
        assert client.request(method,url,json=body).status_code==403

def test_disable_logout_password_and_last_admin(client):
    login(client); uid=create_user(client).json()['id']; admin_cookie=client.cookies.get('radar_session')
    assert client.patch('/api/users/1',json={'enabled':False}).status_code==409
    login(client,'alice','a-strong-test-password'); old_cookie=client.cookies.get('radar_session')
    assert client.put('/api/me/password',json={'current_password':'a-strong-test-password','new_password':'replacement-pass-123'}).status_code==200
    assert client.get('/api/me').status_code==401
    client.cookies.set('radar_session',admin_cookie)
    assert client.patch(f'/api/users/{uid}',json={'enabled':False}).status_code==200
    client.cookies.set('radar_session',old_cookie)
    assert client.get('/api/me').status_code==401
    assert login(client,'alice','replacement-pass-123').status_code==401

def test_topics_preview_ownership_and_followup_isolation(client):
    login(client); create_user(client); notice()
    sid=client.post('/api/subscriptions',json=topic()).json()['id']
    assert client.post('/api/subscriptions/preview',json=topic()).json()['total']==1
    rec=client.get(f'/api/recommendations?subscription_id={sid}').json()
    assert rec['total']==1 and rec['items'][0]['topic_score']>=50
    client.patch('/api/opportunities/1',json={'status':'跟进中','note':'admin private'})
    login(client,'alice','a-strong-test-password')
    personal_topics=client.get('/api/subscriptions').json()['items']
    assert len(personal_topics)==4 and all(t['user_id']==client.get('/api/me').json()['id'] for t in personal_topics)
    assert sid not in {t['id'] for t in personal_topics}
    assert client.put(f'/api/subscriptions/{sid}',json=topic()).status_code==404
    assert client.get(f'/api/recommendations?subscription_id={sid}').status_code==404
    assert client.get('/api/opportunities/1').json()['note']==''
    assert 'admin private' not in client.get('/api/export').text
    client.patch('/api/opportunities/1',json={'status':'已联系','note':'alice private'})
    assert client.get('/api/opportunities?status=已联系').json()['total']==1
    login(client)
    assert client.get('/api/opportunities/1').json()['note']=='admin private'
    assert 'alice private' not in client.get('/api/export').text

def test_model_config_encrypted_and_validated(client):
    login(client)
    body={'mode':'external','base_url':'https://api.example.com/v1','model':'test','api_key':'secret-api-key-12345','response_format':'json_object','auto_analyze':False}
    r=client.put('/api/model-config',json=body); assert r.status_code==200
    assert 'secret-api-key' not in r.text and r.json()['has_key']
    assert 'secret-api-key' not in client.get('/api/model-config').text
    with db.connect() as c:
        assert 'secret-api-key' not in c.execute('SELECT value FROM model_settings').fetchone()[0]
    for url in ['http://api.example.com/v1','https://127.0.0.1/v1','https://169.254.169.254/v1','https://user:pass@api.example.com/v1','https://api.example.com/v1?key=oops']:
        assert client.put('/api/model-config',json={**body,'base_url':url}).status_code==422
    assert client.put('/api/model-config',json={**body,'api_key':''}).json()['has_key']
    assert client.put('/api/model-config',json={**body,'api_key':'','clear_key':True}).json()['has_key'] is False

def test_bootstrap_does_not_reset_password_or_duplicate_users(client):
    login(client)
    client.put('/api/me/password',json={'current_password':'testing-only-password','new_password':'changed-password-123'})
    db.initialize()
    assert login(client,password='testing-only-password').status_code==401
    assert login(client,password='changed-password-123').status_code==200
    assert len(client.get('/api/users').json()['items'])==1

@pytest.mark.parametrize('owner',['admin','alice'])
def test_personal_subscription_expands_source_scoped_public_search(client,owner):
    from app import collectors
    login(client)
    if owner!='admin':
        assert create_user(client).status_code==200
        assert login(client,'alice','a-strong-test-password').status_code==200
    response=client.post('/api/subscriptions',json={**topic(),'keywords':['个人专属芯片'],
                                                 'exclude_keywords':['全局水泵']})
    assert response.status_code==200
    settings={**db.DEFAULT_SETTINGS,'focus_keywords':['全局水泵']}
    prefs=collectors.collection_preferences(settings,'cecep')
    assert '个人专属芯片' in prefs['search_keywords']
    class ApiSource:
        def __init__(self):self.queries=[]
        def post(self,url,payload):
            self.queries.append(payload['condition'][0]['equal'])
            return {'content':{'result':{'records':[]}}}
    source=ApiSource()
    for cycle in range(2):
        collectors.cecep_candidates(source,{**prefs,'collection_cycle':cycle})
    assert '个人专属芯片' in source.queries
    assert '全局水泵' in source.queries and '垃圾焚烧' in source.queries
    for source_id in ['cecep','everbright']:
        scoped=collectors.collection_preferences(settings,source_id)
        assert collectors.relevant('个人专属芯片采购公告',scoped)==(source_id=='cecep')
        assert collectors.relevant('全局水泵采购公告',scoped)


@pytest.mark.parametrize('owner',['admin','alice'])
def test_personal_subscription_cannot_bypass_public_exclusions(client,owner):
    from app import collectors
    login(client)
    if owner!='admin':
        assert create_user(client).status_code==200
        assert login(client,'alice','a-strong-test-password').status_code==200
    assert client.post('/api/subscriptions',json={**topic(),'keywords':['食堂'],
                                                'exclude_keywords':[]}).status_code==200
    prefs=collectors.collection_preferences(db.DEFAULT_SETTINGS,'cecep')
    assert not collectors.relevant('垃圾焚烧厂食堂招标公告',prefs)
    assert collectors.relevant('垃圾焚烧烟气提标招标公告',prefs)


@pytest.mark.parametrize('owner',['admin','alice'])
def test_personal_subscription_still_matches_and_pushes_public_notices(client,owner):
    from app import workbench
    login(client)
    if owner!='admin':
        assert create_user(client).status_code==200
        assert login(client,'alice','a-strong-test-password').status_code==200
    notice()
    response=client.post('/api/subscriptions',json=topic())
    assert response.status_code==200
    sid=response.json()['id']
    assert client.post('/api/subscriptions/preview',json=topic()).json()['total']==1
    assert client.get(f'/api/recommendations?subscription_id={sid}').json()['total']==1
    workbench.dispatch_due(force=True)
    pushed=client.get('/api/pushes').json()
    assert pushed['total']==1 and pushed['items'][0]['subscription_id']==sid

def test_external_model_protocol_and_business_context(client,monkeypatch):
    from app import ai, model_config
    login(client)
    client.put('/api/model-config',json={'mode':'external','base_url':'https://api.example.com/v1','model':'external-test','api_key':'private-key','response_format':'json_object','auto_analyze':False})
    monkeypatch.setattr(model_config,'validate_endpoint',lambda *a,**kw:None)
    recorded={}
    import httpx
    def handle(request):
        recorded.update(url=str(request.url),headers=dict(request.headers),json=json.loads(request.content))
        recorded['headers']['Authorization']=request.headers['Authorization']
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'summary':'测试','fit':'工业控制','questions':[],'suggested_actions':[]})}}]})
    async_client=httpx.AsyncClient
    monkeypatch.setattr(ai.ai_provider.httpx,'AsyncClient',lambda **kw:async_client(transport=httpx.MockTransport(handle),**kw))
    result=ai.explain({'title':'工业升级','content':'公开公告','business_context':{'industry':'工业','offering':'控制系统'}})
    assert recorded['url']=='https://api.example.com/v1/chat/completions'
    assert recorded['headers']['Authorization']=='Bearer private-key'
    assert recorded['json']['response_format']=={'type':'json_object'}
    assert 'chat_template_kwargs' not in recorded['json']
    assert '控制系统' in recorded['json']['messages'][1]['content']
    assert result['model']=='external-test'

def test_secrets_not_echoed_in_validation_errors(client):
    login(client)
    r=client.put('/api/model-config',json={'mode':'external','api_key':'x'*1001})
    assert r.status_code==422 and 'x'*100 not in r.text

def test_topic_analysis_saved_per_user_and_invalidated_by_topic_change(client,monkeypatch):
    from app import ai
    login(client);create_user(client);notice()
    sid=client.post('/api/subscriptions',json=topic()).json()['id']
    monkeypatch.setattr(ai,'explain',lambda item:{'summary':'专题解读','fit':item['business_context']['offering'],'questions':[],'suggested_actions':[],'model':'test'})
    assert client.post(f'/api/opportunities/1/analyze?subscription_id={sid}').status_code==200
    saved=client.get(f'/api/opportunities/1?subscription_id={sid}').json()
    assert saved['ai_analysis']['summary']=='专题解读'
    assert client.get('/api/opportunities/1').json()['ai_analysis'] is None
    client.put(f'/api/subscriptions/{sid}',json={**topic(),'offering':'新能力'})
    assert client.get(f'/api/opportunities/1?subscription_id={sid}').json()['ai_analysis'] is None
    login(client,'alice','a-strong-test-password')
    assert client.get(f'/api/opportunities/1?subscription_id={sid}').status_code==404

def test_legacy_followup_migrated_once(tmp_path,monkeypatch):
    from app import accounts
    monkeypatch.setattr(db,'DATA',tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD','testing-only-password')
    # Simulate the old schema/data before installing the accounts migration.
    original=accounts.initialize;monkeypatch.setattr(accounts,'initialize',lambda:None)
    db.initialize();notice()
    with db.connect() as c:c.execute("UPDATE opportunities SET status='已联系',note='历史备注'")
    monkeypatch.setattr(accounts,'initialize',original);db.initialize()
    with db.connect() as c:
        assert c.execute('SELECT note FROM followups WHERE user_id=1').fetchone()[0]=='历史备注'
        c.execute("UPDATE followups SET note='迁移后修改'")
    db.initialize()
    with db.connect() as c:assert c.execute('SELECT note FROM followups WHERE user_id=1').fetchone()[0]=='迁移后修改'

def test_closed_notice_topic_score_and_detail_guard(client):
    login(client);notice()
    with db.connect() as c:
        row=c.execute('SELECT payload FROM opportunities WHERE id=1').fetchone()
        value=json.loads(row[0]);value.update(stage='中标结果',title='工业智能控制中标结果',actionable=False)
        c.execute("UPDATE opportunities SET stage='中标结果',payload=?",(db.dumps(value),))
    r=client.post('/api/subscriptions/preview',json={**topic(),'stages':[]}).json()
    assert r['total']==1 and r['items'][0]['topic_score']<=45

def test_api_validation_and_logout_revoke_cookie(client):
    login(client)
    old=client.cookies.get('radar_session')
    client.post('/api/logout')
    client.cookies.set('radar_session',old)
    assert client.get('/api/me').status_code==401
    client.cookies.clear()
    login(client)
    assert client.post('/api/subscriptions',json={**topic(),'source_ids':['jilin']}).status_code==422
    assert client.post('/api/subscriptions',json={**topic(),'stages':['虚构阶段']}).status_code==422

def test_topic_detail_presents_its_own_score_and_reasons(client):
    login(client);notice()
    sid=client.post('/api/subscriptions',json=topic()).json()['id']
    rec=client.get(f'/api/recommendations?subscription_id={sid}').json()['items'][0]
    detail=client.get(f'/api/opportunities/1?subscription_id={sid}').json()
    assert detail['topic_match']['score']==rec['topic_score']
    assert detail['topic_match']['reasons']==rec['topic_reasons']

def test_current_recommendations_sort_before_paging_and_keep_filters(client):
    login(client)
    sid=client.post('/api/subscriptions',json={'name':'控制','keywords':['控制']}).json()['id']
    for title,url,published_at in [
        ('控制旧招标公告','old','2026-09-01T09:00:00+08:00'),
        ('最新设备采购公告','latest','2026-09-03T09:00:00+08:00'),
        ('控制近期招标公告','recent','2026-09-02T09:00:00+08:00'),
    ]:
        db.upsert(analyze({'title':title,'url':'https://example.com/'+url,'source_id':'cecep','source_name':'来源','source_kind':'公开','published_at':published_at,'content':'控制设备采购需求'},[],db.DEFAULT_SETTINGS))
    default=client.get('/api/recommendations').json()
    assert [x['title'] for x in default['items']]==['控制近期招标公告','控制旧招标公告','最新设备采购公告']
    latest=client.get(f'/api/recommendations?subscription_id={sid}&sort=published_at&page_size=1&page=2').json()
    assert latest['total']==3 and latest['page']==2 and latest['page_size']==1
    assert latest['items'][0]['title']=='控制近期招标公告'
    first=client.get('/api/recommendations?sort=published_at&page_size=1').json()['items'][0]
    assert first['title']=='最新设备采购公告'
    client.patch(f"/api/opportunities/{first['id']}",json={'status':'跟进中','note':''})
    filtered=client.get(f'/api/recommendations?subscription_id={sid}&sort=published_at&q=最新&status=跟进中').json()
    assert filtered['total']==1 and filtered['items'][0]['id']==first['id']
    assert client.get('/api/recommendations?sort=unknown').status_code==422
