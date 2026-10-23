"""Personal inbox, automatic subscription delivery and durable collection progress."""
import json
import os
import threading
from datetime import datetime,timedelta
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Query,Request
from pydantic import BaseModel,Field
from . import db,accounts,subscriptions,clock,querying
router=APIRouter(prefix='/api')
DISPATCH_LOCK=threading.Lock()

def initialize():
 with db.connect() as c:
  c.executescript('''
  CREATE TABLE IF NOT EXISTS automations(user_id INTEGER PRIMARY KEY REFERENCES users(id),enabled INTEGER NOT NULL DEFAULT 1,interval_minutes INTEGER NOT NULL DEFAULT 60,last_run TEXT,last_count INTEGER NOT NULL DEFAULT 0);
  CREATE TABLE IF NOT EXISTS pushes(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),payload TEXT NOT NULL,pushed_at TEXT NOT NULL,read_at TEXT,UNIQUE(user_id,opportunity_id));
  CREATE INDEX IF NOT EXISTS pushes_owner ON pushes(user_id,pushed_at DESC);
  CREATE TABLE IF NOT EXISTS collection_progress(run_id INTEGER PRIMARY KEY,payload TEXT NOT NULL);
  ''')
  c.execute("UPDATE collection_progress SET payload=? WHERE json_extract(payload,'$.phase') IN ('智能解读','生成站内推送','采集网站')",(db.dumps({'phase':'中断','completed':0,'total':0,'current':''}),))

def request_subscription_check(connection,user_id):
 connection.execute('''INSERT INTO subscription_checks(user_id,requested_revision,checked_revision) VALUES(?,1,0)
   ON CONFLICT(user_id) DO UPDATE SET requested_revision=requested_revision+1''',(user_id,))

def check_revision(connection,user_id):
 row=connection.execute('SELECT requested_revision,checked_revision FROM subscription_checks WHERE user_id=?',(user_id,)).fetchone()
 return (row['requested_revision'],row['checked_revision']) if row else (0,0)

def own_topics(user):return [x for x in subscriptions.rows(user) if x['user_id']==user['id']]

def associated_topics(payload):
 """All historical topic associations, including durable pre-association rows."""
 topics={}
 for topic in (payload.get('matched_topics') or [])+(payload.get('associated_topics') or []):
  topics.setdefault(topic['id'],topic)
 return list(topics.values())

def automation(user):
 with db.connect() as c:
  if clock.writable():c.execute('INSERT OR IGNORE INTO automations(user_id) VALUES(?)',(user['id'],))
  row=c.execute('SELECT * FROM automations WHERE user_id=?',(user['id'],)).fetchone()
  value=dict(row) if row else {'user_id':user['id'],'enabled':1,'interval_minutes':60,'last_run':None,'last_count':0}
  requested,checked=check_revision(c,user['id'])
 value['enabled']=bool(value['enabled'])
 value['scheduler_enabled']=os.getenv('SCHEDULER_ENABLED','true').lower()=='true'
 value['pending_check']=requested>checked
 value['next_run']=db.now() if value['enabled'] and value['pending_check'] else (datetime.fromisoformat(value['last_run'])+timedelta(minutes=value['interval_minutes'])).isoformat() if value['enabled'] and value['last_run'] else None
 return value

class Schedule(BaseModel):
 enabled:bool=True
 interval_minutes:int=Field(default=60,ge=15,le=10080)

@router.get('/automation')
def get_automation(user=Depends(accounts.require_user)):return automation(user)

@router.put('/automation')
def save_automation(body:Schedule,user=Depends(accounts.require_user)):
 with db.connect() as c:
  c.execute('INSERT INTO automations(user_id,enabled,interval_minutes) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled,interval_minutes=excluded.interval_minutes',(user['id'],body.enabled,body.interval_minutes))
  if body.enabled:request_subscription_check(c,user['id'])
 return automation(user)

@router.get('/my-subscriptions')
def my_topics(user=Depends(accounts.require_user)):
 items=own_topics(user)
 with db.connect() as c:pushes=[json.loads(r[0]) for r in c.execute('SELECT payload FROM pushes WHERE user_id=?',(user['id'],))]
 matches=subscriptions.recommendations(user,[t for t in items if t['enabled']]) if any(t['enabled'] for t in items) else []
 for t in items:
  t['push_count']=sum(any(m['id']==t['id'] for m in associated_topics(p)) for p in pushes)
  t['match_count']=sum(any(m['id']==t['id'] for m in p['matched_topics']) for p in matches)
 return {'items':items,'total':len(items),'subscribers':1 if items else 0}

@router.get('/team-dashboard')
def team_dashboard(q:str=Query('',max_length=150),owner_id:int|None=Query(None,ge=1),status:Literal['','enabled','disabled','no_matches','source_unavailable','automation_paused']='',page:int=Query(1,ge=1,le=10000),page_size:int=Query(10,ge=5,le=20),user=Depends(accounts.require_admin)):
    """Read-only team subscription demand and delivery totals, without personal records."""
    if page_size not in (5,10,20):raise HTTPException(422,'每页仅支持5、10或20条')
    from .collectors import source_rows
    with db.connect() as c:
        users=[dict(r) for r in c.execute('SELECT id,username,display_name,enabled FROM users ORDER BY id')]
        rows=c.execute('''SELECT s.id,s.user_id,s.config,s.enabled,s.created_at,s.updated_at,
            u.display_name AS owner_name,u.username AS owner_username,u.enabled AS owner_enabled
            FROM subscriptions s JOIN users u ON u.id=s.user_id WHERE s.deleted=0
            ORDER BY s.updated_at DESC,s.id DESC''').fetchall()
        automations={r['user_id']:dict(r) for r in c.execute('SELECT user_id,enabled,interval_minutes,last_run,last_count FROM automations')}
        public_items=db.unpack_many(c,c.execute('SELECT '+db.LIGHT_COLUMNS+' FROM opportunities').fetchall())
        pushes=c.execute('SELECT user_id,opportunity_id,payload,pushed_at FROM pushes').fetchall()
    for owner in users:owner['enabled']=bool(owner['enabled'])
    sources=source_rows()
    collectable={s['id'] for s in sources if s['enabled'] and s['adapter_available']}
    fields=('name','industry','offering','keywords','exclude_keywords','provinces','stages','source_ids')
    items=[]
    for row in rows:
        topic=subscriptions.unpack(row)
        active=bool(row['enabled'] and row['owner_enabled'])
        plan=automations.get(row['user_id'],{'enabled':True,'interval_minutes':60,'last_run':None,'last_count':0})
        automation={key:plan[key] for key in ('enabled','interval_minutes','last_run','last_count')}
        automation['enabled']=bool(automation['enabled'])
        selected=set(topic['source_ids'])
        source_count=len(collectable.intersection(selected) if selected else collectable)
        match_count=sum(subscriptions.match(item,topic) is not None for item in public_items) if active else 0
        recall='paused' if not active else 'source_unavailable' if not source_count else 'matched' if match_count else 'no_matches'
        items.append({**{key:topic[key] for key in fields},
            **{key:row[key] for key in ('id','user_id','created_at','updated_at','owner_name','owner_username')},
            'enabled':bool(row['enabled']),'owner_enabled':bool(row['owner_enabled']),'automation':automation,
            'match_count':match_count,'push_count':0,'pushes_7d':0,'last_push':None,
            'collection_source_count':source_count,'recall_status':recall})
    topic_map={item['id']:item for item in items}
    delivered={item['id']:set() for item in items}
    recent_delivered={item['id']:set() for item in items}
    last_times={}
    now=datetime.fromisoformat(db.now())
    cutoff=now-timedelta(days=7)
    recent=set()
    for push in pushes:
        key=(push['user_id'],push['opportunity_id'])
        pushed=datetime.fromisoformat(push['pushed_at'])
        if pushed.tzinfo is None:pushed=pushed.replace(tzinfo=db.TZ)
        within_week=cutoff<=pushed<=now
        if within_week:recent.add(key)
        for association in associated_topics(json.loads(push['payload'])):
            item=topic_map.get(association['id'])
            if item is None or item['user_id']!=push['user_id']:continue
            ident=item['id'];delivered[ident].add(key)
            if within_week:recent_delivered[ident].add(key)
            if ident not in last_times or pushed>last_times[ident]:
                last_times[ident]=pushed;item['last_push']=push['pushed_at']
    for item in items:
        item['push_count']=len(delivered[item['id']])
        item['pushes_7d']=len(recent_delivered[item['id']])
    active_items=[item for item in items if item['enabled'] and item['owner_enabled']]
    summary={'total_users':len(users),'subscribed_users':len({item['user_id'] for item in items}),
        'total_topics':len(items),'enabled_topics':len(active_items),'pushes_7d':len(recent),
        'paused_automation_users':len({item['user_id'] for item in active_items if not item['automation']['enabled']}),
        'no_match_topics':sum(item['recall_status']=='no_matches' for item in active_items),
        'scheduler_enabled':os.getenv('SCHEDULER_ENABLED','true').lower()=='true'}
    query=q.lower().strip()
    if query:items=[item for item in items if query in ' '.join([item['name'],item['owner_name'],item['owner_username'],*item['keywords']]).lower()]
    if owner_id is not None:items=[item for item in items if item['user_id']==owner_id]
    if status=='enabled':items=[item for item in items if item['enabled'] and item['owner_enabled']]
    elif status=='disabled':items=[item for item in items if not item['enabled'] or not item['owner_enabled']]
    elif status=='automation_paused':items=[item for item in items if item['enabled'] and item['owner_enabled'] and not item['automation']['enabled']]
    elif status:items=[item for item in items if item['recall_status']==status]
    return {'summary':summary,'users':users,'sources':[{'id':s['id'],'name':s['name'],'enabled':bool(s['enabled'])} for s in sources],
        'items':items[(page-1)*page_size:page*page_size],'total':len(items),'page':page,'page_size':page_size}

def dispatch_due(force=False):
 if not clock.writable():return
 if not DISPATCH_LOCK.acquire(blocking=False):return
 try:
  with db.connect() as c:users=[dict(r) for r in c.execute('SELECT id,username,display_name,role FROM users WHERE enabled=1')]
  for user in users:
   task=automation(user)
   if not task['enabled']:continue
   with db.connect() as c:requested,checked=check_revision(c,user['id'])
   if not force and requested==checked and task['next_run'] and datetime.fromisoformat(task['next_run'])>clock.now():continue
   topics=[t for t in own_topics(user) if t['enabled']]
   current=clock.now()
   matches=[d for m in (subscriptions.recommendations(user,topics) if topics else []) if (d:=subscriptions.deliverable(m,topics,current))]
   inserted=0
   with db.connect() as c:
    c.execute('BEGIN IMMEDIATE')
    active=c.execute('SELECT enabled FROM users WHERE id=?',(user['id'],)).fetchone()
    config=c.execute('SELECT enabled,last_run,interval_minutes FROM automations WHERE user_id=?',(user['id'],)).fetchone()
    if not active or not active[0] or not config[0]:continue
    current_requested,current_checked=check_revision(c,user['id'])
    if current_requested!=requested:continue
    if not force and current_requested==current_checked and config['last_run'] and datetime.fromisoformat(config['last_run'])+timedelta(minutes=config['interval_minutes'])>clock.now():continue
    pushed_at=db.now()
    for m in matches:
     snapshot={k:m.get(k) for k in ('id','title','source_name','published_at','published_at_precision','stage','topic_score','topic_reasons','matched_keywords','matched_topics','subscription_id','subscription_name')}
     snapshot['associated_topics']=associated_topics(snapshot)
     created=c.execute('INSERT OR IGNORE INTO pushes(user_id,opportunity_id,payload,pushed_at) VALUES(?,?,?,?)',(user['id'],m['id'],db.dumps(snapshot),pushed_at)).rowcount
     inserted+=created
     if not created:
      row=c.execute('SELECT id,payload FROM pushes WHERE user_id=? AND opportunity_id=?',(user['id'],m['id'])).fetchone()
      original=json.loads(row['payload'])
      topics=associated_topics({**original,'associated_topics':associated_topics(original)+snapshot['associated_topics']})
      if original.get('associated_topics')!=topics:
       original['associated_topics']=topics
       c.execute('UPDATE pushes SET payload=? WHERE id=?',(db.dumps(original),row['id']))
    c.execute('UPDATE automations SET last_run=?,last_count=? WHERE user_id=?',(db.now(),inserted,user['id']))
    c.execute('UPDATE subscription_checks SET checked_revision=? WHERE user_id=?',(requested,user['id']))
 finally:DISPATCH_LOCK.release()

@router.get('/pushes')
def inbox(request:Request,f:querying.PushParams=Depends(),user=Depends(accounts.require_user)):
 if f.page_size not in (5,10,20):raise HTTPException(422,'每页仅支持5、10或20条')
 querying.validate_params('pushes',request.query_params.keys())
 spec=querying.normalize('pushes',f.model_dump(),clock.now())
 if spec.subscription_id is not None and not any(t['id']==spec.subscription_id for t in own_topics(user)):raise HTTPException(404,'未找到该订阅')
 db.refresh_expired()
 clause='p.user_id=?';params=[user['id']]
 if spec.read:clause+=' AND p.read_at IS '+('NULL' if spec.read=='unread' else 'NOT NULL')
 if spec.subscription_id is not None:
  clause+=" AND EXISTS (SELECT 1 FROM json_each(COALESCE(json_extract(p.payload,'$.associated_topics'),json_extract(p.payload,'$.matched_topics'),'[]')) AS topic WHERE json_extract(topic.value,'$.id')=?)"
  params.append(spec.subscription_id)
 with db.connect() as c:
  unread=c.execute('SELECT COUNT(*) FROM pushes WHERE user_id=? AND read_at IS NULL',(user['id'],)).fetchone()[0]
  rows=c.execute('SELECT p.id,p.opportunity_id,p.payload,p.pushed_at,p.read_at,o.status,o.note,o.created_at,o.updated_at,o.last_seen_at,o.revision FROM pushes p JOIN opportunities o ON o.id=p.opportunity_id WHERE '+clause,params).fetchall()
  publics={x['id']:x for x in db.unpack_many(c,[{**{k:r[k] for k in ('status','note','created_at','updated_at','last_seen_at','revision')},'id':r['opportunity_id']} for r in rows])}
  from . import notifications
  assignments=notifications.unviewed_assignments(c,user['id'])
  ids=[r['opportunity_id'] for r in rows]
  followups={r['opportunity_id']:{'status':r['status'],'updated_at':r['updated_at'],'note':''} for r in c.execute('SELECT opportunity_id,status,updated_at FROM followups WHERE user_id=? AND opportunity_id IN ('+','.join('?' for _ in ids)+')',[user['id']]+ids)} if ids else {}
 from . import team_followups,project_analysis
 teams=team_followups.public_rows(ids);items=[]
 for row in rows:
  snapshot=json.loads(row['payload']);snapshot.pop('note',None)
  public=publics[row['opportunity_id']]
  for key in ('note','status'):public.pop(key,None)
  item=accounts.overlay({**snapshot,**public,'id':row['opportunity_id']},user,followups,teams,assignments)
  item.update(associated_topics=associated_topics(snapshot),push_id=row['id'],pushed_at=row['pushed_at'],read_at=row['read_at'],
              delivery_published_at=snapshot.get('published_at'),delivery_published_at_precision=snapshot.get('published_at_precision'))
  if not querying.matches_notice(item,spec,clock.now()):continue
  item.pop('note',None);item.pop('content',None);items.append(item)
 def order(item):
  score=item.get('topic_score') or 0;published=item.get('published_at') or '';pushed=item['pushed_at']
  return (published,score,pushed,item['push_id']) if f.sort=='published_at' else (score,published,pushed,item['push_id']) if f.sort=='score' else (pushed,score,published,item['push_id'])
 items.sort(key=order,reverse=True);total=len(items)
 page=items[(f.page-1)*f.page_size:f.page*f.page_size]
 return {'items':project_analysis.decorate(page,user),'total':total,'unread':unread,'page':f.page,'page_size':f.page_size}

@router.patch('/pushes/read-all')
def mark_all_read(subscription_id:int|None=Query(None,ge=1),user=Depends(accounts.require_user)):
 clause='user_id=? AND read_at IS NULL';params=[db.now(),user['id']]
 if subscription_id is not None:
  if not any(t['id']==subscription_id for t in own_topics(user)):raise HTTPException(404,'未找到该订阅')
  clause+=" AND EXISTS (SELECT 1 FROM json_each(COALESCE(json_extract(payload,'$.associated_topics'),json_extract(payload,'$.matched_topics'),'[]')) AS topic WHERE json_extract(topic.value,'$.id')=?)"
  params.append(subscription_id)
 with db.connect() as c:changed=c.execute('UPDATE pushes SET read_at=? WHERE '+clause,params).rowcount
 return {'status':'已读','updated':changed}

@router.patch('/pushes/{ident}/read')
def mark_read(ident:int,user=Depends(accounts.require_user)):
 with db.connect() as c:
  if not c.execute('UPDATE pushes SET read_at=COALESCE(read_at,?) WHERE id=? AND user_id=?',(db.now(),ident,user['id'])).rowcount:raise HTTPException(404,'推送记录不存在')
 return {'status':'已读'}

@router.get('/collection-schedule')
def collection_schedule(user=Depends(accounts.require_admin)):
 settings=db.settings()
 return {'enabled':settings.get('collection_enabled',True),'interval_minutes':settings['interval_minutes'],'scheduler_enabled':os.getenv('SCHEDULER_ENABLED','true').lower()=='true'}

@router.put('/collection-schedule')
def save_collection_schedule(body:Schedule,user=Depends(accounts.require_admin)):
 patch={'collection_enabled':body.enabled,'interval_minutes':body.interval_minutes}
 with db.connect() as c:
  c.execute('UPDATE settings SET value=json_patch(value,?) WHERE id=1',(db.dumps(patch),))
 return body.model_dump()

def progress(run_id,phase,completed=0,total=0,current=''):
 with db.connect() as c:c.execute('INSERT OR REPLACE INTO collection_progress VALUES(?,?)',(run_id,db.dumps({'phase':phase,'completed':completed,'total':total,'current':current})))

@router.get('/collection-progress')
def collection_progress(user=Depends(accounts.require_admin)):
 with db.connect() as c:
  row=c.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 1').fetchone()
  if not row:return {'active':False,'run':None,'phase':'尚未采集','completed':0,'total':0,'current':''}
  value=dict(row);value['details']=json.loads(value['details']);p=c.execute('SELECT payload FROM collection_progress WHERE run_id=?',(row['id'],)).fetchone()
  p=json.loads(p[0]) if p else {'phase':value['status'],'completed':len(value['details']),'total':len(value['details']),'current':''}
  active=value['status']=='运行中' or p['phase'] in ('智能解读','生成站内推送')
  if value['status']=='中断':active=False;p['phase']='中断';p['current']=''
  return {**p,'active':active,'run':value}
