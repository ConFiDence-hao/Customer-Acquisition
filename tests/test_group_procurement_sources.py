"""Operators' own procurement boards: disclosed notice columns only, no supplier login."""
import pytest

from app import collectors
from app.collectors import PublicClient, SourceUnavailable
from app.db import DEFAULT_SETTINGS
from app import group_procurement_sources as boards

DG = boards.DYNAGREEN
SEC = boards.SZENERGY


class Pages:
    def __init__(self, pages):
        self.pages, self.calls, self.listing_warnings, self.listing_pages = pages, [], [], 0

    def get(self, url):
        self.calls.append(url)
        if url not in self.pages:
            raise SourceUnavailable('missing page')
        return self.pages[url]


def dg_list(column, rows):
    return '<div class="List2"><ul>' + ''.join(
        f'<li><a href="/lsdlcms/{column}/{key}.html" title="{title}">{title}</a>'
        f'<p><span class="Gray Right">发布时间：{date}</span></p></li>' for key, title, date in rows) + '</ul></div>'


def test_dynagreen_reads_each_disclosed_column_once_and_filters_titles():
    pages = {f'{DG}/lsdlcms/{column}/index.html': '<ul></ul>' for column in boards._DYNAGREEN_COLUMNS}
    pages[f'{DG}/lsdlcms/cmsfcaigougg/index.html'] = dg_list('cmsfcaigougg', [
        ('a' * 32, '平阳项目垃圾焚烧烟气CEMS系统运维服务采购', '2026-10-16'),
        ('b' * 32, '章丘公司食堂大米采购', '2026-10-15')]) + '<a href="/lsdl_front/outernetlogin">用户登录</a>'
    pages[f'{DG}/lsdlcms/cmszzhaobiaogg/index.html'] = dg_list('cmszzhaobiaogg', [
        ('c' * 32, '密云公司烟气深度治理系统升级改造工程的招标', '2026-09-28')])
    client = Pages(pages)
    items = boards.get_candidates(client, {'id': 'dynagreen'}, DEFAULT_SETTINGS)
    assert [x['title'] for x in items] == ['平阳项目垃圾焚烧烟气CEMS系统运维服务采购', '密云公司烟气深度治理系统升级改造工程的招标']
    assert items[0]['url'] == f'{DG}/lsdlcms/cmsfcaigougg/{"a" * 32}.html' and items[0]['published_at'] == '2026-10-16'
    assert sorted(client.calls) == sorted(pages) and not any('lsdl_front' in url for url in client.calls)


def test_dynagreen_detail_uses_notice_container():
    url = f'{DG}/lsdlcms/cmszzhaobiaogg/{"c" * 32}.html'
    html = ('<nav>网站首页 采购公告</nav><h2 class="con_tit">密云公司烟气深度治理系统升级改造工程的招标</h2>'
            '<div class="s_date"><span>发布时间： 2026/09/28 11:37:51</span></div><div id="TDContent"><table><tr>'
            '<td>项目名称：密云公司烟气深度治理系统升级改造工程的招标</td><td>招标人：北京绿色动力再生能源有限公司</td></tr>'
            '<tr><td>投标截止时间：2026-10-19 12:00</td></tr></table></div>')
    body, title, date = boards.read_detail(Pages({url: html}), {'url': url}, {'id': 'dynagreen'})
    assert title.startswith('密云公司') and date.startswith('2026/09/28') and '招标人' in body and '网站首页' not in body
    with pytest.raises(SourceUnavailable):
        boards.read_detail(Pages({url: '<h2 class="con_tit">标题</h2>'}), {'url': url}, {'id': 'dynagreen'})


def sec_list(column, page, rows, next_page=None):
    link = f'<a href="/{column}/index_{next_page}.jhtml">下一页</a><a href="/{column}/index_609.jhtml">尾页</a>' if next_page else ''
    return '<ul>' + ''.join(f'<li><a href="{path}">{title}</a><p class="Gray"><span>发布时间{date}</span></p></li>'
                            for path, title, date in rows) + '</ul>' + link


def test_szenergy_follows_next_page_within_window_and_skips_notices_pages():
    pages = {f'{SEC}/zbggs/index.jhtml': sec_list('zbggs', 1, [
                 ('/zbgg/21419.jhtml', '深圳市深能环保东部有限公司垃圾焚烧烟气净化系统药剂采购项目-招标公告', '2026-10-15 14:12:37'),
                 ('/tongzhi/16200.jhtml', '关于平台加强安全防护措施的公告烟气', '2026-01-01 00:00:00')], next_page=2),
             f'{SEC}/zbggs/index_2.jhtml': sec_list('zbggs', 2, [
                 ('/jggg/21398.jhtml', '化州深能环保有限公司垃圾焚烧炉节能技术研发项目结果公告', '2026-10-12 16:48:00')], next_page=3),
             f'{SEC}/yzbgg/index.jhtml': sec_list('yzbgg', 1, [])}
    for page in range(3, 7):
        pages[f'{SEC}/zbggs/index_{page}.jhtml'] = sec_list('zbggs', page, [], next_page=page + 1)
    client = Pages(pages)
    items = boards.get_candidates(client, {'id': 'szenergy'}, DEFAULT_SETTINGS)
    assert {x['url'].rsplit('/', 2)[-2] for x in items} == {'zbgg', 'jggg'}
    assert f'{SEC}/zbggs/index_6.jhtml' not in client.calls and f'{SEC}/zbggs/index_5.jhtml' in client.calls


@pytest.mark.parametrize('url', ['https://zb.sec.com.cn/tongzhi/1.jhtml', 'https://zb.sec.com.cn:8443/zbgg/1.jhtml',
                                 'http://zb.sec.com.cn/zbgg/1.jhtml', 'https://isp.sec.com.cn/zbgg/1.jhtml'])
def test_szenergy_detail_rejects_unverified_urls(url):
    with pytest.raises(SourceUnavailable):
        boards.read_detail(Pages({}), {'url': url}, {'id': 'szenergy'})


def test_registered_and_port_8000_allowed_only_for_dynagreen():
    sources = {x['id']: x for x in collectors.SOURCES}
    assert {'dynagreen', 'szenergy'} <= collectors.BUILTIN_SOURCE_IDS
    assert sources['dynagreen']['url'] == DG + '/lsdlcms/' and sources['szenergy']['enabled'] is True
    client = PublicClient()
    try:
        assert client.allowed(DG + '/lsdlcms/') is True  # origin publishes no robots file
        with pytest.raises(SourceUnavailable, match='白名单'):
            client._request('https://zb.sec.com.cn:8000/zbggs/index.jhtml')
    finally:
        client.close()
