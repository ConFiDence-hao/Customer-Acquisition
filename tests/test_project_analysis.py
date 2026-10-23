"""Shared project reports honor live ownership and verify public evidence."""
import importlib
import importlib.util
from threading import Event
from time import monotonic,sleep

import pytest
from app import db,ai_jobs
from test_team_followups import client,login,notice,claim,assign,save


def module():
    assert importlib.util.find_spec('app.project_analysis') is not None,'shared project analysis is not implemented'
    return importlib.import_module('app.project_analysis')


def report(summary='共享报告'):
    return {'summary':summary,'procurement_need':'核实采购范围','fit':'能力待核实','questions':['核实参与条件'],
            'next_actions':['查阅来源原文'],'facts':[]}


def wait_report(client,ident,status='succeeded'):
    until=monotonic()+4
    while monotonic()<until:
        response=client.get(f'/api/opportunities/{ident}/project-analysis')
        assert response.status_code==200,response.text
        data=response.json()
        if data['status']==status:return data
        sleep(.02)
    pytest.fail('project report did not reach expected state: '+str(data))


def test_claim_automatically_generates_one_shared_report_without_private_notes(client,monkeypatch):
    project=module();seen=[]
    monkeypatch.setattr(project,'generate',lambda context:seen.append(context) or report())
    ident=notice('report-claim')
    login(client,'alice');save(client,ident,note='alice-private-token')
    with db.connect() as c:assert c.execute("SELECT COUNT(*) FROM ai_jobs WHERE kind='project_analysis'").fetchone()[0]==0
    claim(client,ident)
    own=wait_report(client,ident)
    assert own['report']['summary']=='共享报告'
    assert 'alice-private-token' not in str(seen)
    submitted=client.post(f'/api/opportunities/{ident}/project-analysis')
    login(client,'admin')
    assert client.get(f'/api/opportunities/{ident}/project-analysis').json()['report']==own['report']
    assert client.post(f'/api/opportunities/{ident}/project-analysis').json()['id']==submitted.json()['id']
    assert client.get(f'/api/opportunities/{ident}').json()['project_analysis']['report']==own['report']
    login(client,'bobby')
    assert client.get(f'/api/opportunities/{ident}/project-analysis').status_code==403
    assert client.get(f'/api/opportunities/{ident}').json().get('project_analysis') is None
    with db.connect() as c:assert c.execute("SELECT COUNT(*) FROM notifications WHERE kind='report_ready'").fetchone()[0]==0


def test_assignment_uses_actual_recipient_business_context_and_old_owner_loses_access(client,monkeypatch):
    project=module();seen=[]
    monkeypatch.setattr(project,'generate',lambda context:seen.append(context) or report())
    ident=notice('report-assignment')
    alice=login(client,'alice')
    client.post('/api/subscriptions',json={'name':'Alice 控制业务','keywords':['控制'],'offering':'Alice 的控制服务'})
    login(client,'admin')
    client.post('/api/subscriptions',json={'name':'管理员业务','keywords':['控制'],'offering':'管理员专属服务'})
    assign(client,ident,alice)
    done=wait_report(client,ident)
    assert 'Alice 的控制服务' in str(seen) and '管理员专属服务' not in str(seen)
    bobby=login(client,'bobby');login(client,'admin');assign(client,ident,bobby)
    login(client,'alice')
    assert client.get(f'/api/opportunities/{ident}/project-analysis').status_code==403
    assert client.get(f"/api/ai-jobs/{done['job_id']}").status_code==403


def test_a_to_b_to_a_rejects_old_publication_and_does_not_emit_ready_event(client,monkeypatch):
    project=module();gate=Event();started=Event();calls=[]
    def generate(context):
        calls.append(context)
        if len(calls)==1:started.set();gate.wait(3);return report('obsolete-A-token')
        return report('fresh-current-token')
    monkeypatch.setattr(project,'generate',generate)
    ident=notice('report-revisions')
    alice=login(client,'alice');claim(client,ident)
    assert started.wait(2)
    bobby=login(client,'bobby');login(client,'admin');assign(client,ident,bobby);assign(client,ident,alice)
    gate.set();done=wait_report(client,ident)
    assert done['report']['summary']=='fresh-current-token'
    with db.connect() as c:
        old=c.execute("SELECT * FROM ai_jobs WHERE kind='project_analysis' ORDER BY id LIMIT 1").fetchone()
        assert old['status']=='stale' and old['result'] is None
        assert not c.execute("SELECT 1 FROM notifications WHERE kind='report_ready' AND owner_revision=?",(old['owner_revision'],)).fetchone()


def test_failed_regeneration_preserves_old_report_and_notifies_admin(client,monkeypatch):
    project=module();monkeypatch.setattr(project,'generate',lambda context:report('old-valid-report'))
    ident=notice('report-failure');alice=login(client,'alice');claim(client,ident)
    wait_report(client,ident)
    def fail(context):raise RuntimeError('do-not-leak-private-provider-token')
    monkeypatch.setattr(project,'generate',fail)
    with db.connect() as c:
        row=c.execute('SELECT payload FROM opportunities WHERE id=?',(ident,)).fetchone()
        import json
        payload=json.loads(row[0]);payload['content']='新正文证据：控制系统范围更新。'
        c.execute('UPDATE opportunities SET payload=? WHERE id=?',(db.dumps(payload),ident))
    client.post(f'/api/opportunities/{ident}/project-analysis')
    failed=wait_report(client,ident,'failed')
    assert failed['report']['summary']=='old-valid-report' and failed['stale'] is True
    assert 'do-not-leak' not in str(failed)
    with db.connect() as c:assert c.execute("SELECT COUNT(*) FROM notifications WHERE kind='report_failed'").fetchone()[0]==0


def test_verified_facts_reject_fabricated_amounts_and_empty_body(client,monkeypatch):
    project=module()
    proposed={**report(),'facts':[{'label':'采购范围','value':'控制系统','evidence':'控制系统采购需求。'},
                                {'label':'预算','value':'100万元','evidence':'预算100万元。'}]}
    monkeypatch.setattr(project,'generate',lambda context:proposed)
    ident=notice('report-facts');login(client,'alice');claim(client,ident)
    done=wait_report(client,ident)
    assert len(done['report']['facts'])==1 and done['report']['facts'][0]['url'].startswith('https://')
    assert '100万元' not in str(done['report'])
    with db.connect() as c:
        import json
        payload=json.loads(c.execute('SELECT payload FROM opportunities WHERE id=?',(ident,)).fetchone()[0]);payload['content']=''
        c.execute('UPDATE opportunities SET payload=? WHERE id=?',(db.dumps(payload),ident))
    client.post(f'/api/opportunities/{ident}/project-analysis')
    done=wait_report(client,ident)
    assert done['report']['facts']==[] and '仅标题' in done['report']['evidence_limit']


def test_score_and_last_seen_do_not_invalidate_raw_evidence_cache(client,monkeypatch):
    project=module();monkeypatch.setattr(project,'generate',lambda context:report())
    ident=notice('report-cache');login(client,'alice');claim(client,ident)
    first=wait_report(client,ident)
    with db.connect() as c:
        import json
        payload=json.loads(c.execute('SELECT payload FROM opportunities WHERE id=?',(ident,)).fetchone()[0]);payload.update(score=99,analysis_date='2099-01-01',detail_fetched_at=db.now())
        c.execute('UPDATE opportunities SET payload=?,score=99,last_seen_at=?,content_hash=? WHERE id=?',(db.dumps(payload),db.now(),'different-derived-hash',ident))
    same=client.post(f'/api/opportunities/{ident}/project-analysis')
    assert same.status_code==200 and same.json()['id']==first['job_id']
    assert client.get(f'/api/opportunities/{ident}/project-analysis').json()['stale'] is False


def test_unowned_admin_active_followup_triggers_but_archived_transfer_does_not(client,monkeypatch):
    project=module();monkeypatch.setattr(project,'generate',lambda context:report())
    ident=notice('admin-active');save(client,ident,'跟进中','admin-private-token')
    wait_report(client,ident)
    alice=login(client,'alice');claim(client,ident);wait_report(client,ident)
    save(client,ident,'已归档')
    with db.connect() as c:before=c.execute("SELECT COUNT(*) FROM ai_jobs WHERE kind='project_analysis'").fetchone()[0]
    bobby=login(client,'bobby');login(client,'admin');assign(client,ident,bobby)
    with db.connect() as c:assert c.execute("SELECT COUNT(*) FROM ai_jobs WHERE kind='project_analysis'").fetchone()[0]==before
    login(client,'bobby')
    assert client.post(f'/api/opportunities/{ident}/project-analysis').status_code==200
    wait_report(client,ident)


def test_backfill_rotates_bounded_batches_without_copying_private_reports(client,monkeypatch):
    project=module();monkeypatch.setattr(project,'generate',lambda context:report())
    ai_jobs.stop();login(client,'alice')
    for i in range(7):claim(client,notice(f'backfill-{i}'))
    with db.connect() as c:c.execute("DELETE FROM ai_jobs WHERE kind='project_analysis'")
    project.SCAN_CURSOR.clear()
    for expected in (3,6,7):
        project.backfill(limit=3)
        with db.connect() as c:
            assert c.execute("SELECT COUNT(*) FROM ai_jobs WHERE kind='project_analysis'").fetchone()[0]==expected
            assert not c.execute("SELECT 1 FROM ai_jobs WHERE kind='project_analysis' AND priority!='background'").fetchone()


def test_human_claim_assignment_and_active_followup_reports_are_foreground(client,monkeypatch):
    project=module();ai_jobs.stop()
    first=notice('priority-claim');alice=login(client,'alice');claim(client,first)
    login(client,'admin');second=notice('priority-assignment');assign(client,second,alice)
    third=notice('priority-followup');save(client,third,'跟进中')
    for ident in (first,second,third):
        with db.connect() as c:job=c.execute("SELECT * FROM ai_jobs WHERE opportunity_id=? ORDER BY id DESC LIMIT 1",(ident,)).fetchone()
        assert job['priority']=='foreground'
        data=client.get(f'/api/opportunities/{ident}/project-analysis').json()
        assert data['progress']['stage']=='queued' and data['progress']['estimated'] is True


def test_current_owner_can_retry_failed_shared_job_but_former_owner_cannot(client,monkeypatch):
    project=module()
    def fail(context):raise RuntimeError('safe test failure')
    monkeypatch.setattr(project,'generate',fail)
    ident=notice('retry');alice=login(client,'alice');claim(client,ident)
    failed=wait_report(client,ident,'failed')
    monkeypatch.setattr(project,'generate',lambda context:report('retried report'))
    assert client.post(f"/api/ai-jobs/{failed['job_id']}/retry").status_code==200
    assert wait_report(client,ident)['report']['summary']=='retried report'


def test_failed_automatic_backfill_does_not_create_more_jobs_or_failure_notifications(client,monkeypatch):
    project=module()
    monkeypatch.setattr(project,'generate',lambda context:(_ for _ in ()).throw(RuntimeError('safe synthetic failure')))
    ident=notice('no-failure-spam');login(client,'alice');claim(client,ident);wait_report(client,ident,'failed')
    with db.connect() as c:
        jobs=c.execute("SELECT COUNT(*) FROM ai_jobs WHERE kind='project_analysis'").fetchone()[0]
        notifications=c.execute("SELECT COUNT(*) FROM notifications WHERE kind='report_failed'").fetchone()[0]
    for _ in range(3):project.SCAN_CURSOR.clear();project.backfill()
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM ai_jobs WHERE kind='project_analysis'").fetchone()[0]==jobs
        assert c.execute("SELECT COUNT(*) FROM notifications WHERE kind='report_failed'").fetchone()[0]==notifications==0


def test_authorized_lists_show_job_state_without_disclosing_report_text(client,monkeypatch):
    from app import workbench
    project=module();monkeypatch.setattr(project,'generate',lambda context:report('shared-sensitive-report-token'))
    ident=notice('report-lists');login(client,'alice')
    client.post('/api/subscriptions',json={'name':'控制专题','keywords':['控制']})
    claim(client,ident);wait_report(client,ident);workbench.dispatch_due(force=True)
    for endpoint in ('/api/opportunities','/api/followups','/api/pushes'):
        data=client.get(endpoint).json()
        assert data['items'][0].get('project_ai_status')=='succeeded'
        assert 'shared-sensitive-report-token' not in str(data)
    login(client,'admin')
    assert client.get('/api/team-followups').json()['items'][0].get('project_ai_status')=='succeeded'
    login(client,'bobby')
    assert 'project_ai_status' not in client.get('/api/opportunities').json()['items'][0]
