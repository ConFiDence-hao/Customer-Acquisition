"""Small, bounded adapters for verified public government notice columns.

These read the same HTML or unauthenticated form endpoint as each official
website. A readable site with no relevant notices returns an empty list; a
missing column structure is an error. Supplier files requiring login are not read.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

SOURCE_IDS = frozenset({'ggzy', 'ccgp', 'hubei', 'datong'})
EXTRA_HOSTS = frozenset()

_ORIGINS = {
    'ggzy': 'https://www.ggzy.gov.cn',
    'ccgp': 'https://www.ccgp.gov.cn',
    'hubei': 'https://www.hbggzyfwpt.cn',
    'datong': 'http://ggzyjy.dt.gov.cn',
}
_GUID = re.compile(r'[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}', re.I)

_PATHS = {
    'ggzy': re.compile(r'^/information/(?:deal/)?html/a/\d{6}/\d{4}/\d{8}/[\da-f]+\.html$'),
    'ccgp': re.compile(r'^/cggg/(?:zygg|dfgg)/[\w]+/\d{6}/t\d{8}_\d+\.htm$'),
    'hubei': re.compile(r'^/jyxx/jsgcZbggDetail$'),
    'datong': re.compile(r'^/jyxx(?:gc|zc)[\w]+/\d+\.jhtml$'),
}


def _verified_url(sid: str, url: str, *, root: bool = False, body: bool = False) -> bool:
    """A built-in adapter only accepts its audited origin and notice grammar."""
    try:
        actual, canonical = urlsplit(url), urlsplit(_ORIGINS[sid])
        port = 443 if canonical.scheme == 'https' else 80
        if (actual.scheme != canonical.scheme or actual.hostname != canonical.hostname
                or actual.port not in (None, port) or actual.username is not None
                or actual.password is not None or '#' in url):
            return False
        if root:
            return actual.path in ('', '/') and '?' not in url
        pattern = _PATHS[sid]
        if body:
            pattern = re.compile(pattern.pattern.replace('html/a/', 'html/b/'))
        if not pattern.fullmatch(actual.path):
            return False
        if sid != 'hubei':
            return '?' not in url
        query = parse_qs(actual.query, keep_blank_values=True, max_num_fields=2)
        return (set(query) == {'guid'} and len(query['guid']) == 1
                and _GUID.fullmatch(query['guid'][0]) is not None)
    except (KeyError, TypeError, ValueError):
        return False


def _known_empty_column(sid: str, soup: BeautifulSoup) -> bool:
    if sid != 'ccgp':
        return False
    column = soup.select_one('.vF_detail_relcontent_lst')
    # An empty official list is distinct from a maintenance message, an unknown
    # layout, or a column containing links we do not recognize.
    return (column is not None and column.find('ul') is not None
            and not column.select('li,a') and not column.get_text(strip=True))


def _soup(html: str) -> BeautifulSoup:
    from .collectors import SourceUnavailable
    if re.search(r'CF_APP_WAF|aliyunCaptcha|captcha-container|访问验证|人机验证', html, re.I):
        raise SourceUnavailable('页面要求访问验证，仅保留公开列表线索')
    return BeautifulSoup(html, 'html.parser')


def _text(node) -> str:
    if node is None:
        return ''
    for unwanted in node.select('script,style,nav,header,footer,noscript'):
        unwanted.decompose()
    return re.sub(r'\n{3,}', '\n\n', node.get_text('\n', strip=True))[:65000]


def _body(node) -> str:
    from .collectors import SourceUnavailable
    value = _text(node)
    if len(value) < 120:
        raise SourceUnavailable('公告正文容器缺失或内容不足，不能将导航页当成正文')
    return value


def _date(value: str) -> str | None:
    from .analysis import DATE
    match = re.search(DATE, value)
    return match.group(0).strip() if match else None


def get_candidates(client, source: dict, settings: dict) -> list[dict]:
    from .collectors import SourceUnavailable, relevant,walk_listing_pages,disclosed_listing_links,listing_warning
    from .analysis import date_precision

    sid = source['id']
    if sid not in SOURCE_IDS:
        raise SourceUnavailable('此公共资源来源尚未适配')
    if not _verified_url(sid, source.get('url'), root=True):
        raise SourceUnavailable('来源地址不符合已核验的公开栏目规则')
    origin = _ORIGINS[sid]
    urls = ([origin + '/cggg/zygg/', origin + '/cggg/dfgg/'] if sid == 'ccgp'
            else [origin + '/'])
    def valid_list(target):
        p=urlsplit(target)
        if p.username or p.password or p.query or p.fragment:return False
        if sid=='ccgp':
            return p.scheme=='https' and p.netloc=='www.ccgp.gov.cn' and bool(re.fullmatch(r'/cggg/(?:zygg|dfgg)/(?:index(?:_[1-2])?\.htm)?',p.path))
        if sid=='datong':
            return p.scheme=='https' and p.hostname=='ggzyjy.dt.gov.cn' and p.port in (None,443) and bool(re.fullmatch(r'/jyxx(?:gc|zc)/index(?:_[2-3])?\.jhtml',p.path))
        return False  # GGZY's deeper query requires verification; Hubei's public paged API returned no records.
    def links(html,current):
        targets=disclosed_listing_links(html,current,valid_list)
        if sid=='ccgp':
            pager=re.search(r"Pager\(\{\s*size:\s*(\d+),\s*current:\s*(\d+),\s*prefix:'index',\s*suffix:'htm'",html)
            if pager and int(pager[2])+1<int(pager[1]):
                target=urljoin(current,'index_'+str(int(pager[2])+1)+'.htm')
                if valid_list(target):targets.append(target)
        if sid=='datong' and re.search(r'urlPre:\s*"index"',html) and re.search(r'urlSuf:\s*"\.jhtml"',html):
            curr=re.search(r'\bcurr:\s*(\d+)',html);count=re.search(r'\bcount:\s*(\d+)',html);limit=re.search(r'\blimit:\s*(\d+)',html)
            if curr and count and limit and int(curr[1])*int(limit[1])<int(count[1]):
                target=urljoin(current,'index_'+str(int(curr[1])+1)+'.jhtml')
                if valid_list(target):targets.append(target)
        return targets
    items = {}
    checked=False
    for url,html in walk_listing_pages(client,urls,links,max_pages=7 if sid=='datong' else 6):
        soup = _soup(html)
        recognized = 0
        for anchor in soup.select('a[href]'):
            # Datong's newly disclosed HTTPS column uses relative detail links;
            # retain the registered canonical notice origin for URL deduplication.
            href = urljoin(origin if sid=='datong' else url, anchor['href'])
            parts = urlsplit(href)
            if not _verified_url(sid, href):
                continue
            recognized += 1
            title = anchor.get('title') or anchor.get_text(' ', strip=True)
            if sid == 'datong':
                title = re.split(r'公告类型[：:]|交易场所[：:]', title, maxsplit=1)[0]
            title = re.sub(r'\s+20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s*$', '', title).strip()
            if len(title) < 8 or not relevant(title, settings):
                continue
            # Dates belong to this row, never another announcement on the page.
            row = anchor.find_parent(['li', 'tr']) or anchor
            surrounding = row.get_text(' ', strip=True)
            published = _date(surrounding)
            if not published and sid == 'ggzy':
                match = re.search(r'/(20\d{2})(\d{2})(\d{2})/', parts.path)
                published = '-'.join(match.groups()) if match else None
            items[href] = {'title': title, 'url': href, 'published_at': published,
                           'published_at_precision': date_precision(published)}
        if not recognized and not _known_empty_column(sid, soup):
            if not checked:raise SourceUnavailable('公开公告列表结构缺失，需核验栏目规则')
            listing_warning(client,'补充公告列表结构变化，保留已取得候选')
        else:checked=True
    return list(items.values())[:200]


def read_detail(client, raw: dict, source: dict) -> tuple[str, str, str | None]:
    from .collectors import SourceUnavailable

    sid = source['id']
    if sid not in SOURCE_IDS:
        raise SourceUnavailable('此公共资源来源尚未适配')
    if (not _verified_url(sid, raw.get('url'))
            or ('url' in source and not _verified_url(sid, source['url'], root=True))):
        raise SourceUnavailable('来源地址不符合已核验的公开栏目规则')
    if sid == 'hubei':
        guid = parse_qs(urlsplit(raw['url']).query).get('guid', [''])[0]
        endpoint = 'https://www.hbggzyfwpt.cn/jyxxAjax/jsgcZbggDetail'
        if not client.allowed(endpoint):
            raise SourceUnavailable('站点robots规则不允许查询此公告接口')
        result = client._request(endpoint, 'POST', data={'guid': guid}).json()
        notice = result.get('tender') if isinstance(result, dict) else None
        if not isinstance(notice, dict) or notice.get('tenderBulletinGuid') != guid:
            raise SourceUnavailable('湖北公开公告接口未返回对应正文')
        soup = _soup(notice.get('bulletinContent') or '')
        return _body(soup), notice.get('bulletinName') or '', _date(notice.get('bulletinIssueTime') or '')

    soup = _soup(client.get(raw['url']))
    if sid == 'ggzy':
        # The official wrapper points to a same-record public body fragment.
        # Never follow a site's arbitrary original-link/redirect destination.
        if soup.select_one('#mycontent') is None:
            match = re.search(r'\bfirstLastUrl\s*=\s*[\'"]([^\'"]+)[\'"]', str(soup))
            target = urljoin(raw['url'], match.group(1)) if match else ''
            original = urlsplit(raw['url'])
            body = urlsplit(target)
            if (not _verified_url(sid, target, body=True)
                    or body.path != original.path.replace('/html/a/', '/html/b/')):
                raise SourceUnavailable('全国平台公告正文地址未核实或结构变化')
            soup = _soup(client.get(target))
        title = soup.select_one('.detail .h4_o,.h4_o')
        meta = soup.select_one('.detail .p_o,.p_o')
        return _body(soup.select_one('#mycontent')), _text(title), _date(_text(meta))
    if sid == 'ccgp':
        title = soup.select_one('.vF_detail_header h2')
        published = _date(_text(soup.select_one('#pubTime')))
        content = soup.select_one('.vF_detail_content')
        overview = soup.select_one('.vF_detail_main > .table,.table')
        body = _body(content)
        if overview is not None:
            body = (_text(overview) + '\n' + body)[:65000]
        return body, _text(title), published
    if sid == 'datong':
        # Keep only the audited article containers. A missing/short article is
        # still a useful public list lead, but navigation must never fill it.
        for unwanted in soup.select('script,style,nav,header,footer,noscript'):
            unwanted.decompose()
        title = soup.select_one('.cs_title_P1,.cs_xq_content .cs_title_P2')
        if title is None:
            wrapper = soup.select_one('.cs_xq_content')
            title = wrapper.find('p') if wrapper is not None else None
        published = _date(_text(soup.select_one('.cs_title_P3,.div-title2')))
        body = _text(soup.select_one('.div-article2,.cs_xq_content > table.bid_msgTable'))
        if len(body) < 120:
            raw['content_coverage'] = 'partial' if body else 'list_only'
            raw['detail_status'] = '正文不完整' if body else '仅列表线索'
            raw['extraction_warning'] = (
                '已取得部分公开正文，内容不完整；未取得的信息需核对来源原文。' if body else
                '未取得可核实的详情正文，仅保留公开列表信息；其余信息未知，需核对来源原文。'
            )
        return body, _text(title), published
    raise SourceUnavailable('此公共资源来源尚未适配')
