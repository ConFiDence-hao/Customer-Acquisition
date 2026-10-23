import json
import pytest
from fastapi.testclient import TestClient
from app import db, main, assist

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD','testing-only-password')
    monkeypatch.setenv('SESSION_SECRET','test-session-secret-32-characters-long')
    monkeypatch.setenv('SCHEDULER_ENABLED','false')
    main.FAILURES.clear()
    with TestClient(main.app) as c:
        c.post('/api/login',json={'username':'admin','password':'testing-only-password'})
        yield c

def test_secrets_and_unknown_fields_never_reach_model_or_output(client,monkeypatch):
    seen=[]
    with db.connect() as c:before=[tuple(row) for row in c.execute('SELECT * FROM subscriptions ORDER BY id')]
    def generate(context,keys):
        seen.append(context)
        return {'fields':[{'field':'name','value':'工业自动化'},{'field':'keywords','value':'智能控制，DCS'},{'field':'api_key','value':'bad'},{'field':'enabled','value':'true'}],'explanation':'请核实'}
    monkeypatch.setattr(assist,'generate',generate)
    r=client.post('/api/assist',json={'target':'subscription','instruction':'寻找控制改造需求','current':{'name':'控制','password':'secret123','api_key':'private-key','note':'private notes'}})
    assert r.status_code==200,r.text
    assert set(r.json()['fields'])=={'name','keywords'}
    assert r.json()['fields']['keywords']==['智能控制','DCS']
    assert 'secret123' not in json.dumps(seen) and 'private-key' not in json.dumps(seen) and 'private notes' not in json.dumps(seen)
    with db.connect() as c:assert [tuple(row) for row in c.execute('SELECT * FROM subscriptions ORDER BY id')]==before

def test_privilege_and_invalid_targets(client,monkeypatch):
    client.post('/api/users',json={'username':'alice','display_name':'业务','password':'testing-user-password'})
    client.post('/api/login',json={'username':'alice','password':'testing-user-password'})
    for target in ['source','settings','model','user']:
        assert client.post('/api/assist',json={'target':target,'instruction':'辅助填写'}).status_code==403
    assert client.post('/api/assist',json={'target':'password','instruction':'生成密码'}).status_code==422

def test_regular_user_can_assist_personal_schedule_but_not_collection(client,monkeypatch):
    seen=[]
    def generate(context,keys):
        seen.append(context['page'])
        return {'fields':[{'field':'interval_minutes','value':'120'}],'explanation':'每两小时检查订阅'}
    monkeypatch.setattr(assist,'generate',generate)
    client.post('/api/users',json={'username':'alice','display_name':'业务','password':'testing-user-password'})
    client.post('/api/login',json={'username':'alice','password':'testing-user-password'})
    denied=client.post('/api/assist',json={'target':'automation','page':'automation-collection','instruction':'每两小时采集网站'})
    assert denied.status_code==403
    assert seen==[]
    allowed=client.post('/api/assist',json={'target':'automation','page':'automation-personal','instruction':'每两小时检查订阅'})
    assert allowed.status_code==200
    assert allowed.json()['fields']=={'interval_minutes':120}
    assert seen==['automation-personal']

def test_invalid_numeric_or_enum_rejected(client,monkeypatch):
    monkeypatch.setattr(assist,'generate',lambda *a:{'fields':[{'field':'interval_minutes','value':'1'}],'explanation':''})
    assert client.post('/api/assist',json={'target':'automation','instruction':'加快采集'}).status_code==502

@pytest.mark.parametrize('page',[None,'settings-form'])
def test_strategy_draft_excludes_collection_interval_from_context_and_output(client,monkeypatch,page):
    seen=[]
    def generate(context,keys):
        seen.append(context)
        return {'fields':[{'field':'min_score','value':'67'},{'field':'interval_minutes','value':'120'}],'explanation':'策略草稿'}
    monkeypatch.setattr(assist,'generate',generate)
    schedule=client.get('/api/collection-schedule').json()
    r=client.post('/api/assist',json={'target':'settings','page':page,'instruction':'最低67分，每两小时采集','current':{'min_score':55,'interval_minutes':180}})
    assert r.status_code==200,r.text
    assert r.json()['fields']=={'min_score':67}
    assert seen[0]['current']=={'min_score':55}
    assert 'interval_minutes' not in seen[0]['fields_spec']
    if page:assert '采集间隔' not in seen[0]['page_instruction']
    assert client.get('/api/collection-schedule').json()==schedule

def test_strategy_draft_with_only_collection_interval_is_rejected(client,monkeypatch):
    monkeypatch.setattr(assist,'generate',lambda *a:{'fields':[{'field':'interval_minutes','value':'120'}],'explanation':'两小时'})
    schedule=client.get('/api/collection-schedule').json()
    r=client.post('/api/assist',json={'target':'settings','page':'settings-form','instruction':'每两小时采集'})
    assert r.status_code==502,r.text
    assert client.get('/api/collection-schedule').json()==schedule

def test_missing_model_returns_actionable_message(client,monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL','');monkeypatch.setenv('LLM_MODEL','')
    r=client.post('/api/assist',json={'target':'subscription','instruction':'垃圾焚烧控制'})
    assert r.status_code==503 and '模型' in r.json()['detail']

def test_followup_never_includes_existing_notes(client,monkeypatch):
    from app.analysis import analyze
    db.upsert(analyze({'title':'智能控制系统采购公告','url':'https://example.com/a','source_id':'cecep','source_name':'测试','source_kind':'公开','content':'仅用于生成核实步骤'},[],db.DEFAULT_SETTINGS))
    client.patch('/api/opportunities/1',json={'status':'跟进中','note':'private-client-secret'})
    seen=[]
    def generate(context,keys):
        seen.append(context);return {'fields':[{'field':'note','value':'待核实交付范围'}],'explanation':'草稿'}
    monkeypatch.setattr(assist,'generate',generate)
    assert client.post('/api/assist',json={'target':'followup','instruction':'整理待核实事项','opportunity_id':1,'current':{'note':'private-client-secret'}}).status_code==200
    assert 'private-client-secret' not in json.dumps(seen)
    assert client.get('/api/opportunities/1').json()['note']=='private-client-secret'

@pytest.mark.parametrize('page,target',[
    ('arbitrary-private-context','subscription'),
    ('model-form','subscription'),
    ('automation-collection','search_recommendations'),
])
def test_unknown_or_mismatched_page_context_is_rejected(client,monkeypatch,page,target):
    monkeypatch.setattr(assist,'generate',lambda *a:{'fields':[{'field':'name','value':'草稿'}],'explanation':'草稿'})
    r=client.post('/api/assist',json={'target':target,'page':page,'instruction':'生成本页草稿'})
    assert r.status_code==422,r.text

@pytest.mark.parametrize('page',['push-matches','push-history'])
def test_push_page_rejects_unsupported_personal_status_and_filters_sensitive_context(client,monkeypatch,page):
    seen=[]
    def generate(context,keys):
        seen.append(context)
        return {'fields':[{'field':'q','value':'控制系统'},{'field':'status','value':'跟进中'}],'explanation':'关键词草稿'}
    monkeypatch.setattr(assist,'generate',generate)
    r=client.post('/api/assist',json={'target':'search_recommendations','page':page,'instruction':'查控制系统相关项目','current':{'q':'原关键词','status':'已联系','note':'private-note','api_key':'private-key'},'page_instruction':'private-injected-context'})
    assert r.status_code==422,r.text
    assert '不支持' in r.json()['detail']
    assert seen[0].get('page')==page
    assert seen[0]['current']=={'q':'原关键词'}
    assert 'q' in seen[0]['fields_spec'] and 'status' not in seen[0]['fields_spec']
    assert 'private-note' not in json.dumps(seen)
    assert 'private-key' not in json.dumps(seen)
    assert 'private-injected-context' not in json.dumps(seen)

@pytest.mark.parametrize('page',['automation-collection','automation-personal'])
def test_automation_page_context_preserves_interval_only_drafts(client,monkeypatch,page):
    seen=[]
    def generate(context,keys):
        seen.append(context)
        return {'fields':[{'field':'interval_minutes','value':'120'},{'field':'enabled','value':'true'}],'explanation':'两小时'}
    monkeypatch.setattr(assist,'generate',generate)
    r=client.post('/api/assist',json={'target':'automation','page':page,'instruction':'每两小时执行一次','current':{'interval_minutes':180,'enabled':False}})
    assert r.status_code==200,r.text
    assert r.json()['fields']=={'interval_minutes':120}
    assert seen[0].get('page')==page
    assert seen[0]['current']=={'interval_minutes':180}


def test_fill_scope_is_enforced_by_server_and_does_not_save_configuration(client,monkeypatch):
    seen=[]
    def generate(context,keys):
        seen.append(context)
        return {'fields':[{'field':'name','value':'控制主题'},{'field':'keywords','value':'控制系统'}],'explanation':'仅填名称'}
    monkeypatch.setattr(assist,'generate',generate)
    with db.connect() as c:before=c.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0]
    response=client.post('/api/assist',json={'target':'subscription','page':'topic-form','scope':'name','instruction':'只帮我取订阅名称'})
    assert response.status_code==200,response.text
    assert response.json()['fields']=={'name':'控制主题'} and set(seen[0]['fields_spec'])=={'name'}
    with db.connect() as c:assert c.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0]==before
    assert client.post('/api/assist',json={'target':'subscription','scope':'api_key','instruction':'填写密钥'}).status_code==422


def test_query_unsupported_requirements_are_feedback_instead_of_a_broader_search(client,monkeypatch):
    monkeypatch.setattr(assist,'generate',lambda *args:{'fields':[{'field':'q','value':'控制'}],'explanation':'无法确认预算','unsupported':['按预算筛选暂不支持']})
    response=client.post('/api/assist',json={'target':'search_opportunities','page':'opportunity-filters','instruction':'查预算超过100万的控制项目'})
    assert response.status_code==422 and '预算' in response.json()['detail']


@pytest.mark.parametrize('page,target,sorts',[
    ('opportunity-filters','search_opportunities',['deadline','published_at','score']),
    ('followup-filters','search_followups',['followup_updated_at','published_at']),
    ('push-history','search_recommendations',['published_at','pushed_at','score']),
    ('recommendation-filters','search_recommendations',['published_at','score']),
    ('push-matches','search_recommendations',[]),
    ('company-filters','search_companies',[]),
    ('topic-search','search_subscriptions',[]),
])
def test_query_model_context_only_advertises_current_page_field_choices(client,page,target,sorts):
    context=assist.prepare(assist.AssistRequest(target=target,page=page,instruction='整理本页查询条件'),client.get('/api/me').json())
    assert 'query_policy' in context,'model context lacks page-scoped query semantics'
    policy=context['query_policy']
    assert policy['field_choices'].get('sort',[])==sorts
    assert set(policy['field_choices'])<=set(context['fields_spec'])
    assert set(policy['clear_values'])<=set(context['fields_spec'])
    assert set(policy['sort_directions'])==set(sorts)
    if 'published_at' in sorts:assert policy['sort_directions']['published_at']=='desc'
    if not sorts:assert 'sort' not in context['fields_spec']


def test_query_provider_receives_clear_values_month_boundaries_and_exact_sort_enum(client,monkeypatch):
    from datetime import datetime
    from app import clock
    reference=datetime(2026,10,15,10,0,tzinfo=db.TZ)
    monkeypatch.setattr(clock,'now',lambda:reference)
    monkeypatch.setattr(clock,'today',lambda:reference.date())
    seen=[]
    def completion(messages,schema,**options):
        seen.append(json.loads(messages[1]['content']))
        return {'data':{'fields':[
            {'field':'province','value':''},{'field':'stage','value':''},{'field':'min_score','value':'0'},
            {'field':'terms','value':'烟气'},{'field':'published_from','value':'2026-10-01'},
            {'field':'published_to','value':'2026-10-15'},{'field':'sort','value':'published_at'},
        ],'explanation':'按自然月和明确的筛选范围整理查询','unsupported':[]}}
    monkeypatch.setattr(assist.ai_provider,'complete_json',completion)
    response=client.post('/api/assist',json={'target':'search_opportunities','page':'opportunity-filters',
        'instruction':'请看这个月所有地区及所有公告类别的烟气项目，分数不设下限，发布时间新的在前',
        'current':{'province':'浙江','stage':'招标','min_score':85,'q':'旧关键词','api_key':'private-key'}})
    assert response.status_code==200,response.text
    fields=response.json()['fields']
    assert fields=={'province':'','stage':'','min_score':0,'terms':['烟气'],'q':'','term_mode':'any',
        'published_from':'2026-10-01','published_to':'2026-10-15','sort':'published_at'}
    assert 'query_policy' in seen[0],'actual provider request omits supported query semantics'
    policy=seen[0]['query_policy']
    assert policy['field_choices']['stage']==['','招标','询价','采购意向','变更','中标候选人','中标结果','终止','资讯']
    assert policy['field_choices']['sort']==['deadline','published_at','score']
    assert policy['clear_values']['province']=='' and policy['clear_values']['stage']==''
    assert policy['clear_values']['min_score']=='0'
    assert policy['date_rules']['month_to_date']=={'from':'2026-10-01','to':'2026-10-15'}
    assert policy['date_rules']['inclusive'] is True
    assert 'private-key' not in json.dumps(seen)


def test_query_scoped_sort_context_does_not_offer_other_facets(client):
    context=assist.prepare(assist.AssistRequest(target='search_opportunities',page='opportunity-filters',scope='sort',instruction='按发布新旧排序'),client.get('/api/me').json())
    assert 'query_policy' in context,'scoped query lacks its allowed sort values'
    assert set(context['fields_spec'])=={'sort'}
    assert context['query_policy']['field_choices']=={'sort':['deadline','published_at','score']}
    assert context['query_policy']['clear_values']=={}


def test_query_still_rejects_an_unlisted_sort_enum(client,monkeypatch):
    monkeypatch.setattr(assist,'generate',lambda *args:{'fields':[{'field':'sort','value':'published_desc'}],
        'explanation':'错误排序值','unsupported':[]})
    response=client.post('/api/assist',json={'target':'search_opportunities','page':'opportunity-filters','instruction':'最新发布在前'})
    assert response.status_code==422 and '排序' in response.json()['detail']
