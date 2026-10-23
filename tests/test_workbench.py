import json
from contextlib import contextmanager
import pytest
from fastapi.testclient import TestClient
from app import db,main
from app.analysis import analyze
@pytest.fixture
def client(tmp_path,monkeypatch):
 monkeypatch.setattr(db,'DATA',tmp_path);monkeypatch.setenv('RADAR_PASSWORD','testing-only-password');monkeypatch.setenv('SESSION_SECRET','test-session-secret-32-characters-long');monkeypatch.setenv('SCHEDULER_ENABLED','false');main.FAILURES.clear()
 with TestClient(main.app) as c:
  assert c.post('/api/login',json={'username':'admin','password':'testing-only-password'}).status_code==200
  # These scenarios control their custom topics; archive only the four installed
  # defaults through the API, retaining their markers so restarts do not reseed.
  user_id=c.get('/api/me').json()['id']
  with db.connect() as connection:default_ids={row['subscription_id'] for row in connection.execute('SELECT subscription_id FROM subscription_defaults WHERE user_id=?',(user_id,))}
  initial=c.get('/api/my-subscriptions').json()
  assert initial['total']==4 and {topic['id'] for topic in initial['items']}==default_ids
  for topic_id in default_ids:assert c.delete(f'/api/subscriptions/{topic_id}').status_code==200
  assert c.get('/api/my-subscriptions').json()['total']==0
  yield c

def seed(c):
 c.post('/api/subscriptions',json={'name':'控制','keywords':['控制']})
 for n in range(12):db.upsert(analyze({'title':f'智能控制系统采购公告{n}','url':f'https://example.com/{n}','source_id':'cecep','source_name':'来源','source_kind':'公开','content':'智能控制系统采购需求'},[],db.DEFAULT_SETTINGS))

def test_personal_pushes_persist_deduplicate_and_page(client):
 seed(client)
 from app import workbench
 workbench.dispatch_due()
 first=client.get('/api/pushes?page_size=5').json();assert first['total']==12 and len(first['items'])==5
 ident=first['items'][0]['push_id'];assert client.patch(f'/api/pushes/{ident}/read').status_code==200
 workbench.dispatch_due(force=True)
 result=client.get('/api/pushes?page_size=20').json();assert result['total']==12 and sum(bool(x['read_at']) for x in result['items'])==1
 assert client.get('/api/pushes?page_size=7').status_code==422
 client.post('/api/users',json={'username':'alice','display_name':'业务','password':'testing-user-password'})
 client.post('/api/login',json={'username':'alice','password':'testing-user-password'})
 assert client.get('/api/pushes').json()['total']==0
 assert client.patch(f'/api/pushes/{ident}/read').status_code==404
 assert client.get('/api/my-subscriptions').json()['total']==4

def test_disabled_automation_no_push_and_admin_mine(client):
 seed(client)
 assert client.put('/api/automation',json={'enabled':False,'interval_minutes':15}).status_code==200
 from app import workbench
 workbench.dispatch_due(force=True)
 assert client.get('/api/pushes').json()['total']==0
 assert client.get('/api/my-subscriptions').json()['total']==1
 assert client.put('/api/automation',json={'enabled':True,'interval_minutes':1}).status_code==422

def test_progress_survives_and_global_schedule_permissions(client):
 from app import workbench
 with db.connect() as c:rid=c.execute("INSERT INTO runs(started_at,status) VALUES(?,'运行中')",(db.now(),)).lastrowid
 workbench.progress(rid,'采集网站',1,3,'测试来源')
 r=client.get('/api/collection-progress').json();assert r['active'] and r['completed']==1 and r['total']==3 and r['current']=='测试来源'
 assert client.put('/api/collection-schedule',json={'enabled':False,'interval_minutes':30}).status_code==200
 assert client.get('/api/collection-schedule').json()['enabled'] is False
 assert client.get('/api/dashboard').json()['stats']['next_run'] is None
 db.initialize()
 assert client.get('/api/collection-progress').json()['active'] is False
 client.post('/api/users',json={'username':'alice','display_name':'业务','password':'testing-user-password'})
 client.post('/api/login',json={'username':'alice','password':'testing-user-password'})
 assert client.put('/api/collection-schedule',json={'enabled':True,'interval_minutes':30}).status_code==403

def test_worker_failure_finishes_run_and_releases_lock(client,monkeypatch):
 with db.connect() as c:rid=c.execute("INSERT INTO runs(started_at,status) VALUES(?,'运行中')",(db.now(),)).lastrowid
 monkeypatch.setattr(main,'run_collection',lambda rid:(_ for _ in ()).throw(RuntimeError('test')))
 main.RUN_LOCK.acquire();main.run_worker(rid)
 assert not main.RUN_LOCK.locked()
 result=client.get('/api/collection-progress').json()
 assert not result['active'] and result['run']['status']=='失败' and result['run']['error']

def test_regular_automation_assistance(client,monkeypatch):
 from app import assist
 client.post('/api/users',json={'username':'alice','display_name':'业务','password':'testing-user-password'})
 client.post('/api/login',json={'username':'alice','password':'testing-user-password'})
 monkeypatch.setattr(assist,'generate',lambda *args:{'fields':[{'field':'interval_minutes','value':'120'}],'explanation':'每两小时'})
 assert client.post('/api/assist',json={'target':'automation','instruction':'每两小时执行'}).status_code==200
 assert client.post('/api/assist',json={'target':'settings','instruction':'每两小时执行'}).status_code==403

def add_push_notice(title,url,published_at,content='控制设备采购需求'):
 db.upsert(analyze({'title':title,'url':url,'source_id':'cecep','source_name':'来源','source_kind':'公开','published_at':published_at,'content':content},[],db.DEFAULT_SETTINGS))
 with db.connect() as c:return c.execute('SELECT id FROM opportunities WHERE url=?',(url,)).fetchone()[0]

@pytest.mark.parametrize('saved_before_dispatch',[True,False])
def test_pushes_overlay_followups_saved_before_or_after_dispatch(client,monkeypatch,saved_before_dispatch):
 from app import workbench
 client.post('/api/subscriptions',json={'name':'控制','keywords':['控制']})
 ident=add_push_notice('控制系统招标公告','https://example.com/live-followup','2026-09-01T09:00:00+08:00')
 if saved_before_dispatch:
  monkeypatch.setattr(db,'now',lambda:'2026-10-13T10:00:00+08:00')
  assert client.patch(f'/api/opportunities/{ident}',json={'status':'已联系','note':'只在详情显示的备注'}).status_code==200
 monkeypatch.setattr(db,'now',lambda:'2026-10-13T10:10:00+08:00')
 workbench.dispatch_due(force=True)
 first=client.get('/api/pushes').json()['items'][0]
 assert first.get('status')==('已联系' if saved_before_dispatch else '待研判')
 assert first.get('has_followup') is saved_before_dispatch
 assert first.get('followup_updated_at')==('2026-10-13T10:00:00+08:00' if saved_before_dispatch else None)
 monkeypatch.setattr(db,'now',lambda:'2026-10-13T10:20:00+08:00')
 assert client.patch(f"/api/pushes/{first['push_id']}/read").status_code==200
 with db.connect() as c:original=dict(c.execute('SELECT payload,pushed_at,read_at FROM pushes WHERE id=?',(first['push_id'],)).fetchone())
 monkeypatch.setattr(db,'now',lambda:'2026-10-13T10:30:00+08:00')
 assert client.patch(f'/api/opportunities/{ident}',json={'status':'跟进中','note':'更新后的私人备注'}).status_code==200
 current=client.get('/api/pushes').json()['items'][0]
 assert current.get('status')=='跟进中' and current.get('has_followup') is True
 assert current.get('followup_updated_at')=='2026-10-13T10:30:00+08:00'
 assert current['status']==client.get(f'/api/opportunities/{ident}').json()['status']
 assert 'note' not in current
 monkeypatch.setattr(db,'now',lambda:'2026-10-13T10:40:00+08:00')
 workbench.dispatch_due(force=True)
 later=client.get('/api/pushes').json()['items'][0]
 assert later['status']=='跟进中' and later['followup_updated_at']==current['followup_updated_at']
 with db.connect() as c:stored=dict(c.execute('SELECT payload,pushed_at,read_at FROM pushes WHERE id=?',(first['push_id'],)).fetchone())
 assert stored==original
 assert {'status','has_followup','followup_updated_at','note'}.isdisjoint(json.loads(stored['payload']))


@pytest.mark.parametrize('saved',[False,True])
def test_pushes_overlay_current_metadata_over_stale_legacy_payload(client,monkeypatch,saved):
 from app import workbench
 client.post('/api/subscriptions',json={'name':'控制','keywords':['控制']})
 ident=add_push_notice('控制系统招标公告','https://example.com/stale-followup','2026-09-01T09:00:00+08:00')
 workbench.dispatch_due(force=True)
 with db.connect() as c:
  row=c.execute('SELECT * FROM pushes WHERE opportunity_id=?',(ident,)).fetchone()
  payload=json.loads(row['payload']);payload.update(status='已排除',has_followup=not saved,followup_updated_at='1999-01-01',note='过时的私人备注')
  stale=db.dumps(payload)
  c.execute('UPDATE pushes SET payload=? WHERE id=?',(stale,row['id']))
 if saved:
  monkeypatch.setattr(db,'now',lambda:'2026-10-13T10:30:00+08:00')
  assert client.patch(f'/api/opportunities/{ident}',json={'status':'待研判','note':'已明确保存待研判'}).status_code==200
 current=client.get('/api/pushes').json()['items'][0]
 assert current.get('status')=='待研判'
 assert current.get('has_followup') is saved
 assert current.get('followup_updated_at')==('2026-10-13T10:30:00+08:00' if saved else None)
 assert 'note' not in current
 with db.connect() as c:assert c.execute('SELECT payload FROM pushes WHERE id=?',(row['id'],)).fetchone()[0]==stale


def test_pushes_followups_are_account_and_notice_id_scoped(client):
 from app import workbench
 assert client.post('/api/users',json={'username':'alice','display_name':'业务','password':'testing-user-password'}).status_code==200
 client.post('/api/subscriptions',json={'name':'控制','keywords':['控制']})
 first=add_push_notice('同名控制招标公告','https://example.com/same-title-first','2026-09-01T09:00:00+08:00')
 second=add_push_notice('同名控制招标公告','https://example.com/same-title-second','2026-09-01T09:00:00+08:00')
 assert client.patch(f'/api/opportunities/{first}',json={'status':'已联系','note':'管理员私人备注'}).status_code==200
 assert client.post('/api/login',json={'username':'alice','password':'testing-user-password'}).status_code==200
 client.post('/api/subscriptions',json={'name':'我的控制','keywords':['控制']})
 assert client.patch(f'/api/opportunities/{second}',json={'status':'已排除','note':'Alice私人备注'}).status_code==200
 workbench.dispatch_due(force=True)
 alice={item['id']:item for item in client.get('/api/pushes').json()['items']}
 assert alice[first].get('status')=='待研判' and alice[first].get('has_followup') is False
 assert alice[first].get('followup_updated_at') is None
 assert alice[second].get('status')=='已排除' and alice[second].get('has_followup') is True
 assert client.post('/api/login',json={'username':'admin','password':'testing-only-password'}).status_code==200
 admin={item['id']:item for item in client.get('/api/pushes').json()['items']}
 assert admin[first].get('status')=='已联系' and admin[first].get('has_followup') is True
 assert admin[second].get('status')=='待研判' and admin[second].get('has_followup') is False
 assert admin[second].get('followup_updated_at') is None
 assert all('note' not in item for item in [*admin.values(),*alice.values()])


def test_pushes_fetch_followup_metadata_once_for_the_requested_page(client,monkeypatch):
 from app import workbench
 seed(client);workbench.dispatch_due(force=True)
 with db.connect() as c:
  c.executemany('INSERT INTO followups VALUES(?,?,?,?,?)',[(1,ident,'跟进中','不在推送响应显示','2026-10-13T10:30:00+08:00') for ident in range(1,13)])
 statements=[];connect=db.connect
 @contextmanager
 def traced_connect():
  with connect() as connection:
   connection.set_trace_callback(statements.append);yield connection
 monkeypatch.setattr(db,'connect',traced_connect)
 result=client.get('/api/pushes?page_size=5&page=2').json()
 assert len(result['items'])==5
 assert all(item.get('has_followup') is True and item.get('status')=='跟进中' for item in result['items'])
 assert len([sql for sql in statements if 'FROM FOLLOWUPS' in sql.upper()])==1


def test_later_topic_association_keeps_original_delivery_and_read_history(client,monkeypatch):
 from app import workbench
 first_topic=client.post('/api/subscriptions',json={'name':'控制','keywords':['控制']}).json()['id']
 add_push_notice('控制系统招标公告','https://example.com/late-topic','2026-09-01T09:00:00+08:00')
 workbench.dispatch_due(force=True)
 original=client.get('/api/pushes').json()['items'][0]
 client.patch(f"/api/pushes/{original['push_id']}/read")
 read_at=client.get('/api/pushes').json()['items'][0]['read_at']
 monkeypatch.setattr(db,'now',lambda:'2026-10-13T10:30:00+08:00')
 assert client.patch(f"/api/opportunities/{original['id']}",json={'status':'已联系','note':'同一公告的个人备注'}).status_code==200
 second_topic=client.post('/api/subscriptions',json={'name':'招标','keywords':['招标']}).json()['id']
 workbench.dispatch_due(force=True)
 result=client.get('/api/pushes').json()
 assert result['total']==1
 current=result['items'][0]
 assert {t['id'] for t in current['associated_topics']}=={first_topic,second_topic}
 for key in ('push_id','pushed_at','matched_topics','topic_reasons','matched_keywords','topic_score'):
  assert current[key]==original[key]
 assert current['read_at']==read_at
 for topic_id in (first_topic,second_topic):
  topic_push=client.get(f'/api/pushes?subscription_id={topic_id}').json()['items'][0]
  assert topic_push.get('status')=='已联系' and topic_push.get('has_followup') is True
  assert topic_push.get('followup_updated_at')=='2026-10-13T10:30:00+08:00'
  assert topic_push['status']==client.get(f"/api/opportunities/{original['id']}?subscription_id={topic_id}").json()['status']
 assert client.get('/api/automation').json()['last_count']==0
 assert client.get(f'/api/pushes?subscription_id={second_topic}').json()['total']==1
 counts={t['id']:t['push_count'] for t in client.get('/api/my-subscriptions').json()['items']}
 assert counts=={first_topic:1,second_topic:1}
 workbench.dispatch_due(force=True)
 assert len(client.get('/api/pushes').json()['items'][0]['associated_topics'])==2

def test_push_topic_filter_composes_with_search_read_and_legacy_payload(client):
 from app import workbench
 control=client.post('/api/subscriptions',json={'name':'控制','keywords':['控制']}).json()['id']
 equipment=client.post('/api/subscriptions',json={'name':'设备','keywords':['设备']}).json()['id']
 add_push_notice('控制招标公告','https://example.com/control','2026-09-01T09:00:00+08:00','控制采购需求')
 add_push_notice('设备招标公告','https://example.com/equipment','2026-09-02T09:00:00+08:00','设备采购需求')
 workbench.dispatch_due(force=True)
 filtered=client.get(f'/api/pushes?subscription_id={control}&page_size=5').json()
 assert filtered['total']==1 and filtered['items'][0]['title']=='控制招标公告'
 item=filtered['items'][0]
 # Older durable rows do not have associated_topics until their next match.
 with db.connect() as c:
  payload=json.loads(c.execute('SELECT payload FROM pushes WHERE id=?',(item['push_id'],)).fetchone()[0])
  payload.pop('associated_topics',None)
  c.execute('UPDATE pushes SET payload=? WHERE id=?',(db.dumps(payload),item['push_id']))
 legacy=client.get(f'/api/pushes?subscription_id={control}&q=控制&read=unread').json()
 assert legacy['total']==1 and legacy['items'][0]['associated_topics']==legacy['items'][0]['matched_topics']
 client.patch(f"/api/pushes/{item['push_id']}/read")
 assert client.get(f'/api/pushes?subscription_id={control}&read=unread').json()['total']==0
 assert client.get(f'/api/pushes?subscription_id={control}&q=设备').json()['total']==0
 assert client.get(f'/api/pushes?subscription_id={equipment}&read=read').json()['total']==0
 counts={t['id']:t['push_count'] for t in client.get('/api/my-subscriptions').json()['items']}
 assert counts=={control:1,equipment:1}

def test_push_topic_filter_rejects_foreign_and_deleted_topics_even_for_admin(client):
 from app import workbench
 seed(client);workbench.dispatch_due(force=True)
 own=next(topic for topic in client.get('/api/my-subscriptions').json()['items'] if topic['name']=='控制')
 client.post('/api/users',json={'username':'alice','display_name':'业务','password':'testing-user-password'})
 client.post('/api/login',json={'username':'alice','password':'testing-user-password'})
 foreign=client.post('/api/subscriptions',json={'name':'他人的控制','keywords':['控制']}).json()['id']
 assert client.get(f"/api/pushes?subscription_id={own['id']}").status_code==404
 client.post('/api/login',json={'username':'admin','password':'testing-only-password'})
 assert client.get(f'/api/pushes?subscription_id={foreign}').status_code==404
 client.put(f"/api/subscriptions/{own['id']}",json={**own,'enabled':False})
 assert client.get(f"/api/pushes?subscription_id={own['id']}").json()['total']==12
 client.delete(f"/api/subscriptions/{own['id']}")
 assert client.get(f"/api/pushes?subscription_id={own['id']}").status_code==404
 assert client.get('/api/pushes?subscription_id=99999').status_code==404
 assert client.get('/api/pushes').json()['total']==12

def test_subscription_counts_distinguish_current_matches_and_historical_pushes(client):
 from app import workbench
 first=client.post('/api/subscriptions',json={'name':'控制','keywords':['控制']}).json()['id']
 second=client.post('/api/subscriptions',json={'name':'招标','keywords':['招标']}).json()['id']
 add_push_notice('控制招标公告','https://example.com/counts','2026-09-01T09:00:00+08:00')
 before={t['id']:t for t in client.get('/api/my-subscriptions').json()['items']}
 assert before[first]['match_count']==before[second]['match_count']==1
 assert before[first]['push_count']==before[second]['push_count']==0
 workbench.dispatch_due(force=True)
 client.put(f'/api/subscriptions/{first}',json={'name':'控制','keywords':['控制'],'enabled':False})
 after={t['id']:t for t in client.get('/api/my-subscriptions').json()['items']}
 assert after[first]['match_count']==0 and after[first]['push_count']==1
 assert after[second]['match_count']==1 and after[second]['push_count']==1
 assert client.get('/api/pushes').json()['total']==1

@pytest.mark.parametrize('sort,expected',[
 ('pushed_at',['控制近期招标公告','控制旧招标公告','最新设备采购公告']),
 ('published_at',['最新设备采购公告','控制近期招标公告','控制旧招标公告']),
 ('score',['控制近期招标公告','控制旧招标公告','最新设备采购公告']),
])
def test_push_sort_keeps_same_batch_score_priority_and_supports_publication(client,monkeypatch,sort,expected):
 from app import workbench
 client.post('/api/subscriptions',json={'name':'控制','keywords':['控制']})
 add_push_notice('控制旧招标公告','https://example.com/rank-old','2026-09-01T09:00:00+08:00')
 add_push_notice('最新设备采购公告','https://example.com/rank-latest','2026-09-03T09:00:00+08:00')
 add_push_notice('控制近期招标公告','https://example.com/rank-recent','2026-09-02T09:00:00+08:00')
 frozen=db.now();monkeypatch.setattr(db,'now',lambda:frozen)
 workbench.dispatch_due(force=True)
 result=client.get(f'/api/pushes?sort={sort}').json()
 assert [x['title'] for x in result['items']]==expected
 assert len({x['pushed_at'] for x in result['items']})==1

def test_pushes_validate_sort_instead_of_ignoring_it(client):
 assert client.get('/api/pushes?sort=not-a-sort').status_code==422

def test_schedule_reports_process_scheduler_switch_without_changing_task_configuration(client,monkeypatch):
 disabled=client.get('/api/collection-schedule').json()
 assert disabled['scheduler_enabled'] is False and disabled['enabled'] is True
 assert client.get('/api/automation').json()['scheduler_enabled'] is False
 monkeypatch.setenv('SCHEDULER_ENABLED','true')
 enabled=client.get('/api/collection-schedule').json()
 assert enabled['scheduler_enabled'] is True and enabled['enabled'] is True
 assert client.get('/api/automation').json()['scheduler_enabled'] is True

@pytest.mark.parametrize('precision',['minute','day','unknown'])
def test_push_snapshot_retains_publication_precision_after_later_association(client,precision):
 from app import workbench
 client.post('/api/subscriptions',json={'name':'控制','keywords':['控制']})
 raw={'title':'控制系统招标公告','url':'https://example.com/publication-precision','source_id':'cecep','source_name':'来源','source_kind':'公开','published_at':'2026-09-01T00:00:00+08:00','published_at_precision':precision,'content':'控制设备采购需求'}
 db.upsert(analyze(raw,[],db.DEFAULT_SETTINGS))
 workbench.dispatch_due(force=True)
 original=client.get('/api/pushes').json()['items'][0]
 assert original['published_at_precision']==precision
 assert original['published_at']=='2026-09-01T00:00:00+08:00'
 # Subsequent source revisions and new associations do not rewrite delivery evidence.
 db.upsert(analyze({**raw,'published_at_precision':'day' if precision=='minute' else 'minute'},[],db.DEFAULT_SETTINGS))
 client.post('/api/subscriptions',json={'name':'招标','keywords':['招标']})
 workbench.dispatch_due(force=True)
 later=client.get('/api/pushes').json()['items'][0]
 assert later['published_at_precision']==('day' if precision=='minute' else 'minute')
 assert later['delivery_published_at_precision']==precision
 assert later['delivery_published_at']==original['published_at']
 assert later['pushed_at']==original['pushed_at']
 with db.connect() as c:
  snapshot=json.loads(c.execute('SELECT payload FROM pushes WHERE id=?',(later['push_id'],)).fetchone()[0])
 assert snapshot['published_at_precision']==precision
 assert len(later['associated_topics'])==2
