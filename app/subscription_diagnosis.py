"""Verified delivery diagnostics. AI explains safe statistics; it never changes rules."""
import hashlib
import json
import os
from time import monotonic
from fastapi import APIRouter,Depends,HTTPException
from . import accounts,ai_jobs,ai_provider,clock,db,subscriptions,workbench

router=APIRouter(prefix='/api/subscriptions')
FACETS=[('source_ids','资讯来源'),('provinces','地区'),('stages','公告阶段'),('keywords','关键词'),('exclude_keywords','标题排除词')]


def digest(value):return hashlib.sha256(db.dumps(value).encode()).hexdigest()


def _collect(topic_id,user,c):
    actor=ai_jobs.live_user(c,user['id'])
    if not actor:raise HTTPException(403,'当前账号不可用')
    row=c.execute('SELECT s.*,u.display_name AS owner_name,u.enabled AS owner_enabled FROM subscriptions s JOIN users u ON s.user_id=u.id WHERE s.id=? AND s.deleted=0',(topic_id,)).fetchone()
    if not row or actor['role']!='admin' and row['user_id']!=actor['id']:raise HTTPException(404,'未找到该订阅')
    topic=subscriptions.unpack(row);owner_id=topic['user_id']
    # Public library only: no personal overlay, notes, read state, or historical push body.
    items=db.unpack_many(c,c.execute('SELECT '+db.LIGHT_COLUMNS+' FROM opportunities').fetchall())
    decisions=[(item,subscriptions.match_decision(item,topic)) for item in items]
    matched=[item for item,decision in decisions if decision['allowed']]
    remaining=len(items);funnel=[]
    for index,(key,label) in enumerate(FACETS):
        count=sum(all(d['conditions'][k] for k,_ in FACETS[:index+1]) for _,d in decisions)
        relaxed=sum(all(ok for k,ok in d['conditions'].items() if k!=key) for _,d in decisions)
        funnel.append({'key':key,'label':label,'before':remaining,'remaining':count,'removed':remaining-count,'without_condition':relaxed})
        remaining=count
    deliveries={}
    for push in c.execute('SELECT opportunity_id,payload FROM pushes WHERE user_id=?',(owner_id,)):
        # Inspect associations only; payload is never returned or sent to the provider.
        deliveries[push['opportunity_id']]={t['id'] for t in workbench.associated_topics(json.loads(push['payload']))}
    counts={'matching':len(matched),'undelivered':0,'delivered_for_topic':0,'delivered_by_other_topics':0,'pending_topic_association':0,'push_filtered':0}
    filtered={key:0 for key in subscriptions.PUSH_SKIPS}
    current=clock.now();decided={item['id']:d for item,d in decisions if d['allowed']}
    for item in matched:
        assoc=deliveries.get(item['id'])
        if assoc is None:
            gate=subscriptions.push_decision(item,topic,decided[item['id']],current)
            if gate['push']:counts['undelivered']+=1
            else:counts['push_filtered']+=1;filtered[gate['reason']]+=1
        elif topic_id in assoc:counts['delivered_for_topic']+=1
        else:
            counts['pending_topic_association']+=1
            if assoc:counts['delivered_by_other_topics']+=1
    plan=c.execute('SELECT enabled,interval_minutes,last_run,last_count FROM automations WHERE user_id=?',(owner_id,)).fetchone()
    plan=dict(plan) if plan else {'enabled':True,'interval_minutes':60,'last_run':None,'last_count':0}
    plan['enabled']=bool(plan['enabled']);requested,checked=workbench.check_revision(c,owner_id)
    plan.update(scheduler_enabled=os.getenv('SCHEDULER_ENABLED','true').lower()=='true',pending_check=requested>checked,requested_revision=requested,checked_revision=checked)
    from .collectors import source_rows
    sources=[]
    for source in source_rows():
        if topic['source_ids'] and source['id'] not in topic['source_ids']:continue
        sources.append({k:source.get(k) for k in ('id','name','status','last_success','last_count')}|{'enabled':bool(source['enabled']),'adapter_available':bool(source['adapter_available'])})
    available=sum(s['enabled'] and s['adapter_available'] for s in sources)
    reasons=[]
    for condition,code in [(not row['owner_enabled'],'account_disabled'),(not topic['enabled'],'subscription_paused'),(not plan['enabled'],'automation_paused'),(not plan['scheduler_enabled'],'scheduler_paused'),(not plan['last_run'],'first_check_pending'),(plan['pending_check'] and bool(plan['last_run']),'revision_check_pending'),(not available,'source_unavailable'),(not counts['matching'],'no_matches'),(counts['pending_topic_association']>0,'topic_association_pending')]:
        if condition:reasons.append(code)
    samples=[{key:item.get(key) for key in ('id','title','url','source_name','published_at')} for item in matched[:3]]
    for sample in samples:
        decision=next(d for item,d in decisions if item['id']==sample['id']);sample['matched_keywords']=decision['hits']
    return {'topic':{k:topic[k] for k in ('id','user_id','name','industry','offering','keywords','exclude_keywords','provinces','stages','source_ids','enabled','owner_name')},
            'account_enabled':bool(row['owner_enabled']),'library_count':len(items),'counts':counts,'funnel':funnel,'automation':plan,
            'sources':sources,'available_source_count':available,'clock':{'state':clock.status()['state'],'writable':clock.writable()},
            'reasons':reasons,'samples':samples,'push_filters':[{'code':k,'label':v,'count':filtered[k]} for k,v in subscriptions.PUSH_SKIPS.items()],
            'notice':'按当前公共库统计，不能证明网站没有该业务。去除单项条件只作预览；同账号同公告只推送一次，新增为零不等于无匹配。'}


def collect(topic_id,user):
    # Same temporal refresh as recommendations, without personal records.
    db.refresh_expired()
    with db.connect() as c:result=_collect(topic_id,user,c)
    return {**result,'checked_at':db.now()}


def valid_context(job,c):
    actor=ai_jobs.live_user(c,job['requested_by_user_id'])
    if not actor:return False
    try:current=_collect(job['subscription_id'],actor,c)
    except HTTPException:return False
    return digest(current)==job['input_hash']


def explain(diagnosis):
    schema={'type':'object','properties':{'summary':{'type':'string','maxLength':1200},'suggestions':{'type':'array','maxItems':5,'items':{'type':'string','maxLength':300}}},'required':['summary','suggestions'],'additionalProperties':False}
    messages=[{'role':'system','content':'你解释已核验的订阅推送统计，只根据输入事实；明确区分库内无匹配、任务暂停、未检查、其他主题已送达和待关联。漏斗按给定顺序；单项去除是预览。不得声称网站没有业务，不修改规则、不启用任务、不执行采集。不把新增为零解释为无匹配。输出简短中文 JSON。'}, {'role':'user','content':db.dumps(diagnosis)}]
    return ai_provider.complete_json(messages,schema,task_kind='subscription_diagnosis',config=ai_provider.current_config(),budget=4096,deadline=monotonic()+360)['data']


def register():ai_jobs.register_handler('diagnosis',lambda job:explain(job['payload']['diagnosis']),valid_context)


def enqueue_explanation(topic_id,user):
    db.refresh_expired()
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE');snapshot=_collect(topic_id,user,c)
        return ai_jobs.enqueue({'kind':'diagnosis','requested_by_user_id':user['id'],'context_user_id':snapshot['topic']['user_id'],'visibility':'private',
            'opportunity_id':None,'subscription_id':topic_id,'owner_revision':None,'priority':'foreground','payload':{'diagnosis':snapshot},'input_hash':digest(snapshot)},connection=c)


@router.get('/{topic_id}/diagnosis')
def get_diagnosis(topic_id:int,user=Depends(accounts.require_user)):return collect(topic_id,user)


@router.post('/{topic_id}/diagnosis/explain')
def explain_diagnosis(topic_id:int,user=Depends(accounts.require_user)):return enqueue_explanation(topic_id,user)
