"""Bounded public-source collectors. Never solve captchas or use login cookies."""
from __future__ import annotations
from . import clock
import hashlib
import io
import json
import logging
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlsplit,parse_qs
from .robots_rules import PublicRobots
import httpx
from bs4 import BeautifulSoup
from . import db
from .analysis import DOMAIN, CATEGORIES, DATE, analyze, parse_date, date_precision
from . import industry_sources, public_resource_sources, procurement_sources, enterprise_sources, commercial_sources, bidi_sources, group_procurement_sources

LOG=logging.getLogger(__name__)
USER_AGENT='TenderRadar/1.0 (+internal procurement research; low-rate public pages)'
SOURCES=[
 {'id':'cecep','name':'中节能 · 慧采云','url':'https://new.ebidding.cecep.cn/','kind':'集团采购','enabled':True,'note':'公开检索接口+详情；行业关键词各取最新40条，回看180天'},
 {'id':'everbright','name':'光大环境 · 招采平台','url':'https://zcpt.cebenvironment.com.cn/','kind':'集团采购','enabled':True,'note':'已核实官方域名；首页各阶段公开公告，覆盖受首页容量限制'},
 {'id':'bjx','name':'北极星 · 垃圾发电','url':'https://ljfd.bjx.com.cn/zb/','kind':'行业门户','enabled':True,'note':'招标栏目前3页；详情遇访问验证时降级为标题线索'},
 {'id':'gfcl','name':'北极星 · 固废招标','url':'https://gfcl.bjx.com.cn/zb/','kind':'行业门户','enabled':True,'note':'招标栏目前2页；与垃圾发电重复的原文URL自动去重'},
 {'id':'cseg','name':'三峰环境 · 公开采购','url':'https://www.cseg.cn/index.php?a=lists&catid=15','kind':'集团采购','enabled':True,'note':'官网采购公告列表；页面日期旧时仅作历史线索'},
 {'id':'grandblue','name':'瀚蓝 · 招标采购平台','url':'https://s.grandblue.cn','kind':'集团采购','enabled':False,'note':'官网已确认入口；动态应用，尚未完成公告接口适配，需人工查阅'},
 {'id':'ggzy','name':'全国公共资源交易平台','url':'https://www.ggzy.gov.cn','kind':'公共资源','enabled':False,'note':'全国入口保留；各省公告结构不同，尚未接入自动采集'},
 {'id':'cebpub','name':'中国招标投标公共服务平台','url':'https://www.cebpubservice.com','kind':'公共资源','enabled':False,'note':'用于交叉核验，尚未接入自动采集'},
 {'id':'ccgp','name':'中国政府采购网','url':'https://www.ccgp.gov.cn','kind':'公共资源','enabled':False,'note':'政府采购补充入口，尚未接入自动采集'},
 {'id':'hubei','name':'湖北公共资源交易','url':'https://www.hbggzyfwpt.cn','kind':'公共资源','enabled':False,'note':'地区扩展候选源，尚未接入自动采集'},
 {'id':'sjec','name':'康恒环境','url':'https://www.shsus.com/','kind':'集团采购','enabled':False,'note':'已核实当前官方网站；供应商平台公开查询接口尚未稳定读取'},
 {'id':'sinocycle','name':'中科环保','url':'https://www.cset.ac.cn/','kind':'集团采购','enabled':False,'note':'已核实当前官方网站；公开采购栏目仍待核实'},
 {'id':'hbzhan','name':'环保在线','url':'https://www.hbzhan.com','kind':'行业门户','enabled':False,'note':'转载交叉核验入口，暂未自动采集'},
 {'id':'solidwaste','name':'中国固废网','url':'https://www.solidwaste.com.cn','kind':'行业门户','enabled':False,'note':'行业新闻参考，暂未自动采集'},
 {'id':'cnhw','name':'环卫科技网','url':'https://www.cn-hw.net','kind':'行业门户','enabled':False,'note':'环卫补充参考，暂未自动采集'},
 {'id':'jianyu','name':'剑鱼标讯','url':'https://www.jianyu360.cn','kind':'商业聚合','enabled':False,'requires_authorization':True,'note':'需取得授权数据接口或订阅账号；不绕过付费限制'},
 {'id':'bidi','name':'比地招标网','url':'https://www.bidizhaobiao.com','kind':'商业聚合','enabled':False,'requires_authorization':True,'note':'需授权数据接口，暂未自动采集'},
 {'id':'qianlima','name':'千里马招标网','url':'https://www.qianlima.com','kind':'商业聚合','enabled':False,'requires_authorization':True,'note':'需授权数据接口，暂未自动采集'},
 {'id':'bidcenter','name':'采招网','url':'https://www.bidcenter.com.cn','kind':'商业聚合','enabled':False,'requires_authorization':False,'note':'部分公开邀请公告可读，栏目规则尚未适配；会员内容按网站权限访问'},
 {'id':'chinabidding','name':'中国采购与招标网','url':'https://www.chinabidding.cn','kind':'商业聚合','enabled':False,'requires_authorization':True,'note':'需授权数据接口，暂未自动采集'},
 {'id':'dynagreen','name':'绿色动力','url':'https://www.dynagreen.com.cn/','kind':'集团采购','enabled':False,'note':'官方网站已核实；采购公告栏目待核实，尚未自动采集'},
 {'id':'szenergy','name':'深圳能源 · 电子招标平台','url':'https://zb.sec.com.cn/','kind':'集团采购','enabled':False,'note':'深圳能源集团官方招标平台，含深能环保焚烧厂项目'},
 {'id':'wangneng','name':'旺能环境','url':'https://wannaenergy.com/','kind':'集团采购','enabled':False,'note':'官方网站已核实；采购公告栏目待核实，尚未自动采集'},
 {'id':'junxin','name':'军信股份','url':'https://www.junxinep.com/index.html','kind':'集团采购','enabled':False,'note':'官方网站已核实；采购公告栏目待核实，尚未自动采集'},
 {'id':'bece','name':'首创环境','url':'https://ecp.capitaleco-pro.com/','kind':'集团采购','enabled':False,'note':'集团官方公开采购平台，包含首创环境公告；采集接口与详情规则尚未适配'},
 {'id':'beeg','name':'北控环保 · 集团环境业务入口','url':'https://www.behl.com.hk/sc/business/environment.php','kind':'集团采购','enabled':False,'note':'母集团官方环境业务页面；北控环保独立采购公告入口待核实，尚未自动采集'},
 {'id':'jilin','name':'吉林公共资源交易','url':'https://www.jl.gov.cn/ggzy/','kind':'公共资源','enabled':False,'note':'吉林省公共资源交易公共服务平台；公告列表与详情规则待适配，尚未自动采集'},
 {'id':'datong','name':'大同公共资源交易','url':'http://ggzyjy.dt.gov.cn/','kind':'公共资源','enabled':False,'note':'大同市政府网站名录登记入口；公告列表与详情规则待适配，尚未自动采集'},
 {'id':'bjx-environment','name':'北极星 · 环保','url':'https://huanbao.bjx.com.cn/','kind':'行业门户','enabled':False,'note':'北极星环保总站；栏目规则待适配，可能要求访问验证，尚未自动采集'},
]
SOURCE_ADAPTERS=(industry_sources,public_resource_sources,procurement_sources,enterprise_sources,commercial_sources,bidi_sources,group_procurement_sources)
BUILTIN_SOURCE_IDS=frozenset({'cecep','everbright','bjx','gfcl','cseg'}).union(*(m.SOURCE_IDS for m in SOURCE_ADAPTERS))
PUBLIC_SOURCE_NOTES={
  'cecep':'公开关键词检索，每词最多3页、每页40条，回看180天；每轮轮换最多18词，候选最多300条。',
  'everbright':'官网首页及已公开的采购栏目、列表iframe与分页，列表最多20次读取、候选230条；正文支持公开PDF与网页文本。',
  'bjx':'垃圾发电招标栏目沿已公开分页最多5页、候选250条；静态详情受限时使用浏览器渲染，保留网页文字，公告图片不下载、不识别；缺失部分标明并保留原文入口。',
  'gfcl':'固废招标栏目沿已公开分页最多5页、候选250条；保留列表及可取得的网页文字，公告图片不下载、不识别；非全量历史库。',
  'cseg':'官网项目公告沿已公开分页最多5页、候选250条；图片正文使用本地OCR并保留原图，历史公告保留原日期。',
 'grandblue':'本次核验robots入口返回HTTP 403，自动读取规则尚无法确认，暂未启用。',
 'ggzy':'覆盖官网首页公开公告窗口，非全国所有公告。',
 'cebpub':'本次核验robots规则禁止自动抓取全站，保留网站入口供人工查阅。',
  'ccgp':'中央和地方公开公告栏目各最多3页，合计最多6页、候选200条；不是政府采购全量库。',
 'hubei':'覆盖官网首页工程建设招标公告，正文来自网站公开查询接口。',
 'jilin':'首页可访问，但公告查询接口本次返回HTTP 403，暂未启用。',
  'datong':'官网首页及已公开工程建设、政府采购栏目各最多3页，合计最多7次列表读取、候选200条；旧公告保留真实时间。',
  'hbzhan':'公开招投标专题沿已公开分页最多5页、候选180条，非整个网站全部资讯。',
  'solidwaste':'公开行业资讯首页及项目栏目最多6次列表读取、候选180条，非全部招标库。',
 'cnhw':'覆盖公开资讯首页；仅提供相对发布时间的文章标为发布时间未知。',
 'bjx-environment':'本次核验页面返回访问验证，暂无法自动读取；不处理验证码。',
 'dynagreen':'已找到官网采购平台，但本次连接超时；采购入口使用8000端口，当前采集器未开放。',
 'wangneng':'官网可访问，尚未核实稳定的公开采购公告栏目。',
 'junxin':'本次核验公开入口连接超时或被关闭，采购栏目仍待验证。',
 'beeg':'当前是集团环境业务介绍页面，尚未核实独立采购公告入口。',
 'sjec':'原官网已迁移，已找到当前官方供应商平台；公开列表接口本次未稳定读取。',
 'sinocycle':'原网址不可达，已找到当前官方公司网站；公开采购栏目仍待核实。',
 'jianyu':'公开首页可访问，但抽查文档返回登录界面；公开公告规则或授权数据渠道仍待接入。',
 'bidi':'本次首页未返回可解析的公开公告列表，公开规则或授权数据渠道仍待接入。',
 'qianlima':'公开首页可访问；公开公告规则尚未适配，完整订阅功能需按网站条件接入。',
 'bidcenter':'已核实部分公开公告正文可读；公开栏目尚未适配，会员内容按网站权限访问。',
 'chinabidding':'本次公开首页请求返回HTTP 405，需核实可用公开入口或授权渠道。',
 'bece':procurement_sources.COVERAGE_NOTES['bece'],
}
PUBLIC_SOURCE_NOTES.update(enterprise_sources.BLOCKED_NOTES)
PUBLIC_SOURCE_NOTES.update(enterprise_sources.COVERAGE_NOTES)
PUBLIC_SOURCE_NOTES.update(commercial_sources.COVERAGE_NOTES)
PUBLIC_SOURCE_NOTES.update(group_procurement_sources.COVERAGE_NOTES)
PUBLIC_SOURCE_NOTES.update({
 'jianyu':'首页可访问；企业直采列表返回动态壳，公开列表读取尚未接通，完整信息按网站授权接入。',
 'bidi':bidi_sources.COVERAGE_NOTES['bidi'],
 'qianlima':'首页及免费栏目可访问；抽样正文存在会员遮挡字段，完整公告渠道仍待授权接入。',
 'chinabidding':'本次公开首页返回HTTP 405，未取得公告列表，需核实公开入口或授权渠道。',
})
for _source in SOURCES:
    _source['public_note']=PUBLIC_SOURCE_NOTES.get(_source['id'],'')
    if _source['id'] in industry_sources.SOURCE_IDS:
        _source['url']=industry_sources.LIST_URLS[_source['id']]
    if _source['id'] in procurement_sources.SOURCE_IDS:
        _source['url']=procurement_sources.CANONICAL_URLS[_source['id']]
    if _source['id'] in enterprise_sources.SOURCE_IDS:
        _source['url']=enterprise_sources.CANONICAL_URLS[_source['id']]
        _source['kind']='前期公示'
    if _source['id'] in commercial_sources.SOURCE_IDS:
        _source['url']=commercial_sources.CANONICAL_URLS[_source['id']]
    if _source['id'] in group_procurement_sources.SOURCE_IDS:
        _source['url']=group_procurement_sources.CANONICAL_URLS[_source['id']]
    if _source['id'] in bidi_sources.SOURCE_IDS:
        _source['url']=bidi_sources.CANONICAL_URLS[_source['id']]
        _source['requires_authorization']=False
    if _source['id'] in BUILTIN_SOURCE_IDS:
        _source['enabled']=True
        _source['note']=_source['public_note']
ALLOWED_HOSTS={urlsplit(x['url']).hostname for x in SOURCES if x['id'] in BUILTIN_SOURCE_IDS}|{'huanbao.bjx.com.cn','www.bidizhaobiao.com'}
ALLOWED_HOSTS.update(*(m.EXTRA_HOSTS for m in SOURCE_ADAPTERS))
ALLOWED_PORTS={host:port for m in SOURCE_ADAPTERS for host,port in getattr(m,'EXTRA_PORTS',{}).items()}
ALLOWED_HOSTS.add('img01.mybjx.net')  # Public article screenshots, downloaded only by bounded OCR.

def source_adapter(source_id: str):
    return next((module for module in SOURCE_ADAPTERS if source_id in module.SOURCE_IDS),None)

def public_failure_note(source: dict) -> str:
    """Only fixed explanations are public; never echo stored errors or URLs."""
    from .source_health import failure_note
    return failure_note(source)

class SourceUnavailable(RuntimeError):
    pass

class PublicClient:
    def __init__(self):
        self.client=httpx.Client(timeout=httpx.Timeout(22,connect=10),follow_redirects=False,headers={'User-Agent':USER_AGENT})
        # requests retains the existing 500-call budget. Retries count as
        # separate actual HTTP attempts for diagnostics without relaxing it.
        self.robots={};self.last={};self.requests=0;self.http_attempts=0
        self.listing_warnings=[];self.listing_pages=0
        self.browser=None;self.browser_reads=0;self.ocr_articles=0

    def close(self) -> None:
        if self.browser is not None:self.browser.close()
        self.client.close()

    def _request(self,url: str,method: str='GET',accept_redirect: bool=False,max_bytes: int=4_000_000,**kwargs) -> httpx.Response:
        parts=urlsplit(url)
        if parts.hostname not in ALLOWED_HOSTS or parts.scheme not in ('https','http') or parts.port not in (None,443,80,ALLOWED_PORTS.get(parts.hostname)):
            raise SourceUnavailable('来源链接超出已核实的域名白名单')
        delay=0.65-(time.monotonic()-self.last.get(parts.hostname,0))
        if delay>0: time.sleep(delay)
        self.last[parts.hostname]=time.monotonic()
        self.requests+=1
        if self.requests>500: raise SourceUnavailable('本轮已达到请求数量上限')
        for attempt in range(2):
            try:
                self.http_attempts+=1
                r=self.client.request(method,url,**kwargs)
                if r.status_code in (301,302,303,307,308):
                    if accept_redirect:return r
                    raise SourceUnavailable('来源发生跳转，需核验新地址后适配')
                if r.status_code in (401,403,429):
                    raise SourceUnavailable(f'站点限制访问（HTTP {r.status_code}），本轮停止重试')
                if r.status_code>=500 and attempt==0:
                    time.sleep(2);continue
                r.raise_for_status()
                if len(r.content)>max_bytes: raise SourceUnavailable(f'响应超过{max_bytes//1_000_000}MB限制')
                return r
            except (httpx.TimeoutException,httpx.ConnectError):
                if attempt: raise SourceUnavailable('网络连接超时或不可达')
                time.sleep(2)
        raise SourceUnavailable('网络请求失败')

    def allowed(self,url: str) -> bool:
        p=urlsplit(url);origin=f'{p.scheme}://{p.netloc}'
        if origin in group_procurement_sources.ROBOTS_ABSENT:self.robots.setdefault(origin,None)
        if origin not in self.robots:
            try:
                target=origin+'/robots.txt';visited=set()
                for _ in range(4):
                    if target in visited:raise SourceUnavailable('robots规则发生循环跳转，无法核验')
                    visited.add(target)
                    response=self._request(target,accept_redirect=True)
                    if not response.is_redirect:break
                    target=urljoin(target,response.headers.get('location',''))
                    redirect=urlsplit(target)
                    if f'{redirect.scheme}://{redirect.netloc}'!=origin:
                        raise SourceUnavailable('robots规则跳转到其他站点，需核验后适配')
                else:raise SourceUnavailable('robots规则跳转过多，无法核验')
                if 'user-agent:' in response.text.lower():
                    self.robots[origin]=PublicRobots(response.text)
                else:self.robots[origin]=None
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (404,410): raise SourceUnavailable('无法核验robots规则') from exc
                self.robots[origin]=None
        parser=self.robots[origin]
        return not parser or parser.can_fetch(USER_AGENT,url)

    def get(self,url: str) -> str:
        if not self.allowed(url): raise SourceUnavailable('站点robots规则不允许自动抓取此页面')
        response=self._request(url)
        match=re.search(br'charset=["\s]*([\w-]+)',response.content[:5000],re.I)
        encoding=match.group(1).decode() if match else 'utf-8'
        return response.content.decode(encoding,errors='replace')

    def post(self,url: str,payload: dict) -> dict:
        if not self.allowed(url): raise SourceUnavailable('站点robots规则不允许查询此接口')
        return self._request(url,'POST',json=payload).json()

    def get_bytes(self,url: str) -> bytes:
        if not self.allowed(url):raise SourceUnavailable('站点robots规则不允许读取此图片')
        return self._request(url).content

    def get_pdf_bytes(self,url: str) -> bytes:
        if not self.allowed(url):raise SourceUnavailable('站点robots规则不允许读取此PDF')
        return self._request(url,max_bytes=20_000_000).content

    def get_rendered(self,url: str) -> str:
        from .browser_fetch import BrowserReader,BrowserReadError
        if self.browser is None:self.browser=BrowserReader()
        try:
            # Browser navigation shares the source request budget and host delay.
            host=urlsplit(url).hostname
            delay=0.65-(time.monotonic()-self.last.get(host,0))
            if delay>0:time.sleep(delay)
            self.last[host]=time.monotonic()
            self.requests+=1
            if self.requests>500:raise SourceUnavailable('本轮已达到请求数量上限')
            self.browser_reads+=1
            return self.browser.get(url,self.allowed)
        except BrowserReadError as exc:raise SourceUnavailable(str(exc)) from exc

def register_sources() -> None:
    from .monitoring import initialize
    initialize()
    with db.connect() as c:
        for source in SOURCES:
            c.execute('INSERT OR IGNORE INTO sources(id,config,status) VALUES(?,?,?)', (source['id'],db.dumps(source),'待采集' if source['enabled'] else '未接入'))

def source_rows() -> list[dict]:
    with db.connect() as c:
        rows=c.execute('SELECT * FROM sources').fetchall()
    result=[]
    for r in rows:
        source={**json.loads(r['config']),**{k:r[k] for k in ('status','last_success','last_error','last_count')}}
        source['adapter_available']=bool(source.get('custom') or source['id'] in BUILTIN_SOURCE_IDS)
        result.append(source)
    return result

def relevant(title: str,settings: dict) -> bool:
    lowered=title.casefold()
    if any(k.casefold() in lowered for k in settings['exclude_keywords']): return False
    if DOMAIN.search(title) or any(k.casefold() in lowered for k in settings['focus_keywords']):return True
    # One employee's exclusion only narrows that topic's recall branch.
    return any(any(k.casefold() in lowered for k in topic['keywords']) and
               not any(k.casefold() in lowered for k in topic['exclude_keywords'])
               for topic in settings.get('collection_topics',[]))

def unique_keywords(words:list[str]) -> list[str]:
    result=[];seen=set()
    for word in words:
        value=word.strip();key=value.casefold()
        if value and key not in seen:result.append(value);seen.add(key)
    return result

def listing_warning(client, message: str) -> None:
    if not hasattr(client,'listing_warnings'):client.listing_warnings=[]
    if message not in client.listing_warnings:client.listing_warnings.append(message[:140])

def walk_listing_pages(client, roots, links, max_pages=5):
    """Read only disclosed, adapter-validated links; retain earlier pages on failure."""
    queue=list(dict.fromkeys(roots));visited=set();bodies=set();read=False;error=None
    while queue and len(visited)<max_pages:
        url=queue.pop(0)
        if url in visited:continue
        visited.add(url)
        try:
            html=client.get(url)
            if re.search(r'CF_APP_WAF|aliyunCaptcha|captcha-container|访问验证|人机验证',html,re.I):
                raise SourceUnavailable('公开列表要求访问验证，未继续读取此页')
        except (SourceUnavailable,httpx.HTTPError,ValueError) as exc:
            error=error or exc;listing_warning(client,'补充列表未读到，保留已取得候选：'+str(exc))
            continue
        read=True;client.listing_pages=getattr(client,'listing_pages',0)+1
        parts=urlsplit(url)
        family=parts.path.rstrip('/') if parts.path.endswith('/') else parts.path.rsplit('/',1)[0]
        context=tuple((key,tuple(values)) for key,values in sorted(parse_qs(parts.query).items()) if key!='page')
        digest=(parts.netloc,family,context,hashlib.sha256(html.encode()).hexdigest())
        if digest in bodies:continue
        bodies.add(digest)
        yield url,html
        for target in links(html,url):
            if target not in visited and target not in queue:queue.append(target)
    if not read and error:raise error

def disclosed_listing_links(html: str, url: str, valid, *, turn_page=False):
    """Extract href/iframe targets and the verified sites' literal turnPage(n)."""
    soup=BeautifulSoup(html,'html.parser');result=[]
    for node in soup.select('a[href],iframe[src]'):
        target=urljoin(url,node.get('href') or node.get('src') or '')
        if valid(target) and target!=url and target not in result:result.append(target)
    if turn_page:
        current=int(parse_qs(urlsplit(url).query).get('page',['1'])[0])
        if soup.find(attrs={'onclick':re.compile(r'^\s*turnPage\(\s*'+str(current+1)+r'\s*\)\s*;?\s*$')}):
            target=re.sub(r'([?&])page=\d+',lambda m:m.group(1)+'page='+str(current+1),url)
            if target!=url and valid(target) and target not in result:result.append(target)
    return result

def collection_preferences(settings:dict,source_id:str) -> dict:
    """Active personal topics widen recall; public scoring and source rules stay global."""
    from .subscriptions import rows
    topics=[{'keywords':topic['keywords'],'exclude_keywords':topic['exclude_keywords']}
            for topic in rows() if not topic['source_ids'] or source_id in topic['source_ids']]
    words=settings['focus_keywords']+[word for topic in topics for word in topic['keywords']]
    return {**settings,'collection_topics':topics,'search_keywords':unique_keywords(words)}

def clean_text(html: str,source_id: str,rendered: bool=False,minimum: int=120) -> tuple[str,str,str | None]:
    soup=BeautifulSoup(html,'html.parser')
    if not rendered and re.search(r'CF_APP_WAF|aliyunCaptcha|captcha-container|访问验证|人机验证',html,re.I):
        raise SourceUnavailable('详情页要求访问验证，仅保留公开列表线索')
    for node in soup.select('script,style,nav,header,footer,noscript'): node.decompose()
    if source_id=='cecep':
        container=soup.select_one('.article-info')
        title_node=soup.select_one('.article-title,.article-tt')
    elif source_id in ('bjx','gfcl'):
        container=soup.select_one('#article,.cc-article,.article-content,.article-content-main')
        title_node=soup.find('h1')
    elif source_id=='everbright':
        container=soup.select_one('.textall .fontSize,.fontSize')
        title_node=soup.select_one('.article-title,h1')
    elif source_id=='cseg':
        container=soup.select_one('.content-STRO,.content,.show-content,.article')
        title_node=soup.select_one('.STRO h2,h1')
    else:
        container=soup.select_one('.content,.show-content,.article')
        title_node=soup.find('h1')
    if container is None:
        raise SourceUnavailable('公告正文容器缺失，不能将导航或维护页当成正文')
    text=container.get_text('\n',strip=True)
    if len(text)<minimum: raise SourceUnavailable('详情正文为空或结构发生变化')
    text=re.sub(r'\n{3,}','\n\n',text)[:65000]
    # Date only accepted from labelled publication metadata, never random dates in body.
    title=title_node.get_text(' ',strip=True) if title_node else ''
    container.decompose()
    all_text=soup.get_text(' ',strip=True)
    date=re.search(r'(?:信息发布时间|发布时间|发布日期)[：:\s]*('+DATE+')',all_text)
    if not date and source_id in ('bjx','gfcl'):
        meta=soup.select_one('.cc-headline p > span:first-child')
        date=re.search('('+DATE+')',meta.get_text(' ',strip=True)) if meta else None
    return text,title,date.group(1).strip() if date else None


def read_public_detail(client,raw: dict,source: dict) -> tuple[str,str,str | None]:
    sid=source['id']
    if sid in ('bjx','gfcl'):
        rendered=False
        try:
            html=client.get(raw['url'])
            result=clean_text(html,sid,minimum=0)
        except SourceUnavailable:
            html=client.get_rendered(raw['url'])
            rendered=True
            result=clean_text(html,sid,rendered=True,minimum=0)
        body,title,date=result
        raw['acquisition_method']='browser_rendered' if rendered else 'http_html'
        soup=BeautifulSoup(html,'html.parser')
        container=soup.select_one('#article,.cc-article,.article-content,.article-content-main')
        images=container.select('img[src],img[data-src]') if container else []
        if images:
            raw['image_urls']=list(dict.fromkeys(urljoin(raw['url'],img.get('src') or img.get('data-src')) for img in images))[:30]
            raw.update(content_coverage='partial' if body else 'list_only',
                detail_status='正文不完整' if body else '仅列表线索',
                extraction_warning='已保留网页可见文字；公告图片未下载、未识别，图片中的内容请查看来源原文。')
        elif len(body)<120:
            raw.update(content_coverage='partial' if body else 'list_only',
                detail_status='正文不完整' if body else '仅列表线索',
                extraction_warning='当前仅取得较少的网页文字；未取得的信息需核对来源原文。')
        return body,title,date
    html=client.get(raw['url'])
    if sid=='everbright':return read_everbright_detail(client,html,raw)
    try:return clean_text(html,sid)
    except SourceUnavailable:
        if sid!='cseg':raise
        return read_image_detail(client,html,raw,sid)


def read_image_detail(client,html: str,raw: dict,sid: str,rendered: bool=False):
    from .image_ocr import extract_image_article
    body,urls=extract_image_article(client,html,raw['url'],sid)
    if not body:raise SourceUnavailable('详情正文为空或结构发生变化')
    soup=BeautifulSoup(html,'html.parser')
    container=soup.select_one('.content-STRO,.content,.show-content,.article') if sid=='cseg' else soup.select_one('#article,.cc-article,.article-content,.article-content-main')
    if container is None:raise SourceUnavailable('公告正文容器缺失')
    container.clear();container.append(body)
    raw.update(acquisition_method='image_ocr',image_urls=urls,
               extraction_warning='图片OCR识别可能有误，采购要素请核对原图')
    client.ocr_articles=getattr(client,'ocr_articles',0)+1
    return clean_text(str(soup),sid,rendered=rendered)

def cecep_candidates(client: PublicClient,settings: dict) -> list[dict]:
    items={}
    # First-party public search used by the site's own listing JavaScript.
    endpoint='https://new.ebidding.cecep.cn/inteligentsearch/rest/esinteligentsearch/getFullTextDataNew'
    all_words=unique_keywords(['垃圾焚烧','烟气','环保能源','智慧','智能燃烧','APC','DCS']+
                             settings['focus_keywords']+settings.get('search_keywords',[]))
    # The persisted run sequence advances even when schedules repeat at the same hour.
    offset=(int(settings.get('collection_cycle',0))*18)%len(all_words) if len(all_words)>18 else 0
    words=(all_words[offset:]+all_words[:offset])[:18]
    cutoff=(clock.now()-timedelta(days=180)).strftime('%Y-%m-%d')
    for word in words:
        previous=set()
        for offset in (0,40,80):
            payload={'token':'','pn':offset,'rn':40,'wd':'','inc_wd':'','exc_wd':'','terminal':'','fields':'title;zbdw;bdbh;categorynum','cnum':'epointwebbuilder','sort':json.dumps({'webdate':0}),'cl':500,'condition':[{'fieldName':'title','isLike':True,'likeType':0,'equal':word}],'time':None,'isBusiness':'1','noParticiple':'1'}
            try:
                result=client.post(endpoint,payload)
                client.listing_pages=getattr(client,'listing_pages',0)+1
                content=result.get('content',{})
                data=json.loads(content) if isinstance(content,str) else content
                if not isinstance(data,dict) or 'result' not in data: raise SourceUnavailable('公开检索接口结构变化')
                records=data['result'].get('records',[])
                if not isinstance(records,list):raise SourceUnavailable('公开检索记录格式变化')
            except (SourceUnavailable,httpx.HTTPError,ValueError) as exc:
                if not items and offset==0:raise
                listing_warning(client,'检索补页未读到，保留已取得候选：'+str(exc))
                return sorted(items.values(),key=lambda x:x.get('published_at') or '',reverse=True)[:300]
            signatures={row.get('linkurl') for row in records}
            if not records or signatures.issubset(previous):break
            previous.update(signatures)
            for row in records:
                title=row.get('title','');title=title[0] if isinstance(title,list) and title else title
                title=BeautifulSoup(str(title),'html.parser').get_text()
                if not relevant(title,settings):continue
                if (row.get('webdate') or '')[:10] < cutoff:continue
                url=urljoin('https://new.ebidding.cecep.cn/',row.get('linkurl',''))
                if not re.search(r'/cgxx/.*\.html$',url):continue
                items[url]={'title':title,'url':url,'published_at':row.get('webdate'),'published_at_precision':date_precision(row.get('webdate')),'project_code':row.get('bdbh','')}
            if len(records)<40 or all((row.get('webdate') or '')[:10]<cutoff for row in records):break
    return sorted(items.values(),key=lambda x:x.get('published_at') or '',reverse=True)[:300]

def html_candidates(client: PublicClient,source: dict,settings: dict) -> list[dict]:
    urls=[source['url']]
    if source['id']=='bjx':urls += [urljoin(source['url'],f'{i}/') for i in (2,3)]
    if source['id']=='gfcl':urls += [urljoin(source['url'],'2/')]
    def valid_list(target):
        p=urlsplit(target);root=urlsplit(source['url'])
        if p.scheme!=root.scheme or p.netloc!=root.netloc or p.fragment:return False
        if source['id'] in ('bjx','gfcl'):return not p.query and bool(re.fullmatch(r'/zb/(?:[2-5]/)?',p.path))
        if source['id']=='cseg':
            query=parse_qs(p.query)
            return p.path=='/index.php' and query.get('a')==['lists'] and query.get('catid')==['15'] and set(query)<= {'a','catid','page'} and query.get('page',['1'])[0] in ('1','2','3','4','5')
        query=parse_qs(p.query)
        return p.path in ('/cms/category/bulletinNewList3.html','/cms/category/iframe.html','/cms/category/otherBulletinList.html') and query.get('categoryId',[''])[0] in ('2','3','4','5','6') and query.get('page',['1'])[0] in ('1','2','3') and set(query)<= {'dates','categoryId','tabName','page','tenderflag'}
    links=lambda html,url:disclosed_listing_links(html,url,valid_list,turn_page=source['id']=='everbright')
    items={};recognized=False
    for url,html in walk_listing_pages(client,urls,links,max_pages=20 if source['id']=='everbright' else 5):
        soup=BeautifulSoup(html,'html.parser')
        for anchor in soup.select('a[href]'):
            href=urljoin(url,anchor['href'])
            sid=source['id']
            match = re.search(r'/news/\d{8}/\d+\.shtml$',href) if sid in ('bjx','gfcl') else re.search(r'/ebi_\w+/\d{4}-\d{2}-\d{2}/\d+\.html$',href) if sid=='everbright' else re.search(r'a=shows&catid=15&id=\d+',href)
            if not match:continue
            recognized=True
            title=anchor.get('title') or anchor.get_text(' ',strip=True)
            title=re.sub(r'^【[^】]+】\s*','',title)
            title=re.sub(r'\s*20\d{2}-\d{2}-\d{2}\s*$','',title).strip()
            if not relevant(title,settings) or len(title)<10:continue
            surrounding=anchor.parent.get_text(' ',strip=True)
            date=re.search(DATE,surrounding)
            if not date: date=re.search(DATE,href)
            published=date.group(0).strip() if date else None
            if not published:
                m=re.search(r'/news/(20\d{2})(\d{2})(\d{2})/',href)
                if m:published='-'.join(m.groups())
            items[href]={'title':title,'url':href,'published_at':published,'published_at_precision':date_precision(published)}
    if not recognized: raise SourceUnavailable('未识别到公告列表，页面结构可能已变化')
    # PDF notices require both the detail page and attachment request. Keep
    # space for the bounded list scan and robots checks inside the 500-call cap.
    return list(items.values())[:230 if source['id']=='everbright' else 250]

def read_pdf_attachment(client: PublicClient, html: str, raw: dict | None=None) -> str:
    from .pdf_content import extract_pdf,PDFContentError
    soup=BeautifulSoup(html,'html.parser')
    iframe=soup.select_one('iframe#pdfContainer[src]')
    if iframe is None:raise SourceUnavailable('公告正文PDF未公开或页面结构变化')
    target=parse_qs(urlsplit(iframe['src']).query).get('file',[''])[0]
    if not target or not client.allowed(target):raise SourceUnavailable('PDF正文不可公开读取')
    method=getattr(client,'get_pdf_bytes',None)
    data=method(target) if callable(method) else client._request(target).content
    if not data.startswith(b'%PDF'):raise SourceUnavailable('PDF正文要求登录或返回非PDF内容')
    try:
        text,metadata=extract_pdf(data,max_pages=30)
    except PDFContentError as exc:
        raise SourceUnavailable('PDF文件无法解析，保留列表线索，需人工阅读') from exc
    if raw is not None:
        raw.update(metadata,pdf_source_url=target,
                   pdf_body_empty=not bool(text.strip()),acquisition_method='pdf_ocr' if metadata['pdf_ocr_pages'] else 'pdf_text')
    return text[:65000]

def read_everbright_detail(client: PublicClient, html: str, raw: dict | None=None) -> tuple[str,str,str | None]:
    soup=BeautifulSoup(html,'html.parser')
    if soup.select_one('iframe#pdfContainer[src]') is None:
        return clean_text(html,'everbright')
    body=read_pdf_attachment(client,html,raw) if raw is not None else read_pdf_attachment(client,html)
    heading=soup.select_one('h3')
    title=re.sub(r'^\[[^\]]+\]\s*','',heading.get_text(' ',strip=True)) if heading else ''
    return body,title,None

DETAIL_METADATA_FIELDS=('acquisition_method','image_urls','extraction_warning','content_coverage',
    'pdf_pages_total','pdf_pages_read','pdf_ocr_pages','pdf_failed_pages','pdf_truncated',
    'pdf_ocr_page_numbers','pdf_read_page_numbers','pdf_source_url','pdf_pages_attempted','pdf_page_errors',
    'pdf_text_truncated','pdf_timed_out','pdf_page_limit','pdf_input_limit_bytes','pdf_timeout_seconds',
    'pdf_max_render_pixels','pdf_text_limit_chars','pdf_body_empty')


def _can_reuse_detail(cached: dict, list_title: str, listing_source_id: str) -> bool:
    """Only verified, recent body evidence with this channel's list key is cached."""
    body=cached.get('content')
    complete=cached.get('detail_status')=='正文可读' and isinstance(body,str) and len(body.strip())>=120
    partial=cached.get('detail_status')=='正文不完整' and cached.get('content_coverage')=='partial' and isinstance(body,str) and bool(body.strip()) and not cached.get('pdf_body_empty')
    if not (complete or partial):
        return False
    if cached.get('publication_warning') and not cached.get('detail_published_at'):
        return False
    try:
        fetched=datetime.fromisoformat(cached.get('detail_fetched_at') or '')
        now=clock.now()
        if fetched.tzinfo is None or not now-timedelta(hours=6)<fetched<=now:
            return False
    except (ValueError,TypeError):
        return False
    if 'list_title' in cached or 'listing_source_id' in cached:
        return cached.get('listing_source_id')==listing_source_id and cached.get('list_title')==list_title
    # Earlier records did not store a separate list key. Do not use another
    # channel's final title as proof that this channel's listing is unchanged.
    return cached.get('source_id')==listing_source_id and cached.get('title')==list_title


def collect_source(source: dict,companies: list[dict],settings: dict) -> dict:
    started=time.monotonic()
    if source.get('custom'):
        from .monitoring import collect_custom
        result=collect_custom(source,companies,settings)
        result['elapsed_seconds']=round(time.monotonic()-started,3)
        return result
    client=PublicClient();counts={'source':source['name'],'found':0,'inserted':0,'updated':0,'unchanged':0,'restricted':0,'partial':0,'list_only':0,'errors':[],'issues':[],'coverage_notes':[],
                               'elapsed_seconds':0.0,'http_requests':0,'cache_hits':0,'detail_reads':0,'listing_pages':0,'listing_warnings':[]}
    try:
        adapter=source_adapter(source['id'])
        if source['id'] not in BUILTIN_SOURCE_IDS:raise SourceUnavailable('该来源尚未完成采集规则适配，请先配置并测试')
        candidates=adapter.get_candidates(client,source,settings) if adapter else cecep_candidates(client,settings) if source['id']=='cecep' else html_candidates(client,source,settings)
        counts['found']=len(candidates)
        counts['listing_pages']=getattr(client,'listing_pages',0)
        counts['listing_warnings']=getattr(client,'listing_warnings',[])[:3]
        counts['errors'].extend(counts['listing_warnings'])
        restricted_reason='';consecutive_failures=0
        for raw in candidates:
            # Cache successfully read bodies for 6h. Lists are always refreshed.
            # Keep the actual list title/channel independent of the verified
            # final title and of the first source retained as provenance below.
            raw.update(list_title=raw['title'],listing_source_id=source['id'])
            fresh_publication=False
            with db.connect() as c:
                old=c.execute('SELECT * FROM opportunities WHERE url=?',(raw['url'],)).fetchone()
            raw.update(source_id=source['id'],source_name=source['name'],source_kind=source['kind'])
            cached=json.loads(old['payload']) if old else {}
            observed=sorted(set(cached.get('observed_source_ids',[])+[cached.get('source_id',source['id']),source['id']]))
            if len(observed)>1:raw['observed_source_ids']=observed
            # Preserve first provenance when duplicate URL is reached via another channel.
            if cached:
                for key in ('source_id','source_name','source_kind'):raw[key]=cached[key]
                # A fresh list fetch must not temporarily resurrect a superseded bid.
                # Keep its verified lifecycle link while the new run is still in progress.
                if cached.get('superseded_by'):raw['superseded_by']=cached['superseded_by']
                # A missing/changed list date cannot resolve a known conflict.
                # Only successfully refreshed publication metadata clears it below.
                if cached.get('publication_warning'):
                    raw['publication_warning']=cached['publication_warning']
            # Reverify legacy date warnings once; older records did not store the page date.
            if _can_reuse_detail(cached,raw['list_title'],raw['listing_source_id']):
                counts['cache_hits']+=1
                raw.update(content=cached['content'],title=cached['title'],detail_status=cached.get('detail_status','正文可读'),detail_fetched_at=cached['detail_fetched_at'])
                if cached.get('detail_published_at'):raw['detail_published_at']=cached['detail_published_at']
                for key in DETAIL_METADATA_FIELDS:
                    if key in cached:raw[key]=cached[key]
            else:
                try:
                    if restricted_reason:raise SourceUnavailable(restricted_reason)
                    counts['detail_reads']+=1
                    if adapter:
                        body,title,date=adapter.read_detail(client,raw,source)
                    else:
                        body,title,date=read_public_detail(client,raw,source)
                    if (not body or raw.get('pdf_body_empty')) and cached.get('content'):
                        attempted=raw.get('image_urls')
                        attempted_pdf={key:raw[key] for key in DETAIL_METADATA_FIELDS if key in raw and (key.startswith('pdf_') or key in ('extraction_warning','content_coverage'))}
                        current_warning=raw.get('extraction_warning')
                        for key in DETAIL_METADATA_FIELDS:
                            raw.pop(key,None)
                            if key in cached:raw[key]=cached[key]
                        raw.update(content=cached['content'],detail_status='详情暂不可读；保留历史正文',
                            detail_fetched_at=cached.get('detail_fetched_at'),content_coverage='partial',
                            extraction_warning='；'.join(filter(None,[
                                '本轮未取得新的详情正文，保留历史正文；最新内容需核对来源原文。',current_warning])))
                        if attempted:raw['attempted_image_urls']=attempted
                        if any(key.startswith('pdf_') for key in attempted_pdf):raw['attempted_pdf_metadata']=attempted_pdf
                        if cached.get('detail_published_at'):raw['detail_published_at']=cached['detail_published_at']
                    else:
                        raw.update(content=body,detail_status=raw.get('detail_status') or ('正文可读' if body else '仅列表线索'),
                            detail_fetched_at=db.now() if body else None)
                    consecutive_failures=0
                    if title and len(title)>12:raw['title']=title
                    if date:
                        raw['detail_published_at']=date
                        fresh_publication=bool(parse_date(date))
                    if date and not raw.get('published_at'):
                        raw.update(published_at=date,published_at_precision=date_precision(date))
                except (SourceUnavailable,httpx.HTTPError,ValueError) as exc:
                    from .source_health import public_issues
                    known={issue['code'] for issue in counts['issues']}
                    counts['issues'].extend(issue for issue in public_issues({'last_error':str(exc)}) if issue['code'] not in known)
                    # A scanned/long attachment is an item-level limitation, not
                    # evidence that subsequent public documents cannot be read.
                    individual_limit=bool(re.search('扫描PDF|PDF超过30页|PDF文件无法解析|OCR|图片正文',str(exc)))
                    consecutive_failures=0 if individual_limit else consecutive_failures+1
                    if re.search('验证|安全认证|访问异常|限制访问|robots规则不允许|渲染页数上限|浏览器请求数量上限|浏览器组件未就绪',str(exc)):
                        restricted_reason=str(exc)[:140]
                    elif consecutive_failures>=3:
                        restricted_reason='本站详情连续读取失败，本轮保留列表；下轮再检查'
                    raw.update(content=cached.get('content',''),detail_status='详情暂不可读；保留历史正文' if cached.get('content') else '仅列表线索',detail_fetched_at=cached.get('detail_fetched_at'))
                    if cached.get('content'):
                        for key in DETAIL_METADATA_FIELDS:
                            if key in cached:raw[key]=cached[key]
                        if getattr(exc,'image_urls',None):raw['attempted_image_urls']=exc.image_urls
                    elif getattr(exc,'image_urls',None):
                        raw.update(image_urls=exc.image_urls,acquisition_method='image_ocr',extraction_warning='图片OCR未完成，请核对原图')
                    if cached.get('detail_published_at'):raw['detail_published_at']=cached['detail_published_at']
                    raw['content_coverage']='partial' if raw.get('content') else 'list_only'
                    counts['restricted']+=1
                    if len(counts['errors'])<3 and str(exc)[:140] not in counts['errors']:counts['errors'].append(str(exc)[:140])
            coverage=raw.get('content_coverage') or ('complete' if raw.get('content') else 'list_only')
            if coverage in ('partial','list_only'):
                raw['content_coverage']=coverage
                counts[coverage]+=1
                warning=raw.get('extraction_warning') or ('正文不完整，未取得的信息需核对来源原文。' if coverage=='partial' else '仅保留公开列表信息，正文待核实。')
                if warning not in counts['coverage_notes'] and len(counts['coverage_notes'])<3:counts['coverage_notes'].append(warning)
            # Same-day detail metadata may supply minutes omitted by the list.
            # Reuse that source text on cached runs so precision does not oscillate.
            detail_date=raw.get('detail_published_at')
            listed=raw.get('published_at')
            # A list can change its date while the unchanged title still permits
            # a body cache hit. Compare both dates on every path, including a
            # failed refresh retaining historical body metadata. A prior warning
            # only clears after newly read publication evidence agrees.
            detail_publication=parse_date(detail_date or '')
            list_publication=parse_date(listed or '')
            if detail_publication and list_publication:
                if detail_publication[:10]!=list_publication[:10]:
                    raw['publication_warning']='列表与页面发布日期不一致，保留列表日期，需核实原公告'
                elif fresh_publication:
                    raw.pop('publication_warning',None)
            if not listed and detail_date:
                raw.update(published_at=detail_date,published_at_precision=date_precision(detail_date))
            elif listed and detail_date and date_precision(detail_date)=='minute' and raw.get('published_at_precision',date_precision(listed))!='minute' and (parse_date(detail_date) or '')[:10]==(parse_date(listed) or '')[:10]:
                raw.update(published_at=detail_date,published_at_precision='minute')
            item=analyze(raw,companies,settings)
            counts[db.upsert(item)]+=1
        state='部分可用' if counts['restricted'] or counts['errors'] or counts['partial'] or counts['list_only'] else '正常'
        errors=list(dict.fromkeys([issue['label'] for issue in counts['issues']]+counts['errors']+counts['coverage_notes']))
        with db.connect() as c:
            c.execute('UPDATE sources SET status=?,last_success=?,last_error=?,last_count=? WHERE id=?',(state,db.now(),'；'.join(errors) or None,counts['found'],source['id']))
    except Exception as exc:
        LOG.warning('source failed: %s (%s)',source['id'],type(exc).__name__)
        error=str(exc)[:180] if isinstance(exc,SourceUnavailable) else f'采集失败：{type(exc).__name__}，请检查来源可用性'
        counts['errors'].append(error)
        with db.connect() as c:c.execute('UPDATE sources SET status=?,last_error=? WHERE id=?',('异常',error,source['id']))
    finally:
        try:client.close()
        finally:
            counts['http_requests']=getattr(client,'http_attempts',getattr(client,'requests',0))
            counts['browser_reads']=getattr(client,'browser_reads',0)
            counts['browser_pages']=getattr(getattr(client,'browser',None),'pages',0)
            counts['browser_requests']=getattr(getattr(client,'browser',None),'network_requests',0)
            counts['ocr_articles']=getattr(client,'ocr_articles',0)
            counts['elapsed_seconds']=round(time.monotonic()-started,3)
    return counts

def run_collection(run_id: int) -> None:
    clock.ensure_writable()
    from concurrent.futures import ThreadPoolExecutor
    companies=db.all_companies();settings={**db.settings(),'collection_cycle':run_id-1}
    enabled=[x for x in source_rows() if x['enabled']]
    details=[]
    try:
        # Keep the two BJX channels sequential to respect a shared publisher's rate limit.
        from . import workbench
        for source in enabled:
            workbench.progress(run_id,"采集网站",len(details),len(enabled),source["name"])
            detail=collect_source(source,companies,collection_preferences(settings,source['id']));details.append(detail)
            with db.connect() as c:
                c.execute('UPDATE runs SET found=?,inserted=?,updated=?,details=? WHERE id=?',(sum(x['found'] for x in details),sum(x['inserted'] for x in details),sum(x['updated'] for x in details),db.dumps(details),run_id))
        failed=sum(bool(x['errors']) for x in details)
        db.refresh_expired()
        db.reconcile_lifecycle()
        status='完成' if not failed else '部分完成' if any(x['found'] for x in details) else '失败'
        with db.connect() as c:c.execute('UPDATE runs SET status=?,finished_at=? WHERE id=?',(status,db.now(),run_id))
    except Exception:
        LOG.exception('collection run failed')
        with db.connect() as c:c.execute('UPDATE runs SET status=?,finished_at=?,error=? WHERE id=?',('失败',db.now(),'本轮采集意外中断，下轮重试',run_id))
