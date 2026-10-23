from __future__ import annotations
import base64
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime,timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from fastapi import FastAPI,Depends,HTTPException,Query,Request,Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field,field_validator
from . import db,ai,accounts,subscriptions,workbench,team_followups,notifications,clock,ai_jobs,ai_job_requests,project_analysis,querying,subscription_diagnosis
from .accounts import require_user,require_admin
from .management import router as management_router
from .monitoring import router as monitoring_router
from .assist import router as assist_router
from .companies import router as companies_router
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .analysis import analyze
from .collectors import register_sources,source_rows,run_collection

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
logging.getLogger('httpx').setLevel(logging.WARNING)
LOG=logging.getLogger(__name__)
STATIC=Path(__file__).parent/'static'
RUN_LOCK=threading.Lock()
STOP=threading.Event()
FAILURES={}
FAIL_LOCK=threading.Lock()

def session_secret() -> bytes:
    value=os.getenv('SESSION_SECRET')
    if not value or len(value)<32:raise RuntimeError('SESSION_SECRET must be set (32+ characters)')
    return value.encode()

def run_worker(ident: int) -> None:
    try:
        run_collection(ident)
        workbench.progress(ident,"智能解读")
        ai.analyze_top()
        project_analysis.backfill()
        workbench.progress(ident,"生成站内推送")
        workbench.dispatch_due()
    except Exception:
        LOG.exception("collection processing failed")
        with db.connect() as c:
            c.execute("UPDATE runs SET status=CASE WHEN status='运行中' THEN '失败' ELSE '部分完成' END,finished_at=?,error='采集或后续处理未完成，请查看记录后重试' WHERE id=?",(db.now(),ident))
    finally:
        try:workbench.progress(ident,"结束")
        finally:RUN_LOCK.release()

def start_collection() -> tuple[str,int | None]:
    clock.ensure_writable()
    if not RUN_LOCK.acquire(blocking=False):
        with db.connect() as c:
            row=c.execute('SELECT id FROM runs ORDER BY id DESC LIMIT 1').fetchone()
        return '运行中',row['id'] if row else None
    try:
        with db.connect() as c:
            ident=c.execute("INSERT INTO runs(started_at,status) VALUES(?,'运行中')",(db.now(),)).lastrowid
        threading.Thread(target=run_worker,args=(ident,),daemon=True,name='collector').start()
        return '已启动',ident
    except Exception:
        RUN_LOCK.release();raise

def scheduler() -> None:
    while not STOP.wait(15):
        try:
            if not clock.writable():continue
            with db.connect() as c:row=c.execute('SELECT started_at FROM runs ORDER BY id DESC LIMIT 1').fetchone()
            due=not row or datetime.fromisoformat(row['started_at'])+timedelta(minutes=db.settings()['interval_minutes'])<=clock.now()
            if due and db.settings().get("collection_enabled",True):start_collection()
            workbench.dispatch_due()
            project_analysis.backfill()
        except Exception:LOG.exception('scheduler error')

@asynccontextmanager
async def lifespan(app: FastAPI):
    if len(os.getenv('RADAR_PASSWORD',''))<12:raise RuntimeError('RADAR_PASSWORD must be set (12+ characters)')
    session_secret();clock.initialize();db.initialize();register_sources();db.refresh_expired();ai_jobs.initialize();ai_job_requests.register();project_analysis.initialize();project_analysis.register();subscription_diagnosis.register();ai_jobs.start();project_analysis.backfill();STOP.clear()
    if os.getenv('SCHEDULER_ENABLED','true').lower()=='true':
        threading.Thread(target=scheduler,daemon=True,name='scheduler').start()
    yield
    STOP.set()
    ai_jobs.stop()
    clock.shutdown()

app=FastAPI(title='产业获客情报台',version='2.2.1',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
app.include_router(monitoring_router)
app.include_router(assist_router)
app.include_router(companies_router)
app.include_router(ai_job_requests.router)
app.include_router(project_analysis.router)
app.include_router(subscription_diagnosis.router)
app.include_router(workbench.router)
app.include_router(team_followups.router)
app.include_router(notifications.router)

@app.middleware('http')
async def security(request: Request,call_next):
    if request.method in ('POST','PUT','PATCH','DELETE'):
        try:clock.ensure_writable()
        except clock.ClockUnavailable as error:
            return JSONResponse({'detail':str(error)},status_code=503)
        origin=request.headers.get('origin')
        if origin and urlsplit(origin).netloc!=request.url.netloc:
            return Response(content='{"detail":"跨站请求被拒绝"}',status_code=403,media_type='application/json')
        if request.headers.get('sec-fetch-site')=='cross-site':
            return Response(status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith('/api/'):response.headers['Cache-Control']='no-store'
    elif request.url.path.startswith('/static/') and 'v=' in request.url.query and response.status_code==200:response.headers['Cache-Control']='public, max-age=31536000, immutable'
    return response

class Login(BaseModel):
    username: str=Field(min_length=1,max_length=40)
    password: str=Field(min_length=1,max_length=256)

@app.post('/api/login')
def login(body: Login,request: Request,response: Response):
    client=request.client.host if request.client else 'unknown'
    current=time.monotonic()
    with FAIL_LOCK:
        attempts=[x for x in FAILURES.get(client,[]) if x>current-300]
        if len(attempts)>=10:raise HTTPException(429,'尝试过于频繁，请5分钟后再试')
        with db.connect() as c:row=c.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE',(body.username.strip(),)).fetchone()
        valid=accounts.verify(body.password,row['password_hash'] if row else accounts.DUMMY)
        if not valid or not row or not row['enabled']:
            FAILURES[client]=attempts+[current]
            raise HTTPException(401,'账号或密码不正确，或账号已停用')
        FAILURES.pop(client,None)
    response.set_cookie('radar_session',accounts.create_session(row['id']),httponly=True,samesite='strict',secure=os.getenv('COOKIE_SECURE','false')=='true',max_age=604800)
    return {'status':'已登录','user':accounts.public(row)}

@app.post('/api/logout')
def logout(response: Response,request:Request):
    accounts.revoke(request.cookies.get('radar_session',''))
    response.delete_cookie('radar_session');return {'status':'已退出'}

@app.get('/api/health')
def health():
    try:
        with db.connect() as c:c.execute('SELECT 1 FROM settings LIMIT 1').fetchone()
        return {'status':'ok','version':'2.2.1'}
    except Exception:raise HTTPException(503,'数据服务暂不可用')

@app.get('/api/me',dependencies=[Depends(require_user)])
def me(user=Depends(require_user)):return {'authenticated':True,**user}

@app.get('/api/clock')
def business_clock(request: Request):
    value=clock.status()
    try:user=require_user(request)
    except HTTPException:user=None
    if user and user['role']=='admin':return value
    return {key:value[key] for key in ('state','writable','current_time','timezone')}

class Filters(querying.NoticeParams):
    q:str=Field(default='',max_length=150)
    category:str=Field(default='',max_length=30)
    province:str=Field(default='',max_length=30)
    stage:str=Field(default='',max_length=30)
    status:str=Field(default='',max_length=30)
    min_score:int=Field(default=0,ge=0,le=100)
    actionable:bool=False
    page:int=Field(default=1,ge=1,le=10000)
    page_size:int=Field(default=20,ge=1,le=100)
    sort:Literal['score','published_at','deadline']='score'

def criteria(f: Filters) -> tuple[str,list]:
    db.refresh_expired()
    clauses=['score>=?'];params=[f.min_score]
    if f.q:
        clauses.append('(title LIKE ? OR json_extract(payload,\'$.buyer\') LIKE ?)');params += ['%'+f.q+'%']*2
    for field in ('category','province','stage','status'):
        if getattr(f,field):clauses.append(field+'=?');params.append(getattr(f,field))
    if f.actionable:
        clauses += ['actionable=1','deadline>?',"status NOT IN ('已排除','已归档')"]
        params.append(db.now())
    return ' AND '.join(clauses),params

def filtered_items(f,user):
    spec=querying.normalize('opportunities',f.model_dump(),clock.now())
    items=accounts.items(user,True)
    result=[]
    for x in items:
        if not querying.matches_notice(x,spec,clock.now()):continue
        x.pop('content',None)
        result.append(x)
    if f.sort=='score':result.sort(key=lambda x:(x['score'],x.get('published_at') or ''),reverse=True)
    elif f.sort=='published_at':result.sort(key=lambda x:(x.get('published_at') or '',x['score']),reverse=True)
    else:result.sort(key=lambda x:x.get('deadline') or '9999')
    return result

@app.get('/api/opportunities')
def opportunities(request:Request,f:Filters=Depends(),user=Depends(require_user)):
    querying.validate_params('opportunities',request.query_params.keys())
    items=filtered_items(f,user)
    return {'items':project_analysis.decorate(items[(f.page-1)*f.page_size:f.page*f.page_size],user),'total':len(items),'page':f.page,'page_size':f.page_size}

@app.get('/api/opportunities/{ident}')
def detail(ident:int,user=Depends(require_user),subscription_id:int|None=None):
    db.refresh_expired()
    with db.connect() as c:row=c.execute('SELECT * FROM opportunities WHERE id=?',(ident,)).fetchone()
    if not row:raise HTTPException(404,'未找到该公告')
    item=accounts.overlay(db.unpack(row),user)
    if subscription_id:item=subscriptions.attach_analysis(item,user,subscriptions.get(subscription_id,user))
    return project_analysis.attach(item,user)

class Followup(BaseModel):
    status:Literal['待研判','跟进中','已联系','已排除','已归档']
    note:str=Field(default='',max_length=5000)

@app.patch('/api/opportunities/{ident}',dependencies=[Depends(require_user)])
def followup(ident:int,body:Followup,user=Depends(require_user)):
    team_followups.save_personal(ident,user,body.status,body.note)
    return detail(ident,user)

@app.get('/api/followups')
def saved_followups(request:Request,f:querying.FollowupParams=Depends(),user=Depends(require_user)):
    querying.validate_params('followups',request.query_params.keys())
    spec=querying.normalize('followups',f.model_dump(),clock.now())
    if f.page_size not in (5,10,20):raise HTTPException(422,'每页仅支持5、10或20条')
    result=accounts.saved_followups(user,q=spec.q,status=spec.status,page=f.page,page_size=f.page_size,sort=f.sort,query_spec=spec)
    project_analysis.decorate(result['items'],user)
    return {**result,'page':f.page,'page_size':f.page_size}

@app.post('/api/opportunities/{ident}/analyze',dependencies=[Depends(require_user)])
def model_analyze(ident:int,subscription_id:int|None=None,user=Depends(require_user)):
    if user['role']!='admin' and not subscription_id:raise HTTPException(403,'公共 AI 解读由管理员生成；可在自己的订阅专题中生成解读')
    job=ai_job_requests.submit_analysis(ident,user,subscription_id)
    ai_job_requests.wait(job['id'],user)
    return detail(ident,user,subscription_id)

@app.get('/api/companies',dependencies=[Depends(require_user)])
def companies(request:Request,q:str=Query('',max_length=150),province:str=Query('',max_length=30),page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    querying.validate_params('companies',request.query_params.keys())
    spec=querying.normalize('companies',{'q':q,'province':province},clock.now());q=spec.q;province=spec.province
    where='1=1';params=[]
    if q:where+=' AND (name LIKE ? OR group_name LIKE ? OR city LIKE ?)';params+=['%'+q+'%']*3
    if province:where+=' AND province=?';params.append(province)
    with db.connect() as c:
        total=c.execute('SELECT COUNT(*) FROM companies WHERE '+where,params).fetchone()[0]
        rows=c.execute('SELECT * FROM companies WHERE '+where+' ORDER BY id LIMIT ? OFFSET ?',params+[page_size,(page-1)*page_size]).fetchall()
        provinces=[x[0] for x in c.execute('SELECT DISTINCT province FROM companies ORDER BY province')]
    return {'items':[dict(x) for x in rows],'total':total,'page':page,'page_size':page_size,'provinces':provinces}

@app.get('/api/sources',dependencies=[Depends(require_admin)])
def sources():return {'items':source_rows()}

@app.get('/api/source-catalog',dependencies=[Depends(require_user)])
def source_catalog():
    # Keep availability aligned with subscriptions.Topic.valid_sources.
    items=[{'id':s['id'],'name':s['name']} for s in source_rows() if s['enabled'] or s['adapter_available']]
    return {'items':items}

@app.get('/api/public-sources',dependencies=[Depends(require_user)])
def public_sources():
    from .collectors import SOURCES
    from .source_health import public_health
    with db.connect() as c:
        historical={r[0] for r in c.execute("""
            SELECT source_id FROM opportunities
            UNION
            SELECT observed.value FROM opportunities
            CROSS JOIN json_each(CASE
                WHEN json_type(opportunities.payload,'$.observed_source_ids')='array'
                THEN json_extract(opportunities.payload,'$.observed_source_ids') ELSE '[]'
            END) AS observed
        """)}
    catalog = {source['id']: source for source in SOURCES}
    items = []
    for source in source_rows():
        enabled = bool(source['enabled'])
        authorization = source.get('requires_authorization', catalog.get(source['id'], {}).get('requires_authorization', False))
        health = public_health(source, authorization)
        coverage=catalog.get(source['id'],{}).get('public_note','') if not source.get('custom') else ''
        if coverage:health['availability_note']+=' '+coverage
        # A configured data-channel URL can contain credentials in its path/query.
        # Publish verified built-in entry points, otherwise only the site homepage.
        public_url = source['url']
        if source.get('custom') or public_url != catalog.get(source['id'], {}).get('url'):
            try:
                parsed = urlsplit(public_url)
                host = parsed.hostname or ''
                if ':' in host:host = '[' + host + ']'
                if parsed.port:host += ':' + str(parsed.port)
                public_url = urlunsplit((parsed.scheme, host, '/', '', '')) if host and parsed.scheme in ('http','https') else ''
            except ValueError:
                public_url = ''
        items.append({
            'id': source['id'], 'name': source['name'], 'url': public_url,
            'kind': source.get('kind', ''), 'enabled': enabled,
            'historical': source['id'] in historical, **health,
        })
    return {'items':items}

def run_rows() -> list[dict]:
    with db.connect() as c:rows=c.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 30').fetchall()
    return [{**dict(x),'details':json.loads(x['details'])} for x in rows]

@app.get('/api/runs',dependencies=[Depends(require_admin)])
def runs():return {'items':run_rows()}

@app.post('/api/collect',dependencies=[Depends(require_admin)])
def collect():
    status,ident=start_collection();db.audit('启动采集',str(ident));return {'status':status,'run_id':ident}

class Settings(BaseModel):
    focus_keywords:list[str]=Field(max_length=30)
    exclude_keywords:list[str]=Field(max_length=50)
    preferred_provinces:list[str]=Field(max_length=31)
    min_score:int=Field(55,ge=0,le=100)
    # Validate legacy clients without letting strategy saves change the collection plan.
    interval_minutes:int|None=Field(None,ge=15,le=10080)

    @field_validator('focus_keywords','exclude_keywords','preferred_provinces')
    @classmethod
    def valid_list(cls,value:list[str]) -> list[str]:
        if any(not x.strip() or len(x)>40 for x in value):raise ValueError('关键词必须为1–40个字符')
        return list(dict.fromkeys(x.strip() for x in value))

@app.get('/api/settings')
def get_settings(user=Depends(require_user)):
    settings=db.settings()
    return settings if user['role']=='admin' else {'min_score':settings['min_score']}

@app.put('/api/settings',dependencies=[Depends(require_admin)])
def put_settings(body:Settings):
    if not RUN_LOCK.acquire(blocking=False):raise HTTPException(409,'采集或分析正在运行，请完成后保存策略')
    try:
        patch=body.model_dump(exclude={'interval_minutes'})
        with db.connect() as c:c.execute('UPDATE settings SET value=json_patch(value,?) WHERE id=1',(db.dumps(patch),))
        value=db.settings()
        companies=db.all_companies()
        with db.connect() as c:rows=c.execute('SELECT * FROM opportunities').fetchall()
        for row in rows:
            raw=json.loads(row['payload']);new=analyze(raw,companies,value)
            db.upsert(new,expected_hash=row['content_hash'])
        db.reconcile_lifecycle()
        db.audit('策略更新','settings')
    finally:RUN_LOCK.release()
    return value

@app.get('/api/dashboard',dependencies=[Depends(require_user)])
def dashboard(user=Depends(require_user)):
    db.refresh_expired()
    threshold=db.settings()['min_score']
    with db.connect() as c:
        total=c.execute('SELECT COUNT(*) FROM opportunities').fetchone()[0]
        actionable=c.execute("SELECT COUNT(*) FROM opportunities WHERE actionable=1 AND deadline>? AND status NOT IN ('已排除','已归档')",(db.now(),)).fetchone()[0]
        high=c.execute("SELECT COUNT(*) FROM opportunities WHERE score>=? AND status NOT IN ('已排除','已归档')",(threshold,)).fetchone()[0]
        count=c.execute('SELECT COUNT(*) FROM companies').fetchone()[0]
        regions=[{'name':x[0] or '待核实','count':x[1]} for x in c.execute("SELECT province,COUNT(*) FROM opportunities GROUP BY province ORDER BY COUNT(*) DESC LIMIT 8")]
        cats=[{'name':x[0],'count':x[1]} for x in c.execute('SELECT category,COUNT(*) FROM opportunities GROUP BY category ORDER BY COUNT(*) DESC')]
        daily=[{'date':x[0],'count':x[1]} for x in c.execute('SELECT substr(created_at,1,10),COUNT(*) FROM opportunities GROUP BY substr(created_at,1,10) ORDER BY 1 DESC LIMIT 14')]
        last_update=c.execute('SELECT MAX(last_seen_at) FROM opportunities').fetchone()[0]
    personal=accounts.items(user)
    actionable=sum(x['actionable'] and x['status'] not in ('已排除','已归档') for x in personal)
    high=sum(x['score']>=threshold and x['status'] not in ('已排除','已归档') for x in personal)
    stats={'total':total,'actionable':actionable,'high_priority':high,'companies':count,'last_update':last_update}
    result={'stats':stats,'top_regions':regions,'category_counts':cats,'daily':daily}
    if user['role']!='admin':return result
    histories=run_rows();latest=histories[0] if histories else None
    src=[x for x in source_rows() if x['enabled']]
    next_run=(datetime.fromisoformat(latest['started_at'])+timedelta(minutes=db.settings()['interval_minutes'])).isoformat() if latest and db.settings().get('collection_enabled',True) else None
    stats.update(last_run=latest['started_at'] if latest else None,next_run=next_run,sources_ok=sum(x['status'] in ('正常','部分可用') for x in src),sources_total=len(src))
    return {**result,'ai':ai.config(),'latest_run':latest}

def safe_cell(value: object) -> str:
    text=str(value or '')
    return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r')) else text

@app.get('/api/export',dependencies=[Depends(require_user)])
def export(request:Request,f:Filters=Depends(),user=Depends(require_user)):
    querying.validate_params('opportunities',request.query_params.keys())
    rows=filtered_items(f,user)[:10000]
    stream=io.StringIO();stream.write('\ufeff');writer=csv.writer(stream)
    writer.writerow(['标题','评分','方向','阶段','省份','采购人','发布日期','投标截止','已确认未截止','金额原文','来源','原文链接','跟进状态','备注','风险提示'])
    for x in rows:
        writer.writerow([safe_cell(v) for v in (x['title'],x['score'],x['category'],x['stage'],x['province'],x['buyer'],x['published_at'],x['deadline'],'是' if x['actionable'] else '待核实/已结束',x['budget_text'],x['source_name'],x['url'],x['status'],x['note'],'；'.join(x['risks']))])
    db.audit('商机导出',str(len(rows)))
    return Response(content=stream.getvalue().encode('utf-8'),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="tender-radar.csv"'})

@app.get('/api/report',dependencies=[Depends(require_user)])
def report(user=Depends(require_user)):
    dash=dashboard(user);minimum=db.settings()['min_score']
    rows=[x for x in accounts.items(user) if x['score']>=minimum and x['status'] not in ('已排除','已归档')]
    rows.sort(key=lambda x:(x['actionable'],x['score'],x.get('published_at') or ''),reverse=True)
    warnings=[x['name']+'：'+(x['last_error'] or x['note']) for x in source_rows() if x['enabled'] and x['status']!='正常'] if user['role']=='admin' else []
    return {'generated_at':db.now(),'summary':f"已收录{dash['stats']['total']}条公告与线索，当前有{dash['stats']['actionable']}条具备未截止证据；{dash['stats']['high_priority']}条达到公共关注分数。个人专题结果见订阅推荐。",'opportunities':rows[:12],'warnings':warnings}

@app.exception_handler(RequestValidationError)
async def validation_error(request,exc):
    # Never echo submitted passwords or API keys from Pydantic validation errors.
    fields=list(dict.fromkeys(str(e['loc'][-1]) for e in exc.errors()))
    return JSONResponse(status_code=422,content={'detail':'输入不符合要求，请检查：'+ '、'.join(fields)})

app.include_router(management_router)

app.mount('/static',StaticFiles(directory=STATIC),name='static')

ASSET=re.compile(r'(/static/[\w./-]+\.(?:js|css))(?:\?v=[\w-]*)?')
INDEX_CACHE={}

def versioned_index():
    """index.html with every script/style URL stamped by a content hash, rebuilt when any asset changes."""
    files=sorted(STATIC.glob('*.*'))
    key=tuple((f.name,f.stat().st_mtime_ns,f.stat().st_size) for f in files)
    if INDEX_CACHE.get('key')!=key:
        def stamp(match):
            path=STATIC/match.group(1).removeprefix('/static/')
            return match.group(1)+'?v='+hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.is_file() else match.group(0)
        INDEX_CACHE.update(key=key,html=ASSET.sub(stamp,(STATIC/'index.html').read_text(encoding='utf-8')))
    return INDEX_CACHE['html']

@app.get('/')
def index():
    # The page itself is always revalidated; assets carry content hashes so they can be cached for long.
    return Response(versioned_index(),media_type='text/html; charset=utf-8',headers={'Cache-Control':'no-cache'})
