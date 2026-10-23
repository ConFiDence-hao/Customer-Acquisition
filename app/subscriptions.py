"""Per-account topics match the shared public library independently of industry score."""
from . import clock
import json
import hashlib
from datetime import datetime,timedelta
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from . import db, accounts

class Topic(BaseModel):
    name:str=Field(min_length=1,max_length=60)
    industry:str=Field(default='',max_length=100)
    offering:str=Field(default='',max_length=1000)
    keywords:list[str]=Field(min_length=1,max_length=30)
    exclude_keywords:list[str]=Field(default_factory=list,max_length=50)
    provinces:list[str]=Field(default_factory=list,max_length=31)
    stages:list[str]=Field(default_factory=list,max_length=8)
    source_ids:list[str]=Field(default_factory=list,max_length=40)
    enabled:bool=True

    @field_validator('keywords','exclude_keywords','provinces','stages','source_ids')
    @classmethod
    def clean_list(cls,values):
        if any(not v.strip() or len(v)>60 for v in values):raise ValueError('词条需为1–60个字符')
        return list(dict.fromkeys(v.strip() for v in values))

    @field_validator('name')
    @classmethod
    def clean_name(cls,v):
        if not v.strip():raise ValueError('专题名称不能为空')
        return v.strip()

    @field_validator('source_ids')
    @classmethod
    def valid_sources(cls,values):
        from .collectors import source_rows
        allowed={s['id'] for s in source_rows() if s['enabled'] or s['adapter_available']}
        if any(v not in allowed for v in values):raise ValueError('仅能订阅已接入的采集来源')
        return values

    @field_validator('stages')
    @classmethod
    def valid_stages(cls,values):
        if any(v not in ['招标','询价','采购意向','变更','中标候选人','中标结果','终止','资讯'] for v in values):raise ValueError('无效公告阶段')
        return values

def unpack(row):
    return {**json.loads(row['config']),**{k:row[k] for k in ('id','user_id','created_at','updated_at')},'enabled':bool(row['enabled']),'owner_name':row['owner_name'] if 'owner_name' in row.keys() else ''}

def rows(user=None):
    with db.connect() as c:
        if user:
            clause='s.deleted=0'+('' if user['role']=='admin' else ' AND s.user_id=?')
            params=[] if user['role']=='admin' else [user['id']]
        else:
            clause='s.deleted=0 AND s.enabled=1 AND u.enabled=1';params=[]
        result=c.execute('SELECT s.*,u.display_name AS owner_name FROM subscriptions s JOIN users u ON s.user_id=u.id WHERE '+clause+' ORDER BY s.updated_at DESC',params).fetchall()
    return [unpack(r) for r in result]

def get(ident,user):
    result=next((r for r in rows(user) if r['id']==ident),None)
    if not result:raise HTTPException(404,'未找到该订阅')
    return result

def match_decision(item,topic):
    """One predicate source for actual delivery and non-mutating diagnosis previews."""
    title=item['title'].lower()
    text=title+'\n'+item.get('content','').lower()
    hits=[w for w in topic['keywords'] if w.lower() in text]
    conditions={
        'source_ids':not topic['source_ids'] or bool(set(topic['source_ids']).intersection((item.get('observed_source_ids') or [])+[item['source_id']])),
        'provinces':not topic['provinces'] or item.get('province') in topic['provinces'],
        'stages':not topic['stages'] or item.get('stage') in topic['stages'],
        'keywords':bool(hits),
        'exclude_keywords':not any(w.lower() in title for w in topic['exclude_keywords']),
    }
    return {'allowed':all(conditions.values()),'reason':next((key for key,ok in conditions.items() if not ok),None),'hits':hits,'conditions':conditions}

CLOSED_STAGES=('中标候选人','中标结果','终止')
PUSH_WINDOW_DAYS=30
UNDATED_PUSH_DAYS=7
PUSH_SKIPS={'closed':'已截止、结果或终止公告（未在订阅阶段中明确勾选）',
            'stale':f'发布超过{PUSH_WINDOW_DAYS}天的历史公告',
            'body_only_weak':'仅正文命中，且标题不属于核心业务方向或为常规维护/耗材采购'}

def push_decision(item,topic,decision=None,now=None):
    """Delivery gate on top of matching: inbox alerts carry fresh, open, title-relevant notices.

    Matching (preview, recommendations) is unchanged; this only decides which matches are pushed.
    """
    # Callers that already hold a match (recommendations drop the body) pass no decision.
    if decision is not None and not decision['allowed']:return {'push':False,'reason':decision['reason']}
    current=now or clock.now()
    stage=item.get('stage')
    explicit=bool(topic['stages']) and stage in topic['stages']
    closed=stage in CLOSED_STAGES or item.get('temporal_state')=='expired' or bool(item.get('superseded_by'))
    if closed and not (explicit and stage in CLOSED_STAGES):return {'push':False,'reason':'closed'}
    published=item.get('published_at')
    try:
        if published:fresh=datetime.fromisoformat(published)>=current-timedelta(days=PUSH_WINDOW_DAYS)
        else:fresh=bool(item.get('created_at')) and datetime.fromisoformat(item['created_at'])>=current-timedelta(days=UNDATED_PUSH_DAYS)
    except ValueError:fresh=False
    if not fresh:return {'push':False,'reason':'stale'}
    title=item['title'].lower()
    if not closed and not any(w.lower() in title for w in topic['keywords']) and (item.get('category','其他')=='其他' or int(item.get('score') or 0)<50):
        return {'push':False,'reason':'body_only_weak'}
    return {'push':True,'reason':None}

def deliverable(match_result,topics,now=None):
    """Keep only the matched topics that pass the push gate; None when nothing should be pushed."""
    current=now or clock.now();by_id={t['id']:t for t in topics}
    kept=[m for m in match_result['matched_topics'] if m['id'] in by_id and push_decision(match_result,by_id[m['id']],now=current)['push']]
    if not kept:return None
    if len(kept)==len(match_result['matched_topics']):return match_result
    if match_result.get('subscription_id') in {m['id'] for m in kept}:return {**match_result,'matched_topics':kept}
    top=max(kept,key=lambda m:m['score'])
    base=match(match_result,by_id[top['id']]) or {**match_result,'topic_score':top['score'],'subscription_id':top['id'],'subscription_name':top['name']}
    return {**base,'matched_topics':kept}

def match(item,topic):
    decision=match_decision(item,topic)
    if not decision['allowed']:return None
    title=item['title'].lower();hits=decision['hits']
    title_hits=[w for w in hits if w.lower() in title]
    score=55 if title_hits else 35
    reasons=['标题命中：'+'、'.join(title_hits)] if title_hits else ['正文命中：'+'、'.join(hits)+'；需核实是否属于采购范围']
    if item.get('content'):score+=10;reasons.append('取得正文证据')
    if (item.get('published_at') or '')[:10]==clock.now().date().isoformat():score+=5
    closed=item.get('temporal_state')=='expired' or item.get('superseded_by') or item.get('stage') in ('中标结果','中标候选人','终止')
    if closed:score=min(score,45);reasons.append('已截止或有后续状态，仅作历史/合作研究')
    elif item.get('document_state')=='expired':score=min(score,49);reasons.append('文件领取或报名已截止，新进入条件待核实')
    elif item.get('stage')=='采购意向':reasons.append('前期需求信号，适合提前研究')
    else:reasons.append('参与资格及最新截止时间仍需核实')
    return {**item,'topic_score':score,'topic_reasons':reasons,'matched_keywords':hits,'subscription_id':topic.get('id'),'subscription_name':topic['name'],'industry':topic['industry'],'offering':topic['offering']}

def recommendations(user,topics,full=False):
    result=[]
    for item in accounts.items(user,True):
        matches=[m for t in topics if (m:=match(item,t))]
        if not matches:continue
        best=max(matches,key=lambda x:x['topic_score'])
        best['matched_topics']=[{'id':m['subscription_id'],'name':m['subscription_name'],'score':m['topic_score']} for m in matches]
        if not full:best.pop('content',None)
        result.append(best)
    return sorted(result,key=lambda x:(x['topic_score'],x.get('published_at') or ''),reverse=True)

def topic_hash(topic):
    return hashlib.sha256(json.dumps({k:topic[k] for k in ('name','industry','offering','keywords','exclude_keywords','provinces','stages','source_ids')},ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def attach_analysis(item,user,topic):
    with db.connect() as c:
        row=c.execute('SELECT a.payload FROM topic_analyses a JOIN opportunities o ON a.opportunity_id=o.id WHERE a.user_id=? AND a.subscription_id=? AND a.opportunity_id=? AND a.evidence_hash=o.content_hash AND a.topic_hash=?',(user['id'],topic['id'],item['id'],topic_hash(topic))).fetchone()
    item['ai_analysis']=json.loads(row[0]) if row else None
    item['business_context']={k:topic[k] for k in ('name','industry','offering','keywords')}
    matched=match(item,topic)
    item['topic_match']={'score':matched['topic_score'],'reasons':matched['topic_reasons']} if matched else {'score':0,'reasons':['当前公告已不匹配本专题，请重新查看推荐列表']}
    return item

def save_analysis(item,user,topic):
    from . import ai
    with db.connect() as c:evidence_hash=c.execute('SELECT content_hash FROM opportunities WHERE id=?',(item['id'],)).fetchone()[0]
    result=ai.explain(item)
    result['notice']='按订阅生成的个人解读；事实以原公告为准'
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current=c.execute('SELECT content_hash FROM opportunities WHERE id=?',(item['id'],)).fetchone()[0]
        current_topic=c.execute('SELECT * FROM subscriptions WHERE id=? AND deleted=0',(topic['id'],)).fetchone()
        if current!=evidence_hash or not current_topic or topic_hash(unpack(current_topic))!=topic_hash(topic):raise RuntimeError('公告或专题已更新，请重新分析')
        c.execute('INSERT INTO topic_analyses VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,subscription_id,opportunity_id) DO UPDATE SET evidence_hash=excluded.evidence_hash,topic_hash=excluded.topic_hash,payload=excluded.payload,created_at=excluded.created_at',(user['id'],topic['id'],item['id'],evidence_hash,topic_hash(topic),db.dumps(result),db.now()))
    item['ai_analysis']=result
    return item
