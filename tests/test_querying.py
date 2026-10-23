"""Real query facets preserve scopes, exact dates, and deadline meaning."""
import csv,importlib,importlib.util,io,json
from datetime import datetime,timedelta,timezone
import pytest
from fastapi import HTTPException
from app import clock,db,assist,workbench
from app.analysis import analyze
from test_team_followups import client,login,save
from test_ai_job_api import result

REFERENCE=datetime(2026,10,14,12,tzinfo=timezone(timedelta(hours=8)))


def module():
    assert importlib.util.find_spec('app.querying') is not None,'unified query spec is not implemented'
    return importlib.import_module('app.querying')


def item(**changes):
    return {'title':'智慧消防项目','content':'包含烟气和 SCR 技术','published_at':'2026-10-14T23:59:59+08:00',
            'deadline':'2026-10-15T09:00:00+08:00','document_deadline':'2026-10-13T10:00:00+08:00','document_state':'expired',
            'stage':'招标','status':'待研判','score':60,'actionable':False,**changes}


def test_keyword_facet_replacement():
    q=module().normalize('opportunities',{'q':'燃烧','terms':['消防','烟气'],'province':'湖北'},REFERENCE)
    params=q.to_params()
    assert 'q' not in params and json.loads(params['terms'])==['消防','烟气'] and params['province']=='湖北'


@pytest.mark.parametrize('mode,terms,expected',[('any',['消防','锅炉'],True),('all',['消防','烟气'],True),('all',['消防','锅炉'],False)])
def test_terms_match_title_or_body_before_paging(mode,terms,expected):
    query=module().normalize('opportunities',{'terms':terms,'term_mode':mode},REFERENCE)
    assert module().matches_notice(item(),query,REFERENCE) is expected


@pytest.mark.parametrize('published,expected',[('2026-10-14',True),('2026-10-14T23:59:59+08:00',True),('2026-10-15T00:00:00+08:00',False),(None,False)])
def test_published_date_uses_inclusive_day_and_excludes_unknown_time(published,expected):
    query=module().normalize('opportunities',{'published_from':'2026-10-14','published_to':'2026-10-14'},REFERENCE)
    assert module().matches_notice(item(published_at=published),query,REFERENCE) is expected


@pytest.mark.parametrize('fields,changes,expected',[
    ({'deadline_state':'open'},{},True),({'deadline_state':'eligible'},{},False),
    ({'deadline_state':'open'},{'deadline':None},False),({'deadline_state':'unknown'},{'deadline':None},True),
    ({'deadline_state':'open'},{'stage':'终止'},False),({'deadline_state':'open'},{'superseded_by':9},False),
    ({'deadline_state':'open'},{'deadline':'2026-10-14T12:00:00+08:00'},False),
    ({'deadline_state':'eligible'},{'actionable':True,'document_state':'open','document_deadline':'2026-10-15T08:00:00+08:00'},True),
])
def test_unexpired_and_registration_eligibility_are_distinct(fields,changes,expected):
    query=module().normalize('opportunities',fields,REFERENCE)
    assert module().matches_notice(item(**changes),query,REFERENCE) is expected


@pytest.mark.parametrize('page,fields',[
    ('companies',{'deadline_state':'open'}),('subscriptions',{'province':'浙江'}),('pushes',{'min_score':60}),
    ('opportunities',{'province':'武汉'}),('opportunities',{'stage':'随便一个阶段'}),
    ('opportunities',{'published_from':'2026-10-15','published_to':'2026-10-14'}),
])
def test_unsupported_or_invalid_constraints_are_explicitly_rejected(page,fields):
    with pytest.raises(HTTPException) as error:module().normalize(page,fields,REFERENCE)
    assert error.value.status_code==422


def seed():
    today=clock.today();yesterday=today-timedelta(days=1)
    for i in range(12):
        db.upsert(analyze({'title':f'控制采购项目 {i:02d}','url':f'https://example.com/querying/{i}',
            'source_id':'cecep','source_name':'公开来源','published_at':(yesterday if i<6 else today).isoformat(),
            'content':'烟气 SCR 设备采购' if i<6 else '锅炉控制系统采购'},[],db.DEFAULT_SETTINGS))
    return today.isoformat(),yesterday.isoformat()


def test_list_export_push_dates_and_followup_scope_use_same_filters(client):
    module();today,yesterday=seed();login(client,'alice')
    terms=json.dumps(['烟气','SCR'])
    params={'q':'旧词不会命中','terms':terms,'published_from':yesterday,'published_to':yesterday,'page_size':5}
    first=client.get('/api/opportunities',params=params)
    assert first.status_code==200,first.text
    assert first.json()['total']==6 and len(first.json()['items'])==5
    second=client.get('/api/opportunities',params={**params,'page':2}).json()
    assert second['total']==6 and len(second['items'])==1
    assert len(list(csv.reader(io.StringIO(client.get('/api/export',params=params).text))))==7
    client.post('/api/subscriptions',json={'name':'烟气','keywords':['烟气']});workbench.dispatch_due(force=True)
    assert client.get('/api/pushes',params={'published_from':today}).json()['total']==0
    pushed=client.get('/api/pushes',params={'terms':terms,'pushed_from':today,'pushed_to':today,'page_size':5,'page':2}).json()
    assert pushed['total']==6 and len(pushed['items'])==1
    save(client,1,note='alice private note')
    assert client.get('/api/followups',params={'terms':terms}).json()['total']==1
    login(client,'bobby')
    assert client.get('/api/followups',params={'terms':terms}).json()['total']==0
    assert client.get('/api/companies',params={'deadline_state':'open'}).status_code==422


def test_query_jobs_expire_after_five_minutes_and_fresh_submission_gets_new_task(client,monkeypatch):
    module();monkeypatch.setattr(assist,'generate',lambda *args:{'fields':[{'field':'terms','value':'烟气，SCR'},{'field':'deadline_state','value':'open'}],'explanation':'查询条件'})
    body={'kind':'assist','request':{'target':'search_opportunities','page':'opportunity-filters','instruction':'查看未截止的烟气或SCR项目'}}
    first=client.post('/api/ai-jobs',json=body)
    assert first.status_code==200,first.text
    finished=result(client,first.json()['id'])
    assert finished['status']=='succeeded' and finished['result']['fields']['deadline_state']=='open'
    assert finished.get('server_time') and finished['result'].get('valid_until')
    now=clock.now();monkeypatch.setattr(clock,'now',lambda:now+timedelta(minutes=6))
    stale=client.get(f"/api/ai-jobs/{first.json()['id']}").json()
    assert stale['status']=='stale' and stale['result'] is None
    new=client.post('/api/ai-jobs',json=body)
    assert new.status_code==200 and new.json()['id']!=first.json()['id']
