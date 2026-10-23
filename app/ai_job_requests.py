"""Server-owned requests for the shared AI queue and bounded legacy compatibility."""
import hashlib
import json
from datetime import timedelta
from time import monotonic, sleep
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from . import accounts, ai, ai_jobs, assist, db, subscriptions,clock,querying

router=APIRouter(prefix='/api/ai-jobs')
TERMINAL={'succeeded','failed','stale','waiting_config'}
LEGACY_WAIT_SECONDS=15


class JobRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['assist','public_analysis','topic_analysis','connection_test']
    request:assist.AssistRequest|None=None
    opportunity_id:int|None=None
    subscription_id:int|None=None


def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def base(kind,user,payload,visibility='private',**context):
    return {'kind':kind,'requested_by_user_id':user['id'],'context_user_id':user['id'],
            'visibility':visibility,'opportunity_id':None,'subscription_id':None,'owner_revision':None,
            'priority':'foreground','payload':payload,'input_hash':digest(payload),**context}


def public_item(ident,c=None):
    if c is None:
        with db.connect() as connection:return public_item(ident,connection)
    row=c.execute('SELECT * FROM opportunities WHERE id=?',(ident,)).fetchone()
    if not row:raise HTTPException(404,'公告不存在')
    item=db.unpack(row)
    # The legacy schema may have global notes: never send them to a model or store them in a job.
    allowed=('id','title','url','source_id','source_name','stage','category','deadline','document_deadline',
             'document_state','temporal_state','score','actionable','buyer','budget_text','risks',
             'content','next_actions','superseded_by','published_at','province','observed_source_ids')
    return {key:item[key] for key in allowed if key in item},row['content_hash']


def assist_spec(body,user):
    context=assist.prepare(body,user)
    payload={'target':body.target,'context':context,'request':body.model_dump(exclude={'current'}),
             'safe_current':context['current']}
    revision=None;evidence=None
    if body.target=='followup':
        with db.connect() as c:
            revision=ai_jobs.project_revision(c,body.opportunity_id)
            if body.owner_revision is not None and body.owner_revision!=revision:raise HTTPException(409,'负责人已变化，请刷新项目后重试')
            _,evidence=public_item(body.opportunity_id,c)
        payload['evidence_hash']=evidence
    spec=base('assist',user,payload,opportunity_id=body.opportunity_id if body.target=='followup' else None,owner_revision=revision)
    if body.target.startswith('search_'):
        now=clock.now()
        payload['query_meta']={'submitted_at':now.isoformat(),'valid_until':(now+timedelta(minutes=5)).isoformat(),
                               'reference_date':now.date().isoformat(),'rules_version':1}
        stable=json.loads(db.dumps(payload));stable.pop('query_meta');stable['context'].pop('reference_time',None)
        stable['query_rules_version']=1
        spec['input_hash']=digest(stable)
    return spec


def analysis_spec(ident,user,subscription_id=None,priority='foreground'):
    db.refresh_expired()
    item,evidence=public_item(ident)
    payload={'item':item,'evidence_hash':evidence}
    if subscription_id:
        topic=subscriptions.get(subscription_id,user)
        # attach_analysis only adds this user's business context; discard its historical private report.
        item=subscriptions.attach_analysis(item,user,topic);item.pop('ai_analysis',None)
        payload.update(item=item,topic_hash=subscriptions.topic_hash(topic))
        return base('topic_analysis',user,payload,opportunity_id=ident,subscription_id=subscription_id,priority=priority)
    if user['role']!='admin':raise HTTPException(403,'公共 AI 解读由管理员生成')
    return base('public_analysis',user,payload,'public',opportunity_id=ident,context_user_id=None,priority=priority)


def submit_assist(body,user):return ai_jobs.enqueue(assist_spec(body,user))
def submit_analysis(ident,user,subscription_id=None,priority='foreground'):
    return ai_jobs.enqueue(analysis_spec(ident,user,subscription_id,priority))
def submit_connection(user):
    if user['role']!='admin':raise HTTPException(403,'仅管理员可测试模型连接')
    return ai_jobs.enqueue(base('connection_test',user,{'item':{'title':'系统连接测试','content':'这是一条连接测试，不包含真实项目或联系人信息。','stage':'资讯'}}))


def valid_evidence(job,c):
    meta=job['payload'].get('query_meta')
    if meta and (clock.now()>=querying.parse_time(meta['valid_until']) or clock.today().isoformat()!=meta['reference_date']):return False
    if job.get('opportunity_id'):
        row=c.execute('SELECT content_hash FROM opportunities WHERE id=?',(job['opportunity_id'],)).fetchone()
        if not row or row['content_hash']!=job['payload'].get('evidence_hash'):return False
    if job['kind']=='topic_analysis':
        row=c.execute('SELECT * FROM subscriptions WHERE id=? AND user_id=? AND deleted=0',
                      (job['subscription_id'],job['requested_by_user_id'])).fetchone()
        if not row or subscriptions.topic_hash(subscriptions.unpack(row))!=job['payload']['topic_hash']:return False
    return True


def publish_analysis(job,result,c):
    if job['kind']=='public_analysis':
        row=c.execute('SELECT payload FROM opportunities WHERE id=?',(job['opportunity_id'],)).fetchone()
        payload=json.loads(row['payload']);payload['ai_analysis']=result
        c.execute('UPDATE opportunities SET payload=? WHERE id=?',(db.dumps(payload),job['opportunity_id']))
    else:
        result['notice']='按订阅生成的个人解读；事实以原公告为准'
        c.execute('INSERT INTO topic_analyses VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,subscription_id,opportunity_id) DO UPDATE SET evidence_hash=excluded.evidence_hash,topic_hash=excluded.topic_hash,payload=excluded.payload,created_at=excluded.created_at',
                  (job['requested_by_user_id'],job['subscription_id'],job['opportunity_id'],job['payload']['evidence_hash'],job['payload']['topic_hash'],db.dumps(result),db.now()))
    c.execute('INSERT INTO audit(created_at,action,target,detail) VALUES(?,?,?,?)',(db.now(),'AI解读',str(job['opportunity_id']),'任务：'+str(job['id'])))


def connection_valid(job,c):
    actor=ai_jobs.live_user(c,job['requested_by_user_id'])
    return bool(actor and actor['role']=='admin')


def register():
    ai_jobs.register_handler('assist',assist.run_job,valid_evidence)
    for kind in ('public_analysis','topic_analysis'):
        ai_jobs.register_handler(kind,lambda job:ai.explain(job['payload']['item']),valid_evidence,publish_analysis)
    ai_jobs.register_handler('connection_test',lambda job:ai.explain(job['payload']['item']),connection_valid)


def wait(ident,user,timeout=None):
    until=monotonic()+(LEGACY_WAIT_SECONDS if timeout is None else timeout)
    while True:
        job=ai_jobs.get(ident,user)
        if job['status']=='succeeded':return job
        if job['status'] in TERMINAL:
            raise HTTPException(job.get('error_status') or (409 if job['status']=='stale' else 503),job['error'] or 'AI任务未完成，原有内容已保留')
        if monotonic()>=until:raise HTTPException(504,{'message':'任务继续排队处理，可通过任务编号查看结果','job_id':ident})
        sleep(0.05)


@router.post('')
def submit(body:JobRequest,user=Depends(accounts.require_user)):
    if body.kind=='assist':
        if not body.request:raise HTTPException(422,'请提供智能辅助需求')
        if body.opportunity_id or body.subscription_id:raise HTTPException(422,'智能辅助上下文请放在 request 中')
        return submit_assist(body.request,user)
    if body.request:raise HTTPException(422,'此任务不接受表单请求')
    if body.kind=='connection_test':
        if body.opportunity_id or body.subscription_id:raise HTTPException(422,'连接测试不接受项目上下文')
        return submit_connection(user)
    if not body.opportunity_id or (body.kind=='topic_analysis')!=bool(body.subscription_id):raise HTTPException(422,'项目或订阅上下文不完整')
    return submit_analysis(body.opportunity_id,user,body.subscription_id)


@router.get('/{ident}')
def get(ident:int,user=Depends(accounts.require_user)):return ai_jobs.get(ident,user)


@router.post('/{ident}/retry')
def retry(ident:int,user=Depends(accounts.require_user)):
    ai_jobs.get(ident,user)  # Live read authorization applies before rebuilding, including former owners.
    with db.connect() as c:job=ai_jobs.unpack(c.execute('SELECT * FROM ai_jobs WHERE id=?',(ident,)).fetchone())
    if job['requested_by_user_id']!=user['id'] and job['visibility']!='project':raise HTTPException(403,'只能重试自己的任务')
    if job['kind']=='assist':
        request={**job['payload']['request'],'current':job['payload']['safe_current']}
        if request['target']=='followup':
            with db.connect() as c:request['owner_revision']=ai_jobs.project_revision(c,job['opportunity_id'])
        new=submit_assist(assist.AssistRequest(**request),user)
    elif job['kind'] in ('public_analysis','topic_analysis'):new=submit_analysis(job['opportunity_id'],user,job['subscription_id'])
    elif job['kind']=='connection_test':new=submit_connection(user)
    elif job['kind']=='project_analysis':
        from . import project_analysis
        new=project_analysis.submit(job['opportunity_id'],user)
    else:raise HTTPException(422,'此任务请从项目页面重新生成')
    if new['id']==ident and job['status'] in ('failed','stale'):
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            fresh=ai_jobs.unpack(c.execute('SELECT * FROM ai_jobs WHERE id=?',(ident,)).fetchone())
            if not ai_jobs.context_valid(c,fresh):raise HTTPException(409,'上下文已变化，请重新发起')
            c.execute("UPDATE ai_jobs SET status='queued',error=NULL,error_status=NULL,started_at=NULL,finished_at=NULL,available_at=?,updated_at=? WHERE id=? AND status IN ('failed','stale')",(db.now(),db.now(),ident))
        return ai_jobs.get(ident,user)
    return new
