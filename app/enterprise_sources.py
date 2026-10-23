"""First-party enterprise disclosures, with procurement coverage stated explicitly.

Wangneng's verified environmental disclosures offer early project leads. They
are not invitations to bid. Other audited enterprise sites remain unadapted.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .analysis import DATE, date_precision, parse_date

SOURCE_IDS = frozenset({'wangneng'})
EXTRA_HOSTS = frozenset()
CANONICAL_URLS = {'wangneng': 'https://wannaenergy.com/news/notice'}
COVERAGE_NOTES = {
    'wangneng': '官网信息公示最多前4页、80条；提供扩建、技改与环评等前期项目线索，并非采购招标公告；历史公示保留原日期，正文读取公开网页，附件保留原站查阅',
}
BLOCKED_NOTES = {
    'grandblue': '采购平台robots入口返回HTTP 403，停止自动读取；公告列表与正文尚未适配。',
    'sjec': '官网供应商SRM可访问；公开页面实际调用的公告接口要求登录Token，未接入自动采集。',
    'sinocycle': '当前官网及网站地图可访问，尚未确认公开采购公告栏目或接口。',
    'junxin': '官网通知公告栏目可访问，目前只有公众参观声明，尚未确认采购公告栏目。',
    'beeg': '母集团环境业务页可访问；北控环境与北控环保为不同主体，独立采购栏目尚未确认。',
}

_ORIGIN = 'https://wannaenergy.com'
_DETAIL = re.compile(r'^/news/notice/detail/\d+$')
_PROTECTION = re.compile(r'CF_APP_WAF|aliyunCaptcha|captcha-container|访问验证|人机验证', re.I)


def _verified(url: str, *, detail=False, root=False, page=None) -> bool:
    try:
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.hostname != 'wannaenergy.com'
                or parts.port not in (None, 443) or parts.username is not None
                or parts.password is not None or '#' in url):
            return False
        if detail:
            return _DETAIL.fullmatch(parts.path) is not None and '?' not in url
        if root:
            return parts.path in ('', '/', '/news/notice') and '?' not in url
        if page is not None:
            query = parse_qs(parts.query, keep_blank_values=True, max_num_fields=2)
            return parts.path == '/news/notice' and query == {'page': [str(page)]}
        return False
    except (TypeError, ValueError):
        return False


def _soup(html: str):
    from .collectors import SourceUnavailable
    if _PROTECTION.search(html):
        raise SourceUnavailable('来源页面要求访问验证，本轮停止自动读取')
    return BeautifulSoup(html, 'html.parser')


def _date(text: str) -> str | None:
    match = re.search(DATE, text)
    value = match.group(0).strip() if match else None
    return value if value and parse_date(value) else None


def get_candidates(client, source: dict, settings: dict) -> list[dict]:
    from .collectors import SourceUnavailable, listing_warning, relevant

    if source.get('id') not in SOURCE_IDS or not _verified(source.get('url'), root=True):
        raise SourceUnavailable('集团来源尚无已核验的公开公告采集规则')
    items = {}
    first_list_verified = False
    seen_pages = set()
    url = CANONICAL_URLS['wangneng']
    for page in range(1, 5):
        try:
            soup = _soup(client.get(url))
            container = soup.select_one('#news-list')
            if container is None:
                raise SourceUnavailable('集团公开信息公示列表结构缺失，需重新核验')
            rows = [(anchor, urljoin(url, anchor['href']))
                    for anchor in container.select('a[href]')
                    if _verified(urljoin(url, anchor['href']), detail=True)
                    and anchor.select_one('h3') is not None]
            if not rows:
                raise SourceUnavailable('未取得已核验的集团公示链接，不能将未知页面当成空列表')
        except (SourceUnavailable, httpx.HTTPError):
            if not first_list_verified:
                raise
            listing_warning(client, '旺能后续公示页未能读取，已保留此前公开列表。')
            break
        first_list_verified = True
        client.listing_pages = getattr(client, 'listing_pages', 0) + 1
        # Notice identities detect repeated bodies even when a broken site's
        # pagination URLs keep changing. Filtering does not affect this check.
        signature = tuple(sorted({target for _, target in rows}))
        if signature in seen_pages:
            listing_warning(client, '旺能公示分页返回重复公告，已停止扩展。')
            break
        seen_pages.add(signature)
        for anchor, target in rows:
            heading = anchor.select_one('h3')
            time_node = heading.select_one('.date')
            publication = _date(time_node.get_text(' ', strip=True)) if time_node else None
            if time_node:
                time_node.decompose()
            title = re.sub(r'\s+', ' ', heading.get_text(' ', strip=True))
            if len(title) < 8 or not relevant(title, settings):
                continue
            items.setdefault(target, {'title': title, 'url': target,
                                     'published_at': publication,
                                     'published_at_precision': date_precision(publication)})
            if len(items) >= 80:
                return list(items.values())
        if page < 4:
            # Follow consecutive observed navigation. Off-site links and the
            # last-page shortcut cannot skip pages or expand the bounded window.
            next_url = next((urljoin(url, a['href']) for a in soup.select('.pagination a[href]')
                             if _verified(urljoin(url, a['href']), page=page+1)), None)
            if next_url is None:
                break
            url = next_url
    return list(items.values())


def read_detail(client, raw: dict, source: dict) -> tuple[str, str, str | None]:
    from .collectors import SourceUnavailable

    if (source.get('id') not in SOURCE_IDS or not _verified(source.get('url'), root=True)
            or not _verified(raw.get('url'), detail=True)):
        raise SourceUnavailable('详情链接超出已核验的集团公示栏目')
    soup = _soup(client.get(raw['url']))
    heading = soup.select_one('.news-detail-hd h1')
    container = soup.select_one('.news-detail-hd .content')
    if heading is None or container is None:
        raise SourceUnavailable('集团公示正文或标题容器缺失，不能以列表摘要充当正文')
    title = re.sub(r'\s+', ' ', heading.get_text(' ', strip=True))
    time_node = soup.select_one('.news-detail-hd > .date')
    publication = _date(time_node.get_text(' ', strip=True)) if time_node else None
    for node in container.select('script,style,nav,header,footer,noscript'):
        node.decompose()
    content = re.sub(r'\n{3,}', '\n\n', container.get_text('\n', strip=True))
    if len(content) < 120:
        raise SourceUnavailable('集团公示网页正文不足，附件须在原站查阅')
    return content[:65000], title, publication
