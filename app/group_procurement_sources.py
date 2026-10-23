"""Public notice boards of incineration operators' own procurement platforms.

Both boards are static CMS pages readable without login. Only the disclosed
notice columns are read; supplier login, registration and quotation systems on
the same hosts are never touched.

- 绿色动力 (dgbid.dynagreen.com.cn:8000): linked as "采购平台" from the group's
  official site. Its /robots.txt is not a robots file: it 301-redirects to the
  supplier front end on plain HTTP, which answers 400 (verified 2026-10-16), so the
  origin is recorded as publishing no robots rules.
- 深圳能源 (zb.sec.com.cn): linked as "招标平台" from www.sec.com.cn; it covers
  深能环保 incineration plants. /robots.txt returns 404.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .analysis import DATE, date_precision, parse_date

DYNAGREEN = 'https://dgbid.dynagreen.com.cn:8000'
SZENERGY = 'https://zb.sec.com.cn'
SOURCE_IDS = frozenset({'dynagreen', 'szenergy'})
EXTRA_HOSTS = frozenset({'dgbid.dynagreen.com.cn', 'zb.sec.com.cn'})
EXTRA_PORTS = {'dgbid.dynagreen.com.cn': 8000}
ROBOTS_ABSENT = frozenset({DYNAGREEN})
CANONICAL_URLS = {'dynagreen': DYNAGREEN + '/lsdlcms/', 'szenergy': SZENERGY + '/'}
COVERAGE_NOTES = {
    'dynagreen': '集团数字化采购招标平台公开栏目：采购、招标、资审、变更、中标候选人与结果公告各读取最新一页（约10条），正文读取公开网页；不登录供应商系统。',
    'szenergy': '深圳能源电子招标投标平台公开公告汇总最多前5页及预中标公告前2页，候选最多120条；含深能环保下属焚烧厂项目，按业务关键词筛选；不登录采购门户。',
}

# Dynagreen columns: 采购/资审/变更/结果 (非招标) and 招标/资审/变更/中标候选人/结果 (招标).
_DYNAGREEN_COLUMNS = ('cmsfcaigougg', 'cmszzhaobiaogg', 'cmsfzishengg', 'cmszzishengg', 'cmsfbiangenggg',
                      'cmsfbiangengg', 'cmszzhongbiaohxrgs', 'cmsfjieguogg', 'cmszjieguogg')
_DYNAGREEN_NOTICE = re.compile(r'^/lsdlcms/(%s)/[0-9a-f]{32}\.html$' % '|'.join(_DYNAGREEN_COLUMNS))
_SZENERGY_LISTS = ('zbggs', 'yzbgg')
_SZENERGY_LIST = re.compile(r'^/(zbggs|yzbgg)/index(?:_(\d+))?\.jhtml$')
_SZENERGY_NOTICE = re.compile(r'^/(?!tongzhi/|banshi/|fagui/|xinyonggl/)[a-z]+/\d+\.jhtml$')
_SZENERGY_PAGES = {'zbggs': 5, 'yzbgg': 2}
_PROTECTION = re.compile(r'CF_APP_WAF|aliyunCaptcha|captcha-container|访问验证|人机验证', re.I)


def _origin(url: str) -> str | None:
    try:
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.username is not None or parts.password is not None or parts.fragment:
            return None
        if parts.hostname == 'dgbid.dynagreen.com.cn' and parts.port == 8000:
            return DYNAGREEN
        if parts.hostname == 'zb.sec.com.cn' and parts.port in (None, 443):
            return SZENERGY
    except ValueError:
        return None
    return None


def _notice(source_id: str, url: str) -> bool:
    origin = _origin(url)
    path = urlsplit(url).path if origin else ''
    if source_id == 'dynagreen':
        return origin == DYNAGREEN and not urlsplit(url).query and _DYNAGREEN_NOTICE.fullmatch(path) is not None
    return origin == SZENERGY and not urlsplit(url).query and _SZENERGY_NOTICE.fullmatch(path) is not None


def _date(text: str) -> str | None:
    match = re.search(DATE, text or '')
    value = match.group(0).strip() if match else None
    return value if value and parse_date(value) else None


def _szenergy_next(html: str, url: str):
    """Follow the disclosed 下一页 link only, within each column's page window."""
    current = _SZENERGY_LIST.fullmatch(urlsplit(url).path)
    if not current:
        return []
    column, page = current.group(1), int(current.group(2) or 1)
    for anchor in BeautifulSoup(html, 'html.parser').select('a[href]'):
        target = urljoin(url, anchor['href'])
        found = _origin(target) == SZENERGY and _SZENERGY_LIST.fullmatch(urlsplit(target).path)
        if (found and anchor.get_text(strip=True) == '下一页' and found.group(1) == column
                and int(found.group(2) or 1) == page + 1 <= _SZENERGY_PAGES[column]):
            return [target]
    return []


def get_candidates(client, source: dict, settings: dict) -> list[dict]:
    from .collectors import SourceUnavailable, relevant, walk_listing_pages

    sid = source.get('id')
    if sid not in SOURCE_IDS:
        raise SourceUnavailable('集团采购平台尚无已核验的公开公告规则')
    if sid == 'dynagreen':
        roots = [f'{DYNAGREEN}/lsdlcms/{column}/index.html' for column in _DYNAGREEN_COLUMNS]
        links = lambda html, url: []
        limit = len(roots)
    else:
        roots = [f'{SZENERGY}/{column}/index.jhtml' for column in _SZENERGY_LISTS]
        links = _szenergy_next
        limit = sum(_SZENERGY_PAGES.values())
    items, observed = {}, 0
    for current, html in walk_listing_pages(client, roots, links, max_pages=limit):
        for anchor in BeautifulSoup(html, 'html.parser').select('li a[href]'):
            url = urlunsplit(urlsplit(urljoin(current, anchor['href']))._replace(fragment=''))
            if not _notice(sid, url):
                continue
            observed += 1
            # Titles in the list are truncated with "..."; the title attribute or detail page has the full text.
            title = re.sub(r'\s+', ' ', anchor.get('title') or anchor.get_text(' ', strip=True)).strip()
            if len(title) < 8 or not relevant(title, settings):
                continue
            meta = anchor.find_next_sibling('p')
            publication = _date(meta.get_text(' ', strip=True)) if meta else None
            items.setdefault(url, {'title': title, 'url': url, 'published_at': publication,
                                   'published_at_precision': date_precision(publication)})
    if not observed:
        raise SourceUnavailable('未发现公开采购公告链接，列表可能为空或结构发生变化')
    return sorted(items.values(), key=lambda item: item.get('published_at') or '', reverse=True)[:120]


def read_detail(client, raw: dict, source: dict) -> tuple[str, str, str | None]:
    from .collectors import SourceUnavailable

    sid = source.get('id')
    if sid not in SOURCE_IDS or not _notice(sid, raw.get('url', '')):
        raise SourceUnavailable('采购公告地址超出已核验的公开栏目')
    html = client.get(raw['url'])
    if _PROTECTION.search(html):
        raise SourceUnavailable('采购平台要求访问验证，本轮停止读取')
    soup = BeautifulSoup(html, 'html.parser')
    if sid == 'dynagreen':
        heading, stamp, container = soup.select_one('h2.con_tit'), soup.select_one('.s_date'), soup.select_one('#TDContent')
    else:
        heading = soup.select_one('h1')
        stamp = heading.find_next_sibling('div') if heading else None
        container = soup.select_one('.Content')
    if heading is None or container is None:
        raise SourceUnavailable('公告标题或正文容器缺失，不能以列表标题充当正文')
    for node in container.select('script,style,noscript'):
        node.decompose()
    title = re.sub(r'\s+', ' ', heading.get_text(' ', strip=True))
    publication = _date(stamp.get_text(' ', strip=True)) if stamp else None
    body = re.sub(r'[ \t\xa0]+', ' ', container.get_text('\n', strip=True))
    body = re.sub(r'\n{3,}', '\n\n', body).strip()
    if len(body) < 60:
        raise SourceUnavailable('公告网页正文不足，附件须在原站查阅')
    return body[:65000], title, publication
