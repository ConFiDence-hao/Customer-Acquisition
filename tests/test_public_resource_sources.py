"""Representative structures from publicly readable official procurement pages."""
import importlib
import importlib.util

import httpx
import pytest

from app.collectors import SourceUnavailable
from app.db import DEFAULT_SETTINGS

NOTICE_URLS = {
    'ccgp': 'https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261013_100.htm',
    'datong': 'http://ggzyjy.dt.gov.cn/jyxxgcgz/382768.jhtml',
    'ggzy': 'https://www.ggzy.gov.cn/information/deal/html/a/140000/0101/20261013/1234.html',
}


def adapter():
    assert importlib.util.find_spec('app.public_resource_sources'), 'public source adapter is missing'
    return importlib.import_module('app.public_resource_sources')


class Pages:
    def __init__(self, pages, post_response=None, allow=True):
        self.pages = pages
        self.post_response = post_response
        self.allow = allow

    def get(self, url):
        return self.pages[url]

    def allowed(self, url):
        return self.allow

    def _request(self, url, method='GET', **kwargs):
        assert method == 'POST' and kwargs['data'] == {'guid': '132023b8-9d22-4997-b656-7427965e0a57'}
        return httpx.Response(200, json=self.post_response)


def test_ccgp_lists_read_publication_minutes_and_filter_unrelated_news():
    # Removing the /cggg/ restriction or parsing only URL dates would break this.
    html = '''<div class="vF_detail_relcontent_lst"><ul>
      <li><a title="垃圾焚烧烟气提标采购公告" href="./gkzb/202610/t20261013_27484590.htm">短标题</a>
        发布时间：<em>2026-10-13 13:42</em>地域：<em>湖北</em></li>
      <li><a href="https://www.ccgp.gov.cn/news/202610/t20261013_999.htm">垃圾行业政策新闻</a></li>
      <li><a href="./gkzb/202610/t20261013_100.htm">垃圾焚烧厂食堂采购公告</a></li>
    </ul></div>'''
    pages = Pages({f'https://www.ccgp.gov.cn/cggg/{part}/': html for part in ('zygg', 'dfgg')})
    result = adapter().get_candidates(pages, {'id': 'ccgp', 'url': 'https://www.ccgp.gov.cn/'}, DEFAULT_SETTINGS)
    assert len(result) == 2
    assert {row['title'] for row in result} == {'垃圾焚烧烟气提标采购公告'}
    assert all(row['published_at'] == '2026-10-13 13:42' and row['published_at_precision'] == 'minute' for row in result)


def test_datong_cleans_type_labels_from_title_and_keeps_list_day_precision():
    html = '''<a href="/jyxxgcgz/382768.jhtml"><span>大同生活垃圾中转压缩站设备更新建设项目撤销公告</span>
        <p>公告类型：更正公告 交易场所：大同市 2026/10/13</p></a>
        <a href="https://evil.example/jyxxgczb/998.jhtml">垃圾焚烧设备采购公告</a>'''
    result = adapter().get_candidates(Pages({'http://ggzyjy.dt.gov.cn/': html}),
                                      {'id': 'datong', 'url': 'http://ggzyjy.dt.gov.cn/'}, DEFAULT_SETTINGS)
    assert len(result) == 1
    assert result[0]['title'] == '大同生活垃圾中转压缩站设备更新建设项目撤销公告'
    assert result[0]['published_at'] == '2026/10/13'
    assert result[0]['published_at_precision'] == 'day'


def test_hubei_list_excludes_unadapted_project_registration_pages():
    html = '''<li><a href="/jyxx/jsgcZbggDetail?guid=132023b8-9d22-4997-b656-7427965e0a57"
       title="垃圾焚烧SCR脱硝改造招标公告">垃圾焚烧SCR脱硝改造招标公告</a><span>2026-10-13</span></li>
       <li><a href="/jyxx/jsgcXmxxDetail3?guid=222">垃圾焚烧项目注册</a><span>2026-10-13</span></li>'''
    result = adapter().get_candidates(Pages({'https://www.hbggzyfwpt.cn/': html}),
                                      {'id': 'hubei', 'url': 'https://www.hbggzyfwpt.cn/'}, DEFAULT_SETTINGS)
    assert len(result) == 1 and 'jsgcZbggDetail?' in result[0]['url']
    assert result[0]['published_at_precision'] == 'day'


def test_valid_listing_with_no_matching_topics_is_empty_not_an_adapter_error():
    html = '<li><a href="/jyxxgczb/382779.jhtml">阳高县高标准农田建设招标公告</a><span>2026/10/13</span></li>'
    assert adapter().get_candidates(Pages({'http://ggzyjy.dt.gov.cn/': html}),
                                    {'id': 'datong', 'url': 'http://ggzyjy.dt.gov.cn/'}, DEFAULT_SETTINGS) == []


def test_missing_listing_structure_does_not_look_like_empty_results():
    with pytest.raises(SourceUnavailable, match='列表结构'):
        adapter().get_candidates(Pages({'http://ggzyjy.dt.gov.cn/': '<body>站点维护中</body>'}),
                                 {'id': 'datong', 'url': 'http://ggzyjy.dt.gov.cn/'}, DEFAULT_SETTINGS)


def test_ccgp_detail_keeps_overview_and_body_but_not_navigation():
    html = '''<nav>错误导航文本</nav><div class="vF_detail_header"><h2>垃圾焚烧烟气提标采购公告</h2>
       <span id="pubTime">2026年10月13日 13:42</span></div><div class="table">预算金额 ￥108万元</div>
       <div class="vF_detail_content">''' + '烟气净化改造采购需求。' * 25 + '''原公告发布日期：2025-01-01
       <script>无关脚本</script></div><footer>错误页脚文本</footer>'''
    url = NOTICE_URLS['ccgp']
    body, title, date = adapter().read_detail(Pages({url: html}), {'url': url}, {'id': 'ccgp'})
    assert title == '垃圾焚烧烟气提标采购公告' and date == '2026年10月13日 13:42'
    assert '预算金额' in body and '烟气净化' in body
    assert '错误导航' not in body and '错误页脚' not in body and '无关脚本' not in body


def test_datong_detail_prefers_labelled_publication_time_over_body_deadline():
    html = '''<div class="cs_xq_content"><p class="cs_title_P2">垃圾站设备更新撤销公告</p>
       <p class="cs_title_P3">发布日期：2026-10-12 18:03</p><div class="div-title2">公告发布时间：2026-10-12 18:03:54</div>
       <div class="div-article2">''' + '本项目招标计划有变，公告撤销。' * 15 + '''投标截止时间：2026-11-01 09:00</div></div>'''
    url = NOTICE_URLS['datong']
    _, title, date = adapter().read_detail(Pages({url: html}), {'url': url}, {'id': 'datong'})
    assert title == '垃圾站设备更新撤销公告' and date == '2026-10-12 18:03'


def test_hubei_detail_uses_public_form_api_not_placeholder_html():
    response = {'tender': {'tenderBulletinGuid': '132023b8-9d22-4997-b656-7427965e0a57',
                          'bulletinName': '垃圾焚烧SCR脱硝改造招标公告', 'bulletinIssueTime': '2026-10-13',
                          'bulletinContent': '<p>' + '脱硝改造施工采购要求。' * 30 + '</p>'}}
    url = 'https://www.hbggzyfwpt.cn/jyxx/jsgcZbggDetail?guid=132023b8-9d22-4997-b656-7427965e0a57'
    body, title, date = adapter().read_detail(Pages({url: '<div id="detailNeirong"></div>'}, response),
                                             {'url': url}, {'id': 'hubei'})
    assert title == response['tender']['bulletinName'] and date == '2026-10-13'
    assert '脱硝改造施工采购要求' in body


def test_hubei_detail_respects_robots_for_public_api():
    url = 'https://www.hbggzyfwpt.cn/jyxx/jsgcZbggDetail?guid=132023b8-9d22-4997-b656-7427965e0a57'
    with pytest.raises(SourceUnavailable, match='robots'):
        adapter().read_detail(Pages({}, allow=False), {'url': url}, {'id': 'hubei'})


def test_ggzy_only_reads_verified_same_site_body_document():
    url = 'https://www.ggzy.gov.cn/information/deal/html/a/140000/0101/20261013/1234.html'
    body_url = url.replace('/html/a/', '/html/b/')
    wrapper = '<h4 class="h4_o">垃圾焚烧烟气改造招标公告</h4><script>var firstLastUrl = "' + body_url + '";</script>'
    body_html = '''<div class="detail"><h4 class="h4_o">垃圾焚烧烟气改造招标公告</h4>
       <p class="p_o"><span>发布时间：2026-10-13 00:00</span></p><div id="mycontent">''' + '烟气治理招标要求。' * 30 + '</div></div>'
    body, title, date = adapter().read_detail(Pages({url: wrapper, body_url: body_html}), {'url': url}, {'id': 'ggzy'})
    assert '烟气治理招标要求' in body and title == '垃圾焚烧烟气改造招标公告'
    assert date == '2026-10-13 00:00'


def test_ggzy_wrapper_cannot_redirect_reader_to_unverified_host():
    url = 'https://www.ggzy.gov.cn/information/deal/html/a/140000/0101/20261013/1234.html'
    html = '<script>var firstLastUrl = "https://evil.example/information/deal/html/b/a.html";</script>'
    with pytest.raises(SourceUnavailable, match='正文地址'):
        adapter().read_detail(Pages({url: html}), {'url': url}, {'id': 'ggzy'})


@pytest.mark.parametrize('source_id', ['ccgp', 'datong', 'ggzy'])
def test_challenge_page_is_never_ingested_as_procurement_body(source_id):
    url = NOTICE_URLS[source_id]
    with pytest.raises(SourceUnavailable, match='访问验证'):
        adapter().read_detail(Pages({url: '<div>captcha-container 人机验证</div>'}), {'url': url}, {'id': source_id})


@pytest.mark.parametrize('invalid', [
    'file://ggzyjy.dt.gov.cn/jyxxgczb/382779.jhtml',
    'http://user:secret@ggzyjy.dt.gov.cn/jyxxgczb/382779.jhtml',
    'http://ggzyjy.dt.gov.cn/jyxxgczb/382779.jhtml?token=secret',
    'http://ggzyjy.dt.gov.cn/jyxxgczb/382779.jhtml#fragment',
    'https://ggzyjy.dt.gov.cn/jyxxgczb/382779.jhtml',
])
def test_list_only_returns_canonical_public_urls(invalid):
    html = '<li><a href="/jyxxgcgz/382768.jhtml">垃圾中转站设备更新撤销公告</a><span>2026/10/13</span></li>'
    html += '<li><a href="' + invalid + '">垃圾焚烧设备采购公告</a></li>'
    result = adapter().get_candidates(Pages({'http://ggzyjy.dt.gov.cn/': html}),
                                      {'id': 'datong', 'url': 'http://ggzyjy.dt.gov.cn/'}, DEFAULT_SETTINGS)
    assert [item['url'] for item in result] == ['http://ggzyjy.dt.gov.cn/jyxxgcgz/382768.jhtml']


@pytest.mark.parametrize('sid,url', [
    ('ccgp', 'file://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261013_100.htm'),
    ('datong', 'http://user:secret@ggzyjy.dt.gov.cn/jyxxgcgz/382768.jhtml'),
    ('ggzy', NOTICE_URLS['ggzy'] + '?token=secret'),
    ('ggzy', NOTICE_URLS['ggzy'] + '#fragment'),
    ('datong', NOTICE_URLS['datong'].replace('http:', 'https:')),
    ('ccgp', NOTICE_URLS['ccgp'].replace('www.ccgp.gov.cn', 'foreign.example')),
    ('ccgp', 'https://www.ccgp.gov.cn/news/202610/t20261013_100.htm'),
    ('hubei', 'https://www.hbggzyfwpt.cn/jyxx/jsgcZbggDetail?guid=132023b8-9d22-4997-b656-7427965e0a57&token=secret'),
    ('hubei', 'https://www.hbggzyfwpt.cn/jyxx/jsgcZbggDetail?guid=132023b8-9d22-4997-b656-7427965e0a57&guid=132023b8-9d22-4997-b656-7427965e0a57'),
])
def test_detail_rejects_unverified_url_before_any_network_read(sid, url):
    with pytest.raises(SourceUnavailable, match='来源地址'):
        adapter().read_detail(Pages({}), {'url': url}, {'id': sid})


def test_modified_source_origin_cannot_redirect_builtin_collector():
    with pytest.raises(SourceUnavailable, match='来源地址'):
        adapter().get_candidates(Pages({}), {'id': 'ccgp', 'url': 'https://foreign.example/'}, DEFAULT_SETTINGS)


def test_ccgp_empty_verified_column_keeps_notices_from_other_column():
    pages = Pages({
        'https://www.ccgp.gov.cn/cggg/zygg/': '<div class="vF_detail_relcontent_lst"><ul></ul></div>',
        'https://www.ccgp.gov.cn/cggg/dfgg/': '<div class="vF_detail_relcontent_lst"><ul><li>'
        '<a href="./gkzb/202610/t20261013_100.htm">垃圾焚烧烟气改造采购公告</a>'
        '发布时间：<em>2026-10-13 13:42</em></li></ul></div>',
    })
    result = adapter().get_candidates(pages, {'id': 'ccgp', 'url': 'https://www.ccgp.gov.cn/'}, DEFAULT_SETTINGS)
    assert len(result) == 1 and result[0]['published_at'] == '2026-10-13 13:42'


def test_ccgp_maintenance_page_is_not_a_valid_empty_column():
    pages = Pages({'https://www.ccgp.gov.cn/cggg/zygg/': '<div class="vF_detail_relcontent_lst">系统维护中</div>'})
    with pytest.raises(SourceUnavailable, match='列表结构'):
        adapter().get_candidates(pages, {'id': 'ccgp', 'url': 'https://www.ccgp.gov.cn/'}, DEFAULT_SETTINGS)


def test_datong_plan_reads_verified_project_table_and_publication_metadata():
    # Official 382759.jhtml uses a project table rather than .div-article2.
    url = 'http://ggzyjy.dt.gov.cn/jyxxgcjh/382759.jhtml'
    html = '''<nav>无关导航</nav><p class="cs_title_P1">浑源县毓园社区养老服务中心等三所养老机构提质改造工程</p>
      <p class="cs_title_P3">发布日期：2026-10-10 16:10</p><div class="cs_xq_content">
      <table class="bid_msgTable"><tr><td>投资项目统一代码：</td><td>2606-140225-89-01-658473</td></tr>
      <tr><td>项目名称：</td><td>浑源县毓园社区养老服务中心等三所养老机构提质改造工程</td></tr>
      <tr><td>项目类型：</td><td>房屋市政</td><td>项目总投资：</td><td>1715.36（万元）</td></tr>
      <tr><td>建设内容及规模：</td><td>对三所养老机构现有建筑及设施进行适老化改造，并建设一体化服务管理平台及配套智能终端。
      改造总建筑面积11437.46平方米，新建总建筑面积453.84平方米。</td></tr>
      <tr><td>招标公告（资格预审公告）预计发布时间：</td><td>2026年11月</td></tr></table></div><footer>无关页脚</footer>'''
    body, title, date = adapter().read_detail(Pages({url: html}), {'url': url}, {'id': 'datong'})
    assert title == '浑源县毓园社区养老服务中心等三所养老机构提质改造工程'
    assert date == '2026-10-10 16:10'
    assert '1715.36（万元）' in body and '一体化服务管理平台' in body and '2026年11月' in body
    assert '无关导航' not in body and '无关页脚' not in body


def test_datong_short_abnormality_record_keeps_real_body_and_marks_partial():
    url = 'http://ggzyjy.dt.gov.cn/jyxxgcyc/382778.jhtml'
    html = '''<p class="cs_title_P1">智慧水务平台建设设备采购终止公告</p>
       <p class="cs_title_P3">发布日期：2026-10-13 18:00</p><div class="cs_xq_content">
       <div class="div-title2">审批或核准时间：2026-10-13 18:00:34</div>
       <div class="div-article2">异常情况描述<br>其他<br>审批或核准结果<br>审核通过</div></div>'''
    raw = {'url': url, 'title': '列表中已核实的标题', 'published_at': '2026-10-13', 'source_id': 'datong'}
    body, title, date = adapter().read_detail(Pages({url: html}), raw, {'id': 'datong'})
    assert body == '异常情况描述\n其他\n审批或核准结果\n审核通过'
    assert title == '智慧水务平台建设设备采购终止公告' and date == '2026-10-13 18:00'
    assert raw['content_coverage'] == 'partial' and raw['detail_status'] == '正文不完整'
    assert raw['extraction_warning'] == '已取得部分公开正文，内容不完整；未取得的信息需核对来源原文。'
    assert raw['title'] == '列表中已核实的标题' and raw['published_at'] == '2026-10-13'
    assert raw['url'] == url and raw['source_id'] == 'datong'


@pytest.mark.parametrize('content', [
    '<nav>' + '招标公告网站导航信息。' * 30 + '</nav>',
    '<div class="div-article2"><script>伪正文脚本</script><style>伪正文样式</style></div>',
    '<nav><div class="div-article2">' + '导航中的公告栏目链接。' * 30 + '</div></nav>',
    '<footer><div class="cs_xq_content"><table class="bid_msgTable"><tr><td>'
    + '页脚中的公告栏目链接。' * 30 + '</td></tr></table></div></footer>',
])
def test_datong_no_verified_body_preserves_list_fields_without_inventing_content(content):
    url = NOTICE_URLS['datong']
    raw = {'url': url, 'title': '公开列表设备采购公告', 'published_at': None, 'source_id': 'datong'}
    body, title, date = adapter().read_detail(Pages({url: content}), raw, {'id': 'datong'})
    assert body == '' and title == '' and date is None
    assert raw['content_coverage'] == 'list_only' and raw['detail_status'] == '仅列表线索'
    assert raw['extraction_warning'] == '未取得可核实的详情正文，仅保留公开列表信息；其余信息未知，需核对来源原文。'
    assert raw['title'] == '公开列表设备采购公告' and raw['published_at'] is None
    assert raw['url'] == url and raw['source_id'] == 'datong'


def test_datong_short_body_uses_verified_container_and_excludes_navigation():
    url = NOTICE_URLS['datong']
    html = '<nav><div class="div-article2">错误导航正文</div></nav>'
    html += '<div class="div-article2">项目撤销。<nav>错误内嵌导航</nav><footer>错误页脚</footer></div>'
    raw = {'url': url}
    body, _, _ = adapter().read_detail(Pages({url: html}), raw, {'id': 'datong'})
    assert body == '项目撤销。' and raw['content_coverage'] == 'partial'


def test_datong_complete_body_does_not_override_coverage_defaults():
    url = NOTICE_URLS['datong']
    raw = {'url': url}
    body, _, _ = adapter().read_detail(Pages({url: '<div class="div-article2">' + '公开设备采购要求。' * 30 + '</div>'}), raw, {'id': 'datong'})
    assert len(body) >= 120 and raw == {'url': url}


@pytest.mark.parametrize('error', ['网络访问失败', '站点robots规则不允许访问', '页面要求访问验证'])
def test_datong_access_failure_still_raises_without_partial_metadata(error):
    class UnavailablePage:
        def get(self, url):
            raise SourceUnavailable(error)

    raw = {'url': NOTICE_URLS['datong']}
    with pytest.raises(SourceUnavailable, match=error):
        adapter().read_detail(UnavailablePage(), raw, {'id': 'datong'})
    assert raw == {'url': NOTICE_URLS['datong']}


def test_ccgp_short_body_still_requires_manual_reading():
    url = NOTICE_URLS['ccgp']
    with pytest.raises(SourceUnavailable, match='内容不足'):
        adapter().read_detail(Pages({url: '<div class="vF_detail_content">内容简短。</div>'}), {'url': url}, {'id': 'ccgp'})
