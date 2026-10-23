import json
from contextlib import contextmanager
import pytest
import httpx
from app import ai
from test_team_followups import client, notice

def test_model_receives_current_time_and_cannot_recommend_expired_registration(monkeypatch):
    monkeypatch.setattr(ai.model_config,'stored',lambda:{})
    monkeypatch.setenv('LLM_BASE_URL','http://test.invalid/v1')
    monkeypatch.setenv('LLM_MODEL','test-model')
    recorded={}
    def handle(request):
        recorded.update(json=json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'summary':'摘要','fit':'切入点','questions':[],'suggested_actions':['昨天去下载文件']})}}]})
    async_client=httpx.AsyncClient
    monkeypatch.setattr(ai.ai_provider.httpx,'AsyncClient',lambda **kwargs:async_client(transport=httpx.MockTransport(handle),**kwargs))
    result=ai.explain({'title':'AI升级采购','document_state':'expired','next_actions':['先核实是否已获取文件']})
    evidence=json.loads(recorded['json']['messages'][1]['content'])
    assert evidence['current_time'].endswith('+08:00')
    assert result['suggested_actions']==['先核实是否已获取文件']
    assert result['summary'].startswith('公开列表标题：') and '不能据此认定预算' in result['summary']
    assert recorded['json']['response_format']['type']=='json_schema'
    assert recorded['json']['response_format']['json_schema']['strict'] is True
    assert recorded['json']['response_format']['json_schema']['schema']['properties']['questions']['items']=={'type':'string'}
    assert recorded['json']['max_tokens']>=1600

def test_scheduled_batch_enqueues_at_most_five_and_dispatch_does_not_wait_for_ai(client,monkeypatch):
    from threading import Event
    from time import monotonic
    from app import db,main
    monkeypatch.setattr(ai,'config',lambda:{'enabled':True,'auto_analyze':True})
    for i in range(7):notice(f'batch-{i}')
    with db.connect() as c:c.execute("UPDATE opportunities SET score=90,payload=json_set(payload,'$.score',90)")
    release=Event();dispatched=[]
    def generate(item):
        release.wait(3)
        return {'summary':'队列测试','fit':'待核实','questions':[],'suggested_actions':[]}
    monkeypatch.setattr(ai,'explain',generate)
    monkeypatch.setattr(main,'run_collection',lambda ident:None)
    monkeypatch.setattr(main.workbench,'progress',lambda *args:None)
    monkeypatch.setattr(main.workbench,'dispatch_due',lambda:dispatched.append(True))
    assert main.RUN_LOCK.acquire(blocking=False)
    began=monotonic()
    try:
        main.run_worker(0)
        assert monotonic()-began<1 and dispatched==[True]
        with db.connect() as c:
            jobs=c.execute('SELECT status,priority FROM ai_jobs').fetchall()
        assert len(jobs)==5 and all(row['priority']=='background' for row in jobs)
        assert all(row['status'] in ('queued','running') for row in jobs)
    finally:release.set()


def test_automatic_public_analysis_only_selects_scores_at_least_85(client,monkeypatch):
    from app import db,ai_job_requests
    monkeypatch.setattr(ai,'config',lambda:{'enabled':True,'auto_analyze':True})
    selected=[];ids={score:notice(f'score-boundary-{score}') for score in (84,85,90)}
    with db.connect() as c:
        for score,ident in ids.items():
            c.execute("UPDATE opportunities SET score=?,payload=json_set(payload,'$.score',?) WHERE id=?",(score,score,ident))
    monkeypatch.setattr(ai_job_requests,'submit_analysis',lambda ident,user,priority:selected.append((ident,priority)))
    ai.analyze_top()
    assert [ident for ident,_ in selected]==[ids[90],ids[85]]
    assert all(priority=='background' for _,priority in selected)
