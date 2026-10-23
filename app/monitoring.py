"""User-managed, bounded public website monitors and dry-run configuration tests."""
from __future__ import annotations
from . import clock
import hashlib
import ipaddress
import json
import re
import socket
import time
import uuid
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Literal
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree
import httpx
import soupsieve
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from . import accounts, db
from .analysis import date_precision

router=APIRouter(prefix='/api')
USER_AGENT='TenderRadar/2.1 (public procurement monitoring)'

def validate_public_url(url,resolve=False):
    try:
        p=urlsplit(url)
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.fragment or p.port not in (None,80 if p.scheme=='http' else 443):
            raise ValueError('仅支持无账号密码的公网HTTP/HTTPS标准端口网址')
        hostname=p.hostname.lower().rstrip('.')
        if hostname=='localhost' or hostname.endswith(('.localhost','.local','.internal')):raise ValueError('不能监测内网或本机地址')
        if any(re.search(r'password|secret|token|api.?key|authorization',k,re.I) for k,v in parse_qsl(p.query)):
            raise ValueError('网址不能包含密钥或登录令牌参数')
        try:addresses=[ipaddress.ip_address(hostname)]
        except ValueError:
            addresses=[]
            if resolve:
                addresses=list(dict.fromkeys(ipaddress.ip_address(a[4][0]) for a in socket.getaddrinfo(hostname,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM)))
                if not addresses:raise ValueError('域名没有可用地址')
        if any(not a.is_global for a in addresses):raise ValueError('监测网站必须是公网地址')
        return str(addresses[0]) if addresses else None
    except (OSError,OverflowError) as exc:raise ValueError('无法解析网站公网地址') from exc

class SourceConfig(BaseModel):
    name:str=Field(min_length=1,max_length=80)
    url:str=Field(min_length=8,max_length=1000)
    kind:str=Field(default='自定义监测',max_length=40)
    adapter:Literal['html','rss','json']='html'
    extra_urls:list[str]=Field(default_factory=list,max_length=4)
    item_selector:str=Field(default='a[href]',max_length=300)
    link_selector:str=Field(default='',max_length=300)
    title_selector:str=Field(default='',max_length=300)
    date_selector:str=Field(default='',max_length=300)
    content_selector:str=Field(default='article,.article-content,.content',max_length=300)
    items_path:str=Field(default='',max_length=150)
    title_field:str=Field(default='title',max_length=100)
    url_field:str=Field(default='url',max_length=100)
    date_field:str=Field(default='published_at',max_length=100)
    content_field:str=Field(default='',max_length=100)
    include_keywords:list[str]=Field(default_factory=list,max_length=30)
    exclude_keywords:list[str]=Field(default_factory=list,max_length=50)
    max_items:int=Field(default=20,ge=1,le=40)
    note:str=Field(default='',max_length=1000)
    enabled:bool=False
    test_token:str=Field(default='',max_length=40,exclude=True)

    @model_validator(mode='after')
    def check(self):
        self.name=self.name.strip();self.url=self.url.strip()
        if not self.name:raise ValueError('请输入监测名称')
        for url in [self.url]+self.extra_urls:
            validate_public_url(url)
            if urlsplit(url).hostname!=urlsplit(self.url).hostname:raise ValueError('补充列表页必须与入口属于同一域名')
        for field in ('item_selector','link_selector','title_selector','date_selector','content_selector'):
            value=getattr(self,field).strip();setattr(self,field,value)
            if value:
                try:soupsieve.compile(value)
                except Exception as exc:raise ValueError('CSS规则格式不正确：'+field) from exc
        if self.adapter=='html' and not self.item_selector:raise ValueError('网页列表需要列表项选择器')
        for words in (self.include_keywords,self.exclude_keywords):
            if any(not w.strip() or len(w)>60 for w in words):raise ValueError('关键词需为1–60字')
        for field in ('include_keywords','exclude_keywords'):
            setattr(self,field,list(dict.fromkeys(w.strip() for w in getattr(self,field))))
        return self

class MonitorClient:
    def __init__(self):
        self.client=httpx.Client(timeout=httpx.Timeout(15,connect=8),follow_redirects=False,trust_env=False,headers={'User-Agent':USER_AGENT})
        self.robots={};self.started=time.monotonic();self.requests=0;self.last=0
    def close(self):self.client.close()
    def request(self,url):
        ip=validate_public_url(url,resolve=True);p=urlsplit(url)
        if self.requests>=60 or time.monotonic()-self.started>100:raise ValueError('达到单来源请求或时长上限，下轮继续')
        delay=.65-(time.monotonic()-self.last)
        if delay>0:time.sleep(delay)
        self.last=time.monotonic();self.requests+=1
        # Pin the validated address. Host and SNI retain the original TLS identity.
        target=httpx.URL(url).copy_with(host=ip)
        remaining=max(.1,100-(time.monotonic()-self.started))
        with self.client.stream('GET',target,headers={'Host':p.netloc},extensions={'sni_hostname':p.hostname},timeout=httpx.Timeout(min(15,remaining),connect=min(8,remaining))) as response:
            if response.is_redirect:raise ValueError('网站发生跳转，请改用最终公开地址后测试')
            if response.status_code in (401,403,429):raise ValueError('网站限制访问（HTTP '+str(response.status_code)+'），需人工核查')
            response.raise_for_status();chunks=[];size=0
            for chunk in response.iter_bytes():
                if time.monotonic()-self.started>100:raise ValueError('达到单来源读取时长上限，下轮重试')
                size+=len(chunk)
                if size>4_000_000:raise ValueError('页面超过4MB限制')
                chunks.append(chunk)
            data=b''.join(chunks)
            match=re.search(br'charset=["\s]*([\w-]+)',data[:5000],re.I)
            encoding=match.group(1).decode() if match else response.encoding or 'utf-8'
            try:return data.decode(encoding,errors='replace')
            except LookupError:return data.decode('utf-8',errors='replace')
    def get(self,url):
        p=urlsplit(url);origin=f'{p.scheme}://{p.netloc}'
        if origin not in self.robots:
            try:
                text=self.request(origin+'/robots.txt');parser=RobotFileParser()
                if 'user-agent:' in text.lower():parser.parse(text.splitlines());self.robots[origin]=parser
                else:self.robots[origin]=None
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (404,410):raise ValueError('无法确认robots规则') from exc
                self.robots[origin]=None
        if self.robots[origin] and not self.robots[origin].can_fetch(USER_AGENT,url):raise ValueError('网站robots规则不允许监测此页面')
        return self.request(url)

def dotted(value,path):
    for key in path.split('.') if path else []:
        if not isinstance(value,dict):return None
        value=value.get(key)
    return value

def parse_candidates(text,config,url):
    rows=[];adapter=config['adapter']
    if re.search(r'CF_APP_WAF|aliyunCaptcha|captcha-container|人机验证|访问验证',text,re.I):raise ValueError('网站要求访问验证，无法自动监测')
    if adapter=='html':
        soup=BeautifulSoup(text,'html.parser')
        for n in soup.select('script,style,nav,header,footer,noscript'):n.decompose()
        for node in soup.select(config['item_selector'])[:2000]:
            anchor=node.select_one(config['link_selector']) if config['link_selector'] else node if node.name=='a' else node.select_one('a[href]')
            if not anchor:continue
            title=node.select_one(config['title_selector']) if config['title_selector'] else anchor
            date=node.select_one(config['date_selector']) if config['date_selector'] else None
            rows.append({'title':title.get_text(' ',strip=True) if title else '', 'url':anchor.get('href',''), 'published_at':date.get('datetime') or date.get_text(' ',strip=True) if date is not None else None})
    elif adapter=='rss':
        if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():raise ValueError('订阅源包含不支持的XML声明')
        root=ElementTree.fromstring(text)
        for node in root.iter():
            if node.tag.split('}')[-1] not in ('item','entry'):continue
            fields={x.tag.split('}')[-1]:x for x in node}
            def value(key):return ''.join(fields[key].itertext()).strip() if key in fields else ''
            link=fields.get('link');date=value('pubDate') or value('published') or value('updated')
            if date:
                try:date=parsedate_to_datetime(date).isoformat()
                except (ValueError,TypeError):pass
            rows.append({'title':value('title'),'url':link.get('href','') or value('link') if link is not None else '', 'published_at':date or None})
    else:
        data=dotted(json.loads(text),config.get('items_path',''))
        if not isinstance(data,list):raise ValueError('JSON列表路径未返回数组，请检查列表路径')
        for row in data[:2000]:
            if not isinstance(row,dict):continue
            item={'title':dotted(row,config['title_field']),'url':dotted(row,config['url_field']),'published_at':dotted(row,config['date_field'])}
            if config.get('content_field'):item['content']=BeautifulSoup(str(dotted(row,config['content_field']) or ''),'html.parser').get_text('\n',strip=True)[:65000]
            rows.append(item)
    result={}
    for row in rows:
        title=row.get('title');href=row.get('url')
        if not isinstance(title,str) or not isinstance(href,str) or len(title.strip())<6 or not href.strip() or href.startswith('#'):continue
        absolute=urljoin(url,href.strip());parts=urlsplit(absolute);absolute=urlunsplit(parts._replace(fragment=''))
        if parts.hostname!=urlsplit(config['url']).hostname:continue
        try:validate_public_url(absolute)
        except ValueError:continue
        if absolute in [config['url']]+config.get('extra_urls',[]):continue
        if config.get('include_keywords') and not any(w.lower() in title.lower() for w in config['include_keywords']):continue
        if any(w.lower() in title.lower() for w in config.get('exclude_keywords',[])):continue
        date=row.get('published_at')
        date=date[:100] if isinstance(date,str) else None
        result[absolute]={**row,'title':re.sub(r'\s+',' ',title).strip()[:500],'url':absolute,'published_at':date,'published_at_precision':date_precision(date)}
    return list(result.values())[:config.get('max_items',20)]

def read_candidates(client,config):
    items={}
    for url in [config['url']]+config.get('extra_urls',[]):
        for row in parse_candidates(client.get(url),config,url):items[row['url']]=row
    return list(items.values())[:config.get('max_items',20)]

def read_detail(client,row,config):
    if row.get('content') and len(row['content'])>=120:return row
    if not config.get('content_selector'):return {**row,'content':'','detail_status':'仅列表线索'}
    text=client.get(row['url'])
    if re.search(r'CF_APP_WAF|aliyunCaptcha|captcha-container|人机验证|访问验证',text,re.I):raise ValueError('详情需要访问验证')
    soup=BeautifulSoup(text,'html.parser')
    for n in soup.select('script,style,nav,header,footer,noscript'):n.decompose()
    node=soup.select_one(config['content_selector'])
    body=node.get_text('\n',strip=True) if node else ''
    if len(body)<120:raise ValueError('正文规则未取得足够正文，保留列表线索')
    return {**row,'content':body[:65000],'detail_status':'正文可读','detail_fetched_at':db.now()}

def inspect(config):
    client=MonitorClient()
    try:
        rows=read_candidates(client,config);samples=[];warnings=[]
        for row in rows[:3]:
            try:samples.append(read_detail(client,row,config))
            except (ValueError,httpx.HTTPError):
                samples.append({**row,'content':'','detail_status':'仅列表线索'})
                warnings.append('部分详情未取得正文，保存启用后仍会标明仅列表线索。')
        return {'passed':bool(rows),'found':len(rows),'items':samples,'warnings':list(dict.fromkeys(warnings)),'notice':'测试不写入公告库；无结果时请检查规则、关键词或网站是否为动态页面'}
    finally:client.close()

def fingerprint(config):
    return hashlib.sha256(db.dumps({k:v for k,v in config.items() if k not in ('enabled','test_token','id')}).encode()).hexdigest()

def initialize():
    with db.connect() as c:c.execute('CREATE TABLE IF NOT EXISTS source_tests (token TEXT PRIMARY KEY,user_id INTEGER,config_hash TEXT,created_at TEXT)')

def verified(body,user):
    with db.connect() as c:
        row=c.execute('SELECT 1 FROM source_tests WHERE token=? AND user_id=? AND config_hash=? AND created_at>?',(body.test_token,user['id'],fingerprint(body.model_dump()),(clock.now()-timedelta(hours=1)).isoformat())).fetchone()
    return bool(row)

@router.post('/source-preview')
def preview_source(body:SourceConfig,user=Depends(accounts.require_admin)):
    try:result=inspect(body.model_dump())
    except Exception as exc:
        message=str(exc)[:180] if isinstance(exc,ValueError) and not isinstance(exc,json.JSONDecodeError) else '网站读取失败，请检查公开网址、访问限制或采集规则'
        raise HTTPException(400,message) from exc
    if result['passed']:
        token=uuid.uuid4().hex
        with db.connect() as c:
            c.execute('DELETE FROM source_tests WHERE created_at<?',((clock.now()-timedelta(days=1)).isoformat(),))
            c.execute('INSERT INTO source_tests VALUES(?,?,?,?)',(token,user['id'],fingerprint(body.model_dump()),db.now()))
        result['test_token']=token
    return result

def save_source(body,user,ident=None):
    from .main import RUN_LOCK
    if not RUN_LOCK.acquire(blocking=False):raise HTTPException(409,'当前采集正在运行，请完成后保存网站配置')
    try:
        with db.connect() as c:
            old=c.execute('SELECT config FROM sources WHERE id=?',(ident,)).fetchone() if ident else None
            if ident and (not old or not json.loads(old[0]).get('custom')):raise HTTPException(404,'未找到自定义监测网站')
            previous=json.loads(old[0]) if old else None
            unchanged=previous and previous.get('enabled') and fingerprint({k:v for k,v in previous.items() if k in SourceConfig.model_fields})==fingerprint(body.model_dump())
            if body.enabled and not unchanged and not verified(body,user):raise HTTPException(409,'请先测试当前配置，匹配成功后在一小时内启用')
            if not ident and c.execute("SELECT COUNT(*) FROM sources WHERE json_extract(config,'$.custom')=1").fetchone()[0]>=20:raise HTTPException(409,'当前最多20个自定义监测网站')
            ident=ident or 'custom_'+uuid.uuid4().hex[:12]
            value={**body.model_dump(),'id':ident,'custom':True,'updated_at':db.now()}
            c.execute("INSERT INTO sources(id,config,status) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config,status=excluded.status,last_error=NULL",(ident,db.dumps(value),'待采集' if body.enabled else '已停用'))
        db.audit('保存监测网站',ident,'操作人：'+user['username'])
        return value
    finally:RUN_LOCK.release()

@router.post('/source-configs')
def create_source(body:SourceConfig,user=Depends(accounts.require_admin)):return save_source(body,user)

@router.put('/source-configs/{ident}')
def update_source(ident:str,body:SourceConfig,user=Depends(accounts.require_admin)):return save_source(body,user,ident)

class Toggle(BaseModel):enabled:bool

@router.patch('/source-configs/{ident}/state')
def toggle_source(ident:str,body:Toggle,user=Depends(accounts.require_admin)):
    from .collectors import BUILTIN_SOURCE_IDS
    from .main import RUN_LOCK
    if not RUN_LOCK.acquire(blocking=False):raise HTTPException(409,'采集运行中，请稍后调整来源')
    try:
        with db.connect() as c:
            row=c.execute('SELECT config FROM sources WHERE id=?',(ident,)).fetchone()
            if not row:raise HTTPException(404,'来源不存在')
            value=json.loads(row[0])
            if body.enabled and (value.get('custom') or ident not in BUILTIN_SOURCE_IDS):
                raise HTTPException(409,'请编辑网站规则并测试后启用；参考入口可添加为自定义监测')
            value['enabled']=body.enabled
            c.execute('UPDATE sources SET config=?,status=? WHERE id=?',(db.dumps(value),'待采集' if body.enabled else '已停用',ident))
        db.audit('调整来源启停',ident,'操作人：'+user['username'])
        return value
    finally:RUN_LOCK.release()

def collect_custom(source,companies,settings):
    from .analysis import analyze
    client=MonitorClient();counts={'source':source['name'],'found':0,'inserted':0,'updated':0,'unchanged':0,'restricted':0,'errors':[]}
    try:
        rows=read_candidates(client,source);counts['found']=len(rows)
        if not rows:raise ValueError('未匹配公告，请检查规则、过滤条件或页面变化')
        failures=0
        for row in rows:
            with db.connect() as c:old=c.execute('SELECT payload FROM opportunities WHERE url=?',(row['url'],)).fetchone()
            cached=json.loads(old[0]) if old else {}
            try:
                if failures>=3:raise ValueError('详情连续不可读，本轮保留列表')
                if cached.get('content') and cached.get('detail_fetched_at','')>(clock.now()-timedelta(hours=6)).isoformat():
                    row.update(content=cached['content'],detail_status='正文可读',detail_fetched_at=cached['detail_fetched_at'])
                else:row=read_detail(client,row,source)
                if row.get('content'):failures=0
                else:counts['restricted']+=1
            except (ValueError,httpx.HTTPError):
                failures+=1;counts['restricted']+=1
                row.update(content=cached.get('content',''),detail_status='详情暂不可读；保留历史正文' if cached.get('content') else '仅列表线索')
                if cached.get('detail_fetched_at'):row['detail_fetched_at']=cached['detail_fetched_at']
            row.update(source_id=cached.get('source_id',source['id']),source_name=cached.get('source_name',source['name']),source_kind=cached.get('source_kind',source['kind']))
            observed=sorted(set(cached.get('observed_source_ids',[])+[row['source_id'],source['id']]))
            if len(observed)>1:row['observed_source_ids']=observed
            if cached.get('superseded_by'):row['superseded_by']=cached['superseded_by']
            counts[db.upsert(analyze(row,companies,settings))]+=1
        state='部分可用' if counts['restricted'] else '正常'
        if counts['restricted']:counts['errors']=['部分详情未取得正文，请核查原网站']
        with db.connect() as c:c.execute('UPDATE sources SET status=?,last_success=?,last_count=?,last_error=? WHERE id=?',(state,db.now(),len(rows),'；'.join(counts['errors']) or None,source['id']))
    except Exception as exc:
        message=str(exc)[:180] if isinstance(exc,ValueError) and not isinstance(exc,json.JSONDecodeError) else '网站读取或解析失败，请测试规则及来源可用性'
        counts['errors'].append(message)
        with db.connect() as c:c.execute("UPDATE sources SET status='异常',last_error=? WHERE id=?",(message,source['id']))
    finally:client.close()
    return counts
