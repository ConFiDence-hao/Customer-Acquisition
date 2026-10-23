"""Verified public invitation notices; membership-only fields are not collected."""
from __future__ import annotations
import re
from urllib.parse import urljoin, urlsplit
import httpx
from bs4 import BeautifulSoup

SOURCE_IDS = frozenset({'bidcenter'})
EXTRA_HOSTS = frozenset()
CANONICAL_URLS = {'bidcenter': 'https://www.bidcenter.com.cn/yqzb/index.html'}
COVERAGE_NOTES = {'bidcenter': '公开邀请招标栏目最多8页、200条；读取项目概况与报名须知，会员联系方式及其他受限栏目不采集。'}
_LIST = re.compile(r'^/yqzb/index(?:-[1-9]\d*)?\.html$')
_DETAIL = re.compile(r'^/yqzb/detailyqzb-\d+\.html$')

def _verified(url,pattern):
    try:
        p=urlsplit(url)
        return (p.scheme=='https' and p.hostname=='www.bidcenter.com.cn'
                and p.port in (None,443) and p.username is None and p.password is None
                and not p.query and not p.fragment and pattern.fullmatch(p.path) is not None)
    except (TypeError,ValueError): return False

def _soup(text):
    from .collectors import SourceUnavailable
    if re.search(r'人机验证|访问验证|captcha-container|CF_APP_WAF',text,re.I):
        raise SourceUnavailable('采招该页面要求访问验证')
    return BeautifulSoup(text,'html.parser')

def get_candidates(client,source,settings):
    from .analysis import date_precision
    from .collectors import SourceUnavailable,relevant,listing_warning
    if source['id'] not in SOURCE_IDS: raise SourceUnavailable('未知公开邀请招标来源')
    current=CANONICAL_URLS[source['id']];items={};visited=set();observed=set()
    for _ in range(8):
        if current in visited or not _verified(current,_LIST): break
        visited.add(current)
        try:
            soup=_soup(client.get(current))
            blocks=soup.select('.main-item')
            if not blocks: raise SourceUnavailable('采招公开邀请栏目结构变化，未取得公告列表')
        except (SourceUnavailable,httpx.HTTPError) as exc:
            if len(visited)>1:
                listing_warning(client,'公开邀请栏目补页未读到，保留已取得候选：'+str(exc))
                break
            raise
        client.listing_pages=getattr(client,'listing_pages',0)+1
        page_urls=set()
        for node in blocks:
            # The first notice link is its title; later links contain an excerpt or signup.
            anchors=node.select('a[href]')
            for anchor in anchors:
                url=urljoin(current,anchor.get('href',''))
                if not _verified(url,_DETAIL): continue
                page_urls.add(url)
                title=re.sub(r'\s+',' ',anchor.get_text(' ',strip=True)).strip()
                if not relevant(title,settings): break
                stamp=node.select_one('.item-bott')
                date=re.search(r'发布时间\s*[：:]\s*(20\d{2}-\d{2}-\d{2})',stamp.get_text(' ',strip=True) if stamp else '')
                value=date.group(1) if date else None
                items.setdefault(url,{'url':url,'title':title,'published_at':value,
                    'published_at_precision':date_precision(value),
                    'public_scope':'公开邀请招标项目概况和报名须知'})
                break
        if not page_urls-observed: break
        observed.update(page_urls)
        if len(items)>=200: break
        next_link=next((a for a in soup.select('a[href]') if re.fullmatch(r'\s*下一页\s*',a.get_text())),None)
        if next_link is None: break
        current=urljoin(current,next_link.get('href',''))
    return list(items.values())[:200]

def read_detail(client,raw,source):
    from .collectors import SourceUnavailable
    if source['id'] not in SOURCE_IDS or not _verified(raw.get('url'),_DETAIL):
        raise SourceUnavailable('详情链接超出已核实公开邀请栏目')
    soup=_soup(client.get(raw['url']));node=soup.select_one('.xiangmu-sin')
    if node is None: raise SourceUnavailable('公开项目概况容器缺失，保留列表线索')
    for child in node.select('script,style,form'): child.decompose()
    # Remove the member-only contact rows, not the surrounding public scope.
    for field in list(node.select('tr,p,td,div')):
        if getattr(field,'parent',None) is None: continue
        value=''.join(field.stripped_strings)
        if len(value)<200 and re.search(r'仅向.{0,60}会员显示',value): field.decompose()
    for text in list(node.find_all(string=re.compile(r'仅向.*会员显示'))):
        if getattr(text,'parent',None) is None: continue
        target=text.find_parent('tr') or text.find_parent('p') or text.parent
        if target is not node: target.decompose()
    body=re.sub(r'\n{3,}','\n\n',node.get_text('\n',strip=True)).strip()
    if len(body)<120: raise SourceUnavailable('公开项目说明不足，完整内容可能受权限限制')
    header=soup.select_one('.sin-left-top');heading=header.select_one('h1,h2,h3') if header else None
    title=heading.get_text(' ',strip=True) if heading else raw.get('title','')
    match=re.search(r'发布时间\s*[：:]\s*(20\d{2}-\d{2}-\d{2})',header.get_text(' ',strip=True) if header else '')
    return body[:65000],title,match.group(1) if match else None
