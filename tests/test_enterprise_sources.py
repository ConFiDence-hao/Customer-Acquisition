"""Verified enterprise public information, distinct from procurement coverage."""
import importlib

import httpx
import pytest

from app.collectors import SourceUnavailable
from app.db import DEFAULT_SETTINGS


ORIGIN = 'https://wannaenergy.com'
LIST_URL = ORIGIN + '/news/notice'
DETAIL_URL = ORIGIN + '/news/notice/detail/1679'
SOURCE = {'id': 'wangneng', 'url': LIST_URL}
TITLE = '兰溪市垃圾分类资源化利用扩建项目生态环境影响报告书征求意见稿公示'

# Reduced to the original content containers from the public GET responses.
# Rows, timestamps and the pagination URL were checked against the website.
FIRST = '''<html><nav><a href="/news/notice/detail/999">导航链接不属于列表</a></nav>
<div id="news-list"><a class="clearfix" href="/news/notice/detail/1679"><div class="main-info">
<h3 class="ellipsis"><span class="date">2026-09-13</span>兰溪市垃圾分类资源化利用扩建项目生态环境影响报告书征求意见稿公示</h3>
<div class="desc">本项目生态环境影响报告书征求意见稿已完成...[详情]</div></div></a>
<a class="clearfix" href="/news/notice/detail/1665"><div class="main-info"><h3 class="ellipsis"><span class="date">2026-05-18</span>关于台州旺能再生资源利用有限公司危险废物转移联单的公示</h3></div></a></div>
<ul class="pagination"><li><a data-page="0" href="/news/notice?page=1">1</a></li><li><a data-page="1" href="/news/notice?page=2">2</a></li>
<li><a data-page="3" href="/news/notice?page=4">尾页</a></li></ul></html>'''
SECOND = '''<div id="news-list"><a class="clearfix" href="/news/notice/detail/1643"><div class="main-info">
<h3 class="ellipsis"><span class="date">2025-11-02</span>湖州南太湖环保能源有限公司#4焚烧炉掺烧一般工业固废技改项目环境影响评价信息公示</h3>
<div class="desc">一、建设项目基本情况项目名称...[详情]</div></div></a></div>
<ul class="pagination"><li><a href="/news/notice?page=3">»</a></li></ul>'''
THIRD = '''<div id="news-list"><a href="/news/notice/detail/1461"><h3><span class="date">2023-11-06</span>台州旺能4#炉烟气提标改造项目环境影响评价信息公示</h3></a>
<a href="/news/notice/detail/1679"><h3><span class="date">2026-09-13</span>兰溪市垃圾分类资源化利用扩建项目生态环境影响报告书征求意见稿公示</h3></a></div>
<ul class="pagination"><li><a href="/news/notice?page=4">4</a></li></ul>'''
FOURTH = '''<div id="news-list"><a href="/news/notice/detail/1370"><h3><span class="date">2022-07-13</span>湖州南太湖环保能源有限公司1000吨炉排炉垃圾焚烧发电改造项目环境影响评价信息公示</h3></a></div>
<ul class="pagination"><li><a href="/news/notice?page=5">»</a></li></ul>'''
DETAIL = '''<nav>走进旺能 投资者关系 联系我们</nav><div class="news-detail-hd">
<h1>兰溪市垃圾分类资源化利用扩建项目生态环境影响报告书征求意见稿公示</h1><div class="date"><span>2026-09-13 15:23</span></div>
<div class="content"><html><body><p>本项目生态环境影响报告书征求意见稿已完成，根据《环境影响评价公众参与办法》要求现公开征求公众意见。</p>
<p>建设单位名称：兰溪旺能环境科技有限公司</p><p>地址：浙江省兰溪市黄店镇生活垃圾填埋场旁</p>
<p>（一）生态环境影响报告书征求意见稿全文的网络链接及查阅纸质报告书的方式和途径：</p>
<p>网络链接：https://www.wannaenergy.com/news/notice/detail/1678</p><p>纸质报告查阅地址：金华市李渔路1089号宝莲广场B座5楼</p>
<p>（二）征求意见的公众范围</p><p>以公司厂区为中心、边长5km的矩形区域内的公民、法人和其他组织。</p>
<p>（五）公众提出意见的起止时间</p><p>2026年9月13日至2026年9月24日（共10个工作日）。</p></body></html></div></div>
<footer>公司版权 版权所有</footer>'''


def adapter():
    return importlib.import_module('app.enterprise_sources')


class PublicPages:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.listing_warnings = []
        self.listing_pages = 0

    def get(self, url):
        self.calls.append(url)
        result = self.pages[url]
        if isinstance(result, Exception):
            raise result
        return result


def test_real_public_rows_follow_four_pages_and_keep_title_without_excerpt():
    client = PublicPages({LIST_URL: FIRST, LIST_URL + '?page=2': SECOND,
                          LIST_URL + '?page=3': THIRD, LIST_URL + '?page=4': FOURTH})
    settings = {**DEFAULT_SETTINGS, 'collection_topics': [{'keywords': ['扩建', '焚烧炉'], 'exclude_keywords': []}]}
    rows = adapter().get_candidates(client, SOURCE, settings)
    assert [(row['url'], row['published_at']) for row in rows] == [
        (DETAIL_URL, '2026-09-13'), (ORIGIN + '/news/notice/detail/1643', '2025-11-02'),
        (ORIGIN + '/news/notice/detail/1461', '2023-11-06'),
        (ORIGIN + '/news/notice/detail/1370', '2022-07-13')]
    assert rows[0]['title'] == TITLE
    assert rows[0]['published_at_precision'] == 'day'
    assert client.calls == [LIST_URL, LIST_URL + '?page=2', LIST_URL + '?page=3', LIST_URL + '?page=4']
    assert client.listing_pages == 4
    assert client.listing_warnings == []


def test_real_public_detail_excludes_navigation_and_keeps_header_publication():
    client = PublicPages({DETAIL_URL: DETAIL})
    content, title, publication = adapter().read_detail(client, {'url': DETAIL_URL}, SOURCE)
    assert title == TITLE
    assert publication == '2026-09-13 15:23'
    assert '建设单位名称：兰溪旺能环境科技有限公司' in content
    assert '2026年9月24日' in content
    assert '走进旺能' not in content and '公司版权' not in content
    assert client.calls == [DETAIL_URL]


@pytest.mark.parametrize('url', [
    'https://unverified.example/news/notice/detail/1679',
    ORIGIN + ':8000/news/notice/detail/1679',
    ORIGIN + '/news/notice/detail/1679?url=https://unverified.example/',
    ORIGIN + '/investor/announcement',
    'https://user:pass@wannaenergy.com/news/notice/detail/1679',
    ORIGIN + '/news/notice/detail/1679#fragment',
])
def test_unverified_detail_destinations_fail_before_any_request(url):
    client = PublicPages({})
    with pytest.raises(SourceUnavailable):
        adapter().read_detail(client, {'url': url}, SOURCE)
    assert client.calls == []


def test_unknown_structure_is_a_failure_and_cannot_be_reported_as_empty():
    client = PublicPages({LIST_URL: '<h1>网站维护</h1>'})
    with pytest.raises(SourceUnavailable):
        adapter().get_candidates(client, SOURCE, DEFAULT_SETTINGS)


def test_access_verification_stops_before_following_next_page():
    client = PublicPages({LIST_URL: '访问验证 captcha-container' + FIRST})
    with pytest.raises(SourceUnavailable):
        adapter().get_candidates(client, SOURCE, DEFAULT_SETTINGS)
    assert client.calls == [LIST_URL]


def test_off_origin_pagination_is_not_followed():
    html = FIRST.replace('/news/notice?page=2', 'https://unverified.example/news/notice?page=2')
    client = PublicPages({LIST_URL: html})
    rows = adapter().get_candidates(client, SOURCE, DEFAULT_SETTINGS)
    assert any(row['url'] == DETAIL_URL for row in rows)
    assert client.calls == [LIST_URL]


def test_globally_excluded_but_recognized_public_list_can_return_empty():
    client = PublicPages({LIST_URL: FIRST, LIST_URL + '?page=2': SECOND,
                          LIST_URL + '?page=3': THIRD, LIST_URL + '?page=4': FOURTH})
    settings = {**DEFAULT_SETTINGS, 'exclude_keywords': ['公示']}
    assert adapter().get_candidates(client, SOURCE, settings) == []
    assert client.listing_pages == 4


def test_public_candidates_stop_at_eighty_even_if_a_page_is_larger():
    rows = ''.join('<a href="/news/notice/detail/'+str(2000+i)+'"><h3><span class="date">2026-09-13</span>'+TITLE+'</h3></a>' for i in range(81))
    client = PublicPages({LIST_URL: '<div id="news-list">'+rows+'</div><ul class="pagination"><a href="/news/notice?page=2">2</a></ul>'})
    result = adapter().get_candidates(client, SOURCE, DEFAULT_SETTINGS)
    assert len(result) == 80
    assert result[-1]['url'] == ORIGIN + '/news/notice/detail/2079'
    assert client.calls == [LIST_URL]


@pytest.mark.parametrize('sid', ['grandblue', 'sjec', 'sinocycle', 'junxin', 'beeg'])
def test_unverified_enterprise_sources_are_never_fetched(sid):
    client = PublicPages({})
    with pytest.raises(SourceUnavailable):
        adapter().get_candidates(client, {'id': sid, 'url': LIST_URL}, DEFAULT_SETTINGS)
    assert client.calls == []


def test_page_excerpt_without_detail_container_cannot_become_body():
    client = PublicPages({DETAIL_URL: FIRST})
    with pytest.raises(SourceUnavailable):
        adapter().read_detail(client, {'url': DETAIL_URL}, SOURCE)


@pytest.mark.parametrize('page', [2, 3, 4])
@pytest.mark.parametrize('failure', [
    SourceUnavailable('公开页面访问受到限制'),
    httpx.ReadTimeout('public page timed out'),
    '<h1>页面维护，原列表结构已变化</h1>',
    '访问验证 captcha-container',
])
def test_later_page_failure_preserves_previously_verified_rows(page, failure):
    pages = {LIST_URL: FIRST, LIST_URL + '?page=2': SECOND,
             LIST_URL + '?page=3': THIRD, LIST_URL + '?page=4': FOURTH}
    pages[LIST_URL + '?page=' + str(page)] = failure
    client = PublicPages(pages)
    result = adapter().get_candidates(client, SOURCE, DEFAULT_SETTINGS)
    expected_urls = [DETAIL_URL, ORIGIN + '/news/notice/detail/1643', ORIGIN + '/news/notice/detail/1461'][:page-1]
    assert [row['url'] for row in result] == expected_urls
    assert client.calls[-1] == LIST_URL + '?page=' + str(page)
    assert len(client.calls) == page
    assert client.listing_pages == page - 1
    assert client.listing_warnings


def test_later_failure_after_valid_but_filtered_first_list_returns_empty():
    client = PublicPages({LIST_URL: FIRST, LIST_URL + '?page=2': httpx.ConnectError('connection failed')})
    settings = {**DEFAULT_SETTINGS, 'exclude_keywords': ['公示']}
    assert adapter().get_candidates(client, SOURCE, settings) == []
    assert client.listing_pages == 1
    assert client.listing_warnings


def test_first_page_http_failure_is_still_reported():
    client = PublicPages({LIST_URL: httpx.ReadTimeout('first page timed out')})
    with pytest.raises(httpx.ReadTimeout):
        adapter().get_candidates(client, SOURCE, DEFAULT_SETTINGS)


def test_repeated_notice_page_stops_even_if_pagination_url_keeps_advancing():
    repeated = FIRST.replace('</ul>', '<li><a href="/news/notice?page=3">3</a></li></ul>')
    client = PublicPages({LIST_URL: repeated, LIST_URL + '?page=2': repeated,
                          LIST_URL + '?page=3': THIRD, LIST_URL + '?page=4': FOURTH})
    result = adapter().get_candidates(client, SOURCE, DEFAULT_SETTINGS)
    assert [row['url'] for row in result] == [DETAIL_URL]
    assert client.calls == [LIST_URL, LIST_URL + '?page=2']
    assert client.listing_pages == 2
    assert client.listing_warnings
