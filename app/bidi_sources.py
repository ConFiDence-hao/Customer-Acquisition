"""Bounded browser-rendered public Bidi columns; no login or hidden member data.

The public list row markup was observed, but the current anonymous session
ultimately reaches an IP security check. Such a page is an unavailable source,
never proof of a successful collection. Detail parsing stays conservative until
the site returns a visible article; navigation and excerpts cannot replace it.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .analysis import DATE, date_precision, parse_date

SOURCE_IDS=frozenset({'bidi'})
EXTRA_HOSTS=frozenset({'www.bidizhaobiao.com'})
_ORIGIN='https://www.bidizhaobiao.com'
CANONICAL_URLS={'bidi':_ORIGIN+'/zbyg/'}
_ROOTS=(_ORIGIN+'/zbyg/',_ORIGIN+'/zbgg',_ORIGIN+'/cgyx')
_CHANNELS={'zbyg':'招标预告','zbgg':'招标公告','cgyx':'采购意向'}
COVERAGE_NOTES={'bidi':'普通浏览器渲染公开招标预告、招标公告和采购意向根栏目；每栏最多3页、合计250条，只跟随公开且允许的分页链接。当前匿名核验遇IP安全认证，不能保证读取成功；登录或会员受限正文保留列表线索，不处理验证码。'}
_DETAIL=re.compile(r'/info-[1-9]\d*\.html')
_GATE=re.compile(r'登录后(?:即可)?查看|登录后阅读|请先登录|请登录后|注册后(?:即可)?查看|开通会员|升级会员|会员(?:专享|可见)|VIP可见|完整(?:公告|内容).{0,12}(?:登录|会员)|(?:登录|会员).{0,12}完整(?:公告|内容)',re.I)
_SECURITY=re.compile(r'IP存在访问异常|请进行安全认证|人机验证|访问验证|请完成.{0,10}验证')

def _origin_allowed(url):
    try:
        p=urlsplit(url)
        return (p.scheme=='https' and p.hostname=='www.bidizhaobiao.com'
                and p.port in (None,443) and p.username is None and p.password is None
                and not p.fragment)
    except (TypeError,ValueError):return False

def _detail_allowed(url):
    return _origin_allowed(url) and not urlsplit(url).query and _DETAIL.fullmatch(urlsplit(url).path) is not None

def _list_allowed(url):
    if not _origin_allowed(url):return False
    p=urlsplit(url);query=parse_qs(p.query,keep_blank_values=True)
    # The published robots file disallows /zbyg/*/ and /zbgg/*/. Do not
    # follow nested routes merely because RobotFileParser treats '*' literally.
    return (p.path.strip('/') in _CHANNELS and p.path.count('/')<=2
            and set(query)<={'page'} and ('page' not in query or
                 len(query['page'])==1 and re.fullmatch(r'[1-9]\d{0,3}',query['page'][0]) is not None))

def _visible_soup(html):
    from .collectors import SourceUnavailable
    if not isinstance(html,str) or len(html)>4_000_000:
        raise SourceUnavailable('比地渲染页面为空或超过公开读取限制')
    soup=BeautifulSoup(html,'html.parser')
    for node in list(soup.select('script,style,noscript,[hidden],[aria-hidden="true"]')):
        if node.parent is not None:node.decompose()
    for node in list(soup.select('[style]')):
        if node.parent is None:continue
        style=node.get('style','')
        if re.search(r'(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\s*(?:!important)?\s*(?:;|$)',style,re.I):node.decompose()
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    text=soup.get_text(' ',strip=True)
    if title in ('安全认证','访问验证','人机验证') or _SECURITY.search(text):
        raise SourceUnavailable('比地当前要求安全认证或出现访问异常，保留已取得公开线索')
    if re.fullmatch(r'.{0,15}(?:会员登录|用户登录|账号登录|登录验证).{0,10}',title) or re.search('403 Forbidden|Access Denied',title,re.I):
        raise SourceUnavailable('比地页面要求登录或限制访问，未取得公开正文')
    return soup

def _render(client,url):
    from .collectors import SourceUnavailable
    method=getattr(client,'get_rendered',None)
    if not callable(method):raise SourceUnavailable('比地公开页面需要普通浏览器渲染，当前渲染器不可用')
    return _visible_soup(method(url))

def _next_page(soup,current,client):
    from .collectors import listing_warning
    page=int(parse_qs(urlsplit(current).query).get('page',['1'])[0])
    for anchor in soup.select('a[href]'):
        label=anchor.get_text(' ',strip=True)
        if label not in ('下一页','下页','>','»',str(page+1)):continue
        target=urljoin(current,anchor['href'])
        if _list_allowed(target) and urlsplit(target).path.strip('/')==urlsplit(current).path.strip('/'):
            return target
        if anchor['href'].strip() and not anchor['href'].startswith(('javascript:','#')):
            listing_warning(client,'比地下一页路径不属于已允许的公开根栏目，未继续读取')
    return None

def get_candidates(client,source,settings):
    from .collectors import SourceUnavailable,relevant,listing_warning
    if source.get('id') not in SOURCE_IDS or source.get('url') not in (_ORIGIN,_ORIGIN+'/',*_ROOTS):
        raise SourceUnavailable('比地来源地址不属于已核实公开根栏目')
    items={};public_pages=0
    for root in _ROOTS:
        current=root;visited=set();observed=set()
        for _ in range(3):
            if current in visited or not _list_allowed(current):break
            visited.add(current)
            try:
                soup=_render(client,current)
                rows=soup.select('.ul > .li')
                visible=[node for node in rows if node.select_one('.cp-title a[href]')]
                if not visible:
                    empty=soup.select_one('.ul')
                    if empty is None or not re.search('暂无数据|暂无信息|没有找到|未找到相关',empty.get_text(' ',strip=True)):
                        raise SourceUnavailable('比地未取得公开可见列表，可能仍是隐藏模板或栏目结构变化')
                public_pages+=1;client.listing_pages=getattr(client,'listing_pages',0)+1
            except (SourceUnavailable,httpx.HTTPError,ValueError) as exc:
                if not public_pages:raise
                listing_warning(client,'比地补充栏目或分页未读到，保留已取得候选：'+str(exc))
                if re.search('安全认证|访问异常|限制访问|访问验证|人机验证|渲染.*上限',str(exc)):
                    return list(items.values())[:250]
                break
            page_urls=set()
            for node in visible:
                for anchor in node.select('.cp-title a[href]'):
                    target=urljoin(current,anchor['href'])
                    if not _detail_allowed(target):continue
                    page_urls.add(target)
                    title=re.sub(r'\s+',' ',anchor.get_text(' ',strip=True)).strip()
                    if len(title)<6 or not relevant(title,settings):continue
                    stamp=node.select_one('.year_date');value=stamp.get_text(' ',strip=True) if stamp else ''
                    match=re.search(DATE,value);published=match.group(0) if match and parse_date(match.group(0)) else None
                    items.setdefault(target,{'url':target,'title':title[:500],'published_at':published,
                        'published_at_precision':date_precision(published),
                        'listing_channel':_CHANNELS[urlsplit(root).path.strip('/')],
                        'public_scope':'浏览器公开可见列表与正文；会员隐藏字段不采集'})
                    break
            if page_urls and not page_urls-observed:break
            observed.update(page_urls)
            if len(items)>=250:return list(items.values())[:250]
            next_url=_next_page(soup,current,client)
            if next_url is None:break
            current=next_url
    return list(items.values())[:250]

def read_detail(client,raw,source):
    from .collectors import SourceUnavailable
    if source.get('id') not in SOURCE_IDS or not _detail_allowed(raw.get('url')):
        raise SourceUnavailable('比地详情链接超出已核实公开公告路径')
    soup=_render(client,raw['url'])
    for node in list(soup.select('nav,header,footer,form,button,input,aside')):
        if node.parent is not None:node.decompose()
    # No whole-body fallback: only an explicit visible article can be evidence.
    # Additional site-specific containers require a successful public DOM check.
    article=soup.select_one('article,#article,.article-content,.notice-content,.detail-content')
    heading=soup.select_one('h1')
    if article is None or heading is None:
        raise SourceUnavailable('比地公开正文容器或标题尚未取得，可能需登录或规则仍待核验；保留列表线索')
    body=re.sub(r'\n{3,}','\n\n',article.get_text('\n',strip=True)).strip()
    if _GATE.search(body):raise SourceUnavailable('比地该公告完整正文要求登录或会员权限，保留公开列表线索')
    if len(body)<120:raise SourceUnavailable('比地公开正文文字不足，可能仅简介或图片，保留列表线索')
    title=heading.get_text(' ',strip=True)
    if len(title)<6:raise SourceUnavailable('比地公开公告标题不足，未将其他页面当成正文')
    published=None
    for meta in soup.select('.article-meta,.info-meta,.detail-meta,.notice-meta,.detail-time,time'):
        value=meta.get('datetime') or meta.get_text(' ',strip=True)
        match=re.search(DATE,value)
        if match and parse_date(match.group(0)) and (meta.name=='time' or re.search('发布时间|发布日期|发布于',value)):
            published=match.group(0);break
    return body[:65000],title[:500],published
