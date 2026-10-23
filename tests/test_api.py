import json
from contextlib import contextmanager
from datetime import datetime,timedelta
import pytest
from fastapi.testclient import TestClient
from app import db,main
from app.analysis import analyze

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD','testing-only-password')
    monkeypatch.setenv('SESSION_SECRET','test-session-secret-32-characters-long')
    monkeypatch.setenv('SCHEDULER_ENABLED','false')
    with TestClient(main.app) as c:
        yield c

def auth(client):
    assert client.post('/api/login',json={'username':'admin','password':'testing-only-password'}).status_code==200

def insert():
    return db.upsert(analyze({'title':'垃圾焚烧智慧管控公开招标','url':'https://new.ebidding.cecep.cn/test.html','source_id':'cecep','source_name':'慧采云','source_kind':'集团采购','published_at':'2026-10-10','content':'投标截止时间：2099-11-10 12:00'},[],db.DEFAULT_SETTINGS))

def test_auth_and_csrf(client):
    assert client.get('/api/health').status_code==200
    for endpoint in ['/api/companies','/api/export','/api/opportunities','/api/settings','/api/sources']:
        assert client.get(endpoint).status_code==401
    auth(client)
    assert client.get('/api/me').status_code==200
    assert client.post('/api/collect',headers={'Origin':'https://evil.example'}).status_code==403
    client.post('/api/logout')
    assert client.get('/api/me').status_code==401

def test_dedup_followup_persistence_and_versions(client):
    auth(client)
    assert insert()=='inserted'
    assert insert()=='unchanged'
    items=client.get('/api/opportunities').json()
    assert items['total']==1
    ident=items['items'][0]['id']
    assert client.patch(f'/api/opportunities/{ident}',json={'status':'跟进中','note':'=HYPERLINK("unsafe")'}).status_code==200
    insert()
    assert client.get(f'/api/opportunities/{ident}').json()['status']=='跟进中'
    exported=client.get('/api/export')
    assert '\'=HYPERLINK' in exported.text and exported.content.startswith(b'\xef\xbb\xbf')
    assert client.get('/api/opportunities?min_score=101').status_code==422
    assert client.get('/api/opportunities?sort=drop table').status_code==422

def test_filters_export_and_not_found(client):
    auth(client);insert()
    assert client.get('/api/opportunities?q=智慧&actionable=true').json()['total']==1
    assert client.get('/api/opportunities?q=无关').json()['total']==0
    assert client.get('/api/opportunities/999').status_code==404
    assert client.get('/api/companies?page_size=101').status_code==422
    assert client.get('/api/dashboard').json()['stats']['total']==1
    assert client.get('/api/report').json()['opportunities']

def test_settings_validate_and_rescore(client):
    auth(client);insert()
    settings=client.get('/api/settings').json()
    assert client.put('/api/settings',json={**settings,'interval_minutes':1}).status_code==422
    response=client.put('/api/settings',json={**settings,'exclude_keywords':['智慧管控']})
    assert response.status_code==200
    assert not client.get('/api/opportunities').json()['items'][0]['actionable']

@pytest.mark.parametrize('legacy_interval',[None,120])
def test_strategy_save_preserves_collection_schedule(client,legacy_interval):
    auth(client)
    response=client.put('/api/collection-schedule',json={'enabled':False,'interval_minutes':180})
    assert response.status_code==200,response.text
    schedule=client.get('/api/collection-schedule').json()
    strategy={'focus_keywords':['烟气优化'],'exclude_keywords':['保洁'],'preferred_provinces':['浙江'],'min_score':67}
    payload={**strategy}
    if legacy_interval is not None:payload['interval_minutes']=legacy_interval

    response=client.put('/api/settings',json=payload)

    assert response.status_code==200,response.text
    assert response.json()['interval_minutes']==180
    saved=client.get('/api/settings').json()
    assert all(saved[key]==value for key,value in strategy.items())
    assert saved['interval_minutes']==180
    assert client.get('/api/collection-schedule').json()==schedule

@pytest.mark.parametrize('legacy_interval',[1,10081])
def test_strategy_rejects_out_of_range_legacy_collection_interval(client,legacy_interval):
    auth(client)
    settings=client.get('/api/settings').json()
    response=client.put('/api/settings',json={**settings,'interval_minutes':legacy_interval})
    assert response.status_code==422,response.text
    assert client.get('/api/settings').json()==settings

@pytest.mark.parametrize('first_writer',['strategy','collection'])
def test_interleaved_strategy_and_collection_saves_preserve_both_owners(client,monkeypatch,first_writer):
    auth(client)
    initial=client.get('/api/settings').json()
    strategy={'focus_keywords':['烟气优化'],'exclude_keywords':['保洁'],'preferred_provinces':['浙江'],'min_score':67}
    schedule={'enabled':False,'interval_minutes':240}
    writes={'strategy':('/api/settings',strategy),'collection':('/api/collection-schedule',schedule)}
    second_writer='collection' if first_writer=='strategy' else 'strategy'
    real_connect=db.connect
    interleaved=False

    class ReadSnapshot:
        def __init__(self,row):self.row=row
        def fetchone(self):return self.row

    class InterleavingConnection:
        def __init__(self,connection):self.connection=connection
        def __getattr__(self,name):return getattr(self.connection,name)
        def execute(self,sql,*args):
            nonlocal interleaved
            statement=' '.join(sql.upper().split())
            settings_read=statement.startswith('SELECT VALUE FROM SETTINGS WHERE ID=1')
            settings_write=statement.startswith('UPDATE SETTINGS SET VALUE=')
            if not interleaved and (settings_read or settings_write):
                # A read/replace writer keeps its old snapshot; an atomic writer sees
                # the other owner's commit before updating its own fields.
                snapshot=None
                if settings_read:
                    cursor=self.connection.execute(sql,*args)
                    snapshot=ReadSnapshot(cursor.fetchall()[0]);cursor.close()
                interleaved=True
                endpoint,payload=writes[second_writer]
                response=client.put(endpoint,json=payload)
                assert response.status_code==200,response.text
                if snapshot is not None:return snapshot
            return self.connection.execute(sql,*args)

    @contextmanager
    def interleaving_connect():
        with real_connect() as connection:yield InterleavingConnection(connection)

    monkeypatch.setattr(db,'connect',interleaving_connect)
    endpoint,payload=writes[first_writer]
    response=client.put(endpoint,json=payload)
    assert response.status_code==200,response.text
    assert interleaved
    saved=client.get('/api/settings').json()
    assert saved=={**initial,**strategy,'collection_enabled':False,'interval_minutes':240}
    assert client.get('/api/collection-schedule').json()=={**schedule,'scheduler_enabled':False}

def test_model_failure_is_explicit(client,monkeypatch):
    auth(client);insert();monkeypatch.delenv('LLM_BASE_URL',raising=False)
    result=client.post('/api/opportunities/1/analyze')
    assert result.status_code==503 and '未配置' in result.json()['detail']

def test_future_expiry_dynamic_filter(client):
    auth(client);insert()
    with db.connect() as c:
        row=c.execute('SELECT * FROM opportunities').fetchone()
        value=json.loads(row['payload']);value['deadline']='2020-01-01T00:00:00+08:00'
        value['content']='投标截止时间：2020-01-01 00:00'
        c.execute('UPDATE opportunities SET deadline=?,payload=?',(value['deadline'],db.dumps(value)))
    assert client.get('/api/opportunities?actionable=true').json()['total']==0
    assert client.get('/api/opportunities/1').json()['actionable'] is False
    assert client.get('/api/opportunities/1').json()['score']<50
    assert '未到期' not in client.get('/api/opportunities/1').json()['summary']
    assert not client.get('/api/report').json()['opportunities']

def test_unverified_expiry_invalidates_ai(client):
    auth(client);insert()
    with db.connect() as c:
        row=c.execute('SELECT * FROM opportunities').fetchone()
        value=json.loads(row['payload'])
        value.update(content='投标截止时间：2020-01-01 00:00',deadline='2020-01-01T00:00:00+08:00',actionable=False,ai_analysis={'summary':'尚未截止'},detail_status='详情暂不可读；保留历史正文')
        c.execute('UPDATE opportunities SET actionable=0,deadline=?,payload=?',(value['deadline'],db.dumps(value)))
    item=client.get('/api/opportunities/1').json()
    assert item['score']<50 and item['ai_analysis'] is None

def test_exact_project_results_supersede_active_announcement(client,monkeypatch):
    auth(client);insert()
    with db.connect() as c:
        row=c.execute('SELECT * FROM opportunities').fetchone()
        value=json.loads(row['payload']);value['project_code']='202610100000123'
        c.execute('UPDATE opportunities SET payload=?',(db.dumps(value),))
    result=analyze({**value,'url':'https://new.ebidding.cecep.cn/result.html','title':'垃圾焚烧智慧管控中标结果公告','published_at':'2026-10-11'},[],db.DEFAULT_SETTINGS)
    db.upsert(result)
    assert db.reconcile_lifecycle()==1
    item=client.get('/api/opportunities/1').json()
    assert not item['actionable'] and item['score']<50
    from app import collectors
    source=next(x for x in collectors.SOURCES if x['id']=='cecep')
    monkeypatch.setattr(collectors,'cecep_candidates',lambda *args:[{'url':value['url'],'title':value['title'],'published_at':value['published_at'],'project_code':value['project_code']}])
    class PublicStub:
        def get(self,url):return '<div class="article-info">'+value['content']+(' 正文证据 '*40)+'</div>'
        def close(self):pass
    monkeypatch.setattr(collectors,'PublicClient',PublicStub)
    collectors.collect_source(source,[],db.DEFAULT_SETTINGS)
    # Check immediately, without waiting for end-of-run lifecycle reconciliation.
    item=client.get('/api/opportunities/1').json()
    assert not item['actionable'] and item['score']<50
    assert item['superseded_by']['url'].endswith('result.html')
    settings=client.get('/api/settings').json()
    assert client.put('/api/settings',json=settings).status_code==200
    item=client.get('/api/opportunities/1').json()
    assert not item['actionable'] and item['score']<50

def test_stale_reanalysis_cannot_overwrite_new_source_content(client):
    auth(client);insert()
    with db.connect() as c:row=c.execute('SELECT * FROM opportunities').fetchone()
    old=json.loads(row['payload']);new={**old,'content':'投标截止时间：2099-12-11 10:00'}
    new=analyze(new,[],db.DEFAULT_SETTINGS);db.upsert(new)
    assert db.upsert(old,expected_hash=row['content_hash'])=='conflict'
    assert client.get('/api/opportunities/1').json()['deadline'].startswith('2099-12-11')

@pytest.mark.parametrize('refresh_mode',['cached','failed','verified','same_day_time','missing_date','legacy','different_day','cached_changed_date','failed_changed_date'])
def test_publication_warning_survives_cached_evidence_until_reverified(client,monkeypatch,refresh_mode):
    from app import collectors
    auth(client)
    warning='列表与页面发布日期不一致，保留列表日期，需核实原公告'
    raw={'url':'https://new.ebidding.cecep.cn/date-warning.html','title':'垃圾焚烧智慧控制系统改造公开招标','published_at':'2026-10-10','source_id':'cecep','source_name':'中节能 · 慧采云','source_kind':'集团采购','content':'烟气净化改造。'*40,'detail_status':'正文可读','publication_warning':warning,'detail_fetched_at':db.now() if refresh_mode in ('cached','cached_changed_date') else '2020-01-01T00:00:00+08:00'}
    source=next(x for x in collectors.SOURCES if x['id']=='cecep')
    raw.update(source_name=source['name'],source_kind=source['kind'])
    if refresh_mode in ('cached','cached_changed_date'):raw['detail_published_at']='2026-10-09'
    if refresh_mode=='same_day_time':raw['published_at']='2026-10-10T19:18:00+08:00'
    if refresh_mode=='missing_date':raw.update(published_at=None,detail_published_at='2026-10-09',detail_fetched_at=db.now())
    if refresh_mode=='legacy':raw['detail_fetched_at']=db.now()
    db.upsert(analyze(raw,[],db.DEFAULT_SETTINGS))
    candidate={k:raw[k] for k in ('url','title','published_at')}
    if refresh_mode in ('cached_changed_date','failed_changed_date'):candidate['published_at']='2026-10-11'
    monkeypatch.setattr(collectors,'cecep_candidates',lambda *args:[dict(candidate)])
    calls=[]
    class PublicStub:
        def get(self,url):
            calls.append(url)
            if refresh_mode in ('failed','failed_changed_date'):raise collectors.SourceUnavailable('详情暂不可读')
            return '<div class="article-info">'+raw['content']+'</div>发布时间：'+('2026-10-09' if refresh_mode=='different_day' else '2026-10-10')
        def close(self):pass
    monkeypatch.setattr(collectors,'PublicClient',PublicStub)
    result=collectors.collect_source(source,[],db.DEFAULT_SETTINGS)
    item=client.get('/api/opportunities/1').json()
    assert (warning in item['risks']) == (refresh_mode not in ('verified','same_day_time','legacy'))
    assert bool(item.get('publication_warning')) == (refresh_mode not in ('verified','same_day_time','legacy'))
    if refresh_mode in ('cached','missing_date'):
        assert not calls
        if refresh_mode=='missing_date':
            assert result['updated']==1 and result['unchanged']==0
            assert item['published_at']=='2026-10-09T00:00:00+08:00'
            assert item['published_at_precision']=='day'
            repeated=collectors.collect_source(source,[],db.DEFAULT_SETTINGS)
            repeated_item=client.get('/api/opportunities/1').json()
            assert not calls
            assert repeated_item['published_at']=='2026-10-09T00:00:00+08:00'
            assert repeated_item['published_at_precision']=='day'
            assert warning in repeated_item['risks']
            assert repeated_item['publication_warning']==warning
            assert repeated['unchanged']==1 and repeated['updated']==0
        else:
            assert result['unchanged']==1 and result['updated']==0
    if refresh_mode=='cached_changed_date':assert not calls

def test_undated_deadline_notice_ages_out_of_top_priority(client):
    auth(client)
    raw={'title':'垃圾焚烧智慧管控招标','url':'https://new.ebidding.cecep.cn/old.html','source_id':'cecep','source_kind':'集团采购','published_at':'2020-01-01','content':''}
    item=analyze(raw,[],db.DEFAULT_SETTINGS,datetime(2020,1,2,tzinfo=db.TZ));db.upsert(item)
    assert item['score']>=75
    item=client.get('/api/opportunities/1').json()
    assert item['score']<75 and item['analysis_date']==db.now()[:10]

def test_file_window_expiry_refreshes_within_same_day_and_clears_ai(client):
    auth(client)
    current=datetime.now(db.TZ)
    end=current-timedelta(minutes=2)
    raw={'title':'垃圾焚烧智慧管控招标','url':'https://new.ebidding.cecep.cn/window.html','source_id':'cecep','source_kind':'集团采购','published_at':current.isoformat(),'content':'报名截止时间：'+end.strftime('%Y-%m-%d %H:%M')+'\n投标截止时间：2099-11-01 10:00'}
    item=analyze(raw,[],db.DEFAULT_SETTINGS,current-timedelta(minutes=10))
    assert item['actionable'] and item['document_state']=='open'
    item['ai_analysis']={'summary':'仍可报名'};db.upsert(item)
    updated=client.get('/api/opportunities/1').json()
    assert not updated['actionable'] and updated['document_state']=='expired' and updated['ai_analysis'] is None

def test_legacy_title_only_model_summary_cannot_promote_unverified_amount_to_budget(client):
    auth(client)
    value=analyze({'title':'3200万！垃圾焚烧烟气提标项目招标','url':'https://new.ebidding.cecep.cn/title-only.html','source_id':'cecep','source_kind':'集团采购','published_at':db.now(),'content':''},[],db.DEFAULT_SETTINGS)
    value['ai_analysis']={'summary':'项目预算约3200万元','fit':'可评估控制优化','questions':[],'suggested_actions':['核实原公告']}
    db.upsert(value)
    for item in [client.get('/api/opportunities/1').json(),client.get('/api/opportunities').json()['items'][0]]:
        assert '预算约' not in item['ai_analysis']['summary']
        assert '公开列表标题：3200万' in item['ai_analysis']['summary']
        assert not item['actionable'] and not item['budget_text']
        assert item['ai_analysis']['fit']=='可评估控制优化'
