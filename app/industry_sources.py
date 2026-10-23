"""Public industry-news adapters; no login, search restrictions or WAF bypasses."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit,parse_qs

from bs4 import BeautifulSoup

from .analysis import DATE, date_precision, parse_date

SOURCE_IDS = frozenset({'hbzhan', 'solidwaste', 'cnhw'})
EXTRA_HOSTS = frozenset({'www.hbzhan.com', 'www.solidwaste.com.cn', 'www.cn-hw.net'})

# These are public static/SSR columns verified on the publishers' own websites.
# They are bounded news windows, not a claim of complete procurement coverage.
LIST_URLS = {
    'hbzhan': 'https://www.hbzhan.com/news/topic-99.html',
    'solidwaste': 'https://www.solidwaste.com.cn/news/',
    'cnhw': 'https://www.cn-hw.net/article',
}
DETAIL_PATHS = {
    'hbzhan': re.compile(r'/news/detail/\d+\.html'),
    'solidwaste': re.compile(r'/news/\d+\.html'),
    'cnhw': re.compile(r'/article/detail/\d+'),
}
LIST_CONTAINERS = {'hbzhan': '.topic-detail', 'solidwaste': '.news_ul2', 'cnhw': '.newList'}
LIST_ANCHORS = {
    'hbzhan': '.topic-detail li .text > p a[href]',
    'solidwaste': '.news_ul2 > li h3 a[href]',
    'cnhw': '.newList > a[href]',
}
BODY_SELECTORS = {'hbzhan': '.content', 'solidwaste': '#txtedit', 'cnhw': '.article_contents .contents'}
TITLE_SELECTORS = {'hbzhan': '.wrapper-title h1', 'solidwaste': '.newscontent_press h1', 'cnhw': '.article_tops .titles'}
TIME_SELECTORS = {'hbzhan': '.wrapper-title p', 'solidwaste': '.newscontent_press_con', 'cnhw': '.article_tops .member_info .timer'}


def _checked_soup(html: str):
    from .collectors import SourceUnavailable

    if re.search(r'CF_APP_WAF|aliyunCaptcha|captcha-container|访问验证|人机验证', html, re.I):
        raise SourceUnavailable('来源页面要求访问验证，本轮停止自动读取')
    return BeautifulSoup(html, 'html.parser')


def _valid_url(url: str, source_id: str) -> bool:
    parts = urlsplit(url)
    # Exact origin and exact article path prevent off-site navigation, query
    # trackers and arbitrary publisher endpoints entering the detail queue.
    return (parts.scheme == 'https' and parts.netloc == urlsplit(LIST_URLS[source_id]).netloc
            and not parts.query and not parts.fragment
            and DETAIL_PATHS[source_id].fullmatch(parts.path) is not None)


def _publication(text: str) -> str | None:
    match = re.search(DATE, text)
    return match.group(0).strip() if match and parse_date(match.group(0)) else None


def get_candidates(client, source: dict, settings: dict) -> list[dict]:
    from .collectors import SourceUnavailable, relevant,walk_listing_pages,disclosed_listing_links,listing_warning

    sid = source['id']
    if sid not in SOURCE_IDS:
        raise SourceUnavailable('行业来源尚无已核验的公开采集规则')
    url = LIST_URLS[sid]
    def valid_list(target):
        p=urlsplit(target);root=urlsplit(url)
        if p.scheme!=root.scheme or p.netloc!=root.netloc or p.fragment:return False
        if sid=='hbzhan':return not p.query and bool(re.fullmatch(r'/news/topic-99(?:_p[2-5])?\.html',p.path))
        if sid=='solidwaste':
            query=parse_qs(p.query)
            return p.path=='/news/list' and query.get('cid')==['246'] and set(query)<={'cid','page'} and query.get('page',['1'])[0] in ('1','2','3','4','5')
        return False  # No public static pagination has been verified for CN-HW.
    links=lambda html,current:disclosed_listing_links(html,current,valid_list)
    items = {}
    checked=False
    for current,html in walk_listing_pages(client,[url],links,max_pages=6 if sid=='solidwaste' else 5):
        soup = _checked_soup(html)
        if soup.select_one(LIST_CONTAINERS[sid]) is None:
            if not checked:raise SourceUnavailable('公开资讯列表结构已变化，未将未知页面当成空列表')
            listing_warning(client,'补充资讯列表结构变化，保留已取得候选');continue
        checked=True
        for anchor in soup.select(LIST_ANCHORS[sid]):
            target = urljoin(current, anchor['href'])
            if not _valid_url(target, sid):continue
            title_node = anchor.select_one('h2') if sid == 'cnhw' else None
            title = (anchor.get('title') or
                     (title_node.get_text(' ', strip=True) if title_node else anchor.get_text(' ', strip=True)))
            title = re.sub(r'\s+', ' ', title).strip()
            if len(title) < 8 or not relevant(title, settings):continue
            row = anchor if sid == 'cnhw' else anchor.find_parent('li')
            time_node = row.select_one('.timer' if sid == 'cnhw' else '.data .last' if sid == 'hbzhan' else 'span.fr') if row else None
            # Relative dates never establish an absolute publication date.
            published = _publication(time_node.get_text(' ', strip=True)) if time_node else None
            items.setdefault(target, {'title': title, 'url': target, 'published_at': published,
                                      'published_at_precision': date_precision(published)})
    return list(items.values())[:180]


def read_detail(client, raw: dict, source: dict) -> tuple[str, str, str | None]:
    from .collectors import SourceUnavailable

    sid = source['id']
    if sid not in SOURCE_IDS or not _valid_url(raw['url'], sid):
        raise SourceUnavailable('详情链接超出该来源已核验的公开资讯路径')
    soup = _checked_soup(client.get(raw['url']))
    title_node = soup.select_one(TITLE_SELECTORS[sid])
    container = soup.select_one(BODY_SELECTORS[sid])
    if title_node is None or container is None:
        raise SourceUnavailable('公开资讯正文结构已变化，不能以导航或维护页面充当正文')
    time_node = soup.select_one(TIME_SELECTORS[sid])
    published = _publication(time_node.get_text(' ', strip=True)) if time_node else None
    title = title_node.get_text(' ', strip=True)
    for node in container.select('script,style,nav,header,footer,noscript'):
        node.decompose()
    content = re.sub(r'\n{3,}', '\n\n', container.get_text('\n', strip=True))
    if len(content) < 120:
        raise SourceUnavailable('公开资讯正文为空或过短，需核验页面结构')
    return content[:65000], title, published
