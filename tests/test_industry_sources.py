import pytest

from app.db import DEFAULT_SETTINGS
from app.collectors import SourceUnavailable
from app.industry_sources import get_candidates, read_detail


class Pages:
    def __init__(self, html):
        self.html = html
        self.requested = []

    def get(self, url):
        self.requested.append(url)
        return self.html


@pytest.mark.parametrize('source,html,title,date,precision', [
    ({'id': 'hbzhan'}, '<div class="topic-detail"><li><div class="text"><p><a href="/news/detail/202192.html" title="垃圾焚烧烟气治理招标">正文摘要</a></p><div class="data"><span class="last">2026-10-13 981次阅读</span></div></div></li></div>', '垃圾焚烧烟气治理招标', '2026-10-13', 'day'),
    ({'id': 'solidwaste'}, '<ul class="news_ul2"><li><h3><a href="/news/365203.html">垃圾焚烧厂烟气提标改造</a></h3><p><span class="fr">10-10 09:12</span></p></li></ul>', '垃圾焚烧厂烟气提标改造', None, 'unknown'),
    ({'id': 'cnhw'}, '<div class="newList"><a class="item" href="/article/detail/123"><h2>垃圾焚烧厂燃烧控制优化</h2><p>正文摘要不能成为标题</p><div class="timer">2026-08-21</div><i>5000</i></a></div>', '垃圾焚烧厂燃烧控制优化', '2026-08-21', 'day'),
])
def test_public_lists_preserve_titles_and_source_date_precision(source, html, title, date, precision):
    items = get_candidates(Pages(html), source, DEFAULT_SETTINGS)
    assert len(items) == 1
    assert items[0]['title'] == title
    assert items[0]['published_at'] == date
    assert items[0]['published_at_precision'] == precision


def test_listing_filters_foreign_urls_exclusions_and_duplicate_anchors():
    html = '<div class="topic-detail"><li><div class="text"><p>' + ''.join(
        f'<a href="{url}">{title}</a>' for url, title in [
            ('/news/detail/1.html', '垃圾焚烧烟气净化系统招标'),
            ('/news/detail/1.html', '垃圾焚烧烟气净化系统招标'),
            ('https://other.example/news/detail/2.html', '垃圾焚烧锅炉优化招标'),
            ('/news/detail/3.html', '垃圾焚烧厂食堂采购公告'),
            ('/news/detail/4.html?account=secret', '垃圾焚烧控制系统招标'),
        ]) + '</p></div></li></div>'
    items = get_candidates(Pages(html), {'id': 'hbzhan'}, DEFAULT_SETTINGS)
    assert [item['url'] for item in items] == ['https://www.hbzhan.com/news/detail/1.html']


@pytest.mark.parametrize('source,html', [
    ('hbzhan', '<div class="topic-detail"><li><div class="text"><p><a href="/news/detail/1.html">水处理设备采购信息</a></p></div></li></div>'),
    ('solidwaste', '<ul class="news_ul2"></ul>'),
    ('cnhw', '<div class="newList"></div>'),
])
def test_recognized_empty_or_unrelated_list_is_not_a_source_failure(source, html):
    assert get_candidates(Pages(html), {'id': source}, {**DEFAULT_SETTINGS, 'focus_keywords': []}) == []


@pytest.mark.parametrize('source', ['hbzhan', 'solidwaste', 'cnhw'])
def test_missing_list_structure_is_not_silently_treated_as_empty(source):
    with pytest.raises(SourceUnavailable):
        get_candidates(Pages('<html>网站维护</html>'), {'id': source}, DEFAULT_SETTINGS)


@pytest.mark.parametrize('source,header,body', [
    ('hbzhan', '<div class="wrapper-title"><h1>烟气治理招标</h1><p>2026-10-13 14:28:07 来源：公开平台</p></div>', '<div class="content">'),
    ('solidwaste', '<div class="newscontent_press"><h1>烟气治理招标</h1><div class="newscontent_press_con">时间：2026-10-13 14:28</div></div>', '<div id="txtedit">'),
    ('cnhw', '<div class="article_tops"><div class="titles">烟气治理招标</div><div class="member_info"><div class="timer">2026-10-13 14:28</div></div></div>', '<div class="article_contents"><div class="contents">'),
])
def test_detail_uses_article_metadata_not_deadline_or_related_news(source, header, body):
    html = header + body + '公开采购项目技术改造内容。' * 20 + '投标截止时间：2099-12-31 23:59<script>ignored()</script></div></div><aside>发布时间：2099-01-01</aside>'
    urls = {'hbzhan': 'https://www.hbzhan.com/news/detail/1.html', 'solidwaste': 'https://www.solidwaste.com.cn/news/1.html', 'cnhw': 'https://www.cn-hw.net/article/detail/1'}
    content, title, date = read_detail(Pages(html), {'url': urls[source]}, {'id': source})
    assert len(content) > 120 and 'ignored' not in content
    assert '2099-01-01' not in content
    assert title == '烟气治理招标'
    assert date == '2026-10-13 14:28'


def test_relative_publication_time_is_unknown_and_edit_timestamp_is_not_used():
    html = '<div class="article_tops"><div class="titles">垃圾焚烧项目分析</div><div class="member_info"><div class="timer">4天前</div></div></div><div class="article_contents"><div class="contents">' + '垃圾焚烧项目公开报道。' * 20 + '</div></div><script>article_editime:"1791510397"</script>'
    assert read_detail(Pages(html), {'url': 'https://www.cn-hw.net/article/detail/1'}, {'id': 'cnhw'})[2] is None


@pytest.mark.parametrize('html', ['<html>CF_APP_WAF 验证</html>', '<div class="wrapper-title"><h1>标题</h1></div><div class="content">页面维护</div>'])
def test_challenges_and_missing_body_cannot_become_notice_evidence(html):
    with pytest.raises(SourceUnavailable):
        read_detail(Pages(html), {'url': 'https://www.hbzhan.com/news/detail/1.html'}, {'id': 'hbzhan'})


def test_detail_rejects_cross_source_urls_before_requesting_them():
    client = Pages('unused')
    with pytest.raises(SourceUnavailable):
        read_detail(client, {'url': 'https://www.hbzhan.com.evil.example/news/detail/1.html'}, {'id': 'hbzhan'})
    assert not client.requested
