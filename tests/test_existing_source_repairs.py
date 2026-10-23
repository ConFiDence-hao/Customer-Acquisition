from pathlib import Path

import pytest

from app import collectors

FIXTURES = Path(__file__).parent / 'fixtures'


def test_cseg_public_notice_uses_actual_article_container_and_keeps_body_dates_out_of_metadata():
    html = (FIXTURES / 'cseg_public_notice.html').read_text(encoding='utf-8')
    body, title, date = collectors.clean_text(html, 'cseg')
    assert '喷雾塔顶板' in body and len(body) > 500
    assert '导航' not in body and '友情链接' not in body
    assert title == '#1机组烟气处理系统及工业水系统改造比选公告'
    assert date is None  # The tender document contains submission dates, not a published timestamp.


def test_everbright_html_notice_does_not_require_a_pdf():
    html = (FIXTURES / 'everbright_html_notice.html').read_text(encoding='utf-8')
    class NoPdfRequests:
        def _request(self, *args, **kwargs):
            pytest.fail('HTML notice must not request a nonexistent PDF')
    body, title, date = collectors.read_everbright_detail(NoPdfRequests(), html)
    assert '供应商管理办法' in body and '限制期满' in body
    assert '导航' not in body and '相关公告' not in body
    assert date is None


def test_everbright_pdf_notices_still_use_the_public_attachment(monkeypatch):
    calls = []
    monkeypatch.setattr(collectors, 'read_pdf_attachment', lambda client, html: calls.append(html) or 'PDF正文')
    html = '<h3>[采购公告]垃圾焚烧厂烟气改造公开采购公告</h3><iframe id="pdfContainer" src="/viewer?file=x"></iframe>'
    body, title, date = collectors.read_everbright_detail(object(), html)
    assert body == 'PDF正文' and len(calls) == 1
    assert title == '垃圾焚烧厂烟气改造公开采购公告'


def test_missing_everbright_article_is_never_replaced_with_navigation():
    with pytest.raises(collectors.SourceUnavailable):
        collectors.read_everbright_detail(object(), '<div id="main">' + '平台导航和其他公告' * 30 + '</div>')


def test_valid_public_list_with_no_matching_keywords_is_not_a_source_failure():
    class Listing:
        def get(self, url):
            return '<li><a href="/news/20261013/1234.shtml">办公家具采购公开招标公告</a><time>2026-10-13</time></li>'
    assert collectors.html_candidates(Listing(), {'id': 'bjx', 'url': 'https://ljfd.bjx.com.cn/zb/'},
                                      {'exclude_keywords': ['办公家具'], 'focus_keywords': []}) == []


def test_list_structure_failure_is_reported_even_when_no_items_match():
    class BrokenListing:
        def get(self, url):
            return '<body>网站升级维护中</body>'
    with pytest.raises(collectors.SourceUnavailable):
        collectors.html_candidates(BrokenListing(), {'id': 'bjx', 'url': 'https://ljfd.bjx.com.cn/zb/'},
                                    {'exclude_keywords': [], 'focus_keywords': []})


def test_scanned_documents_do_not_stop_later_readable_documents(tmp_path, monkeypatch):
    import json
    from app import db, procurement_sources
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(db, 'ROOT', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'testing-only-password')
    db.initialize()
    source = next(item for item in collectors.SOURCES if item['id'] == 'bece')
    with db.connect() as c:
        c.execute('INSERT INTO sources(id,config) VALUES(?,?)', (source['id'], db.dumps(source)))
    monkeypatch.setattr(procurement_sources, 'get_candidates', lambda *args: [
        {'title': '垃圾焚烧烟气改造公告' + str(i), 'url': source['url'] + 'notice-' + str(i),
         'published_at': '2026-10-13'} for i in range(5)])
    seen = []
    def detail(client, raw, config):
        seen.append(raw['url'])
        if len(seen) <= 4:
            raise collectors.SourceUnavailable('扫描PDF无可提取正文，需人工阅读')
        return '可公开读取的烟气改造项目正文。' * 20, '', None
    monkeypatch.setattr(procurement_sources, 'read_detail', detail)
    class NoNetwork:
        def close(self): pass
    monkeypatch.setattr(collectors, 'PublicClient', NoNetwork)
    result = collectors.collect_source(source, [], db.DEFAULT_SETTINGS)
    assert len(seen) == 5 and result['restricted'] == 4
    assert result['inserted'] == 5
    with db.connect() as c:
        rows = [json.loads(row[0]) for row in c.execute('SELECT payload FROM opportunities ORDER BY id')]
    assert rows[-1]['detail_status'] == '正文可读'
    assert all(row['detail_status'] == '仅列表线索' for row in rows[:-1])


def test_damaged_pdf_is_a_notice_limitation_instead_of_stopping_the_source():
    class DamagedPdf:
        def allowed(self, url): return True
        def _request(self, url):
            class Response:
                content = b'%PDF-1.7\ntruncated public attachment'
            return Response()
    with pytest.raises(collectors.SourceUnavailable, match='PDF文件无法解析'):
        collectors.read_pdf_attachment(DamagedPdf(), '<iframe id="pdfContainer" src="/viewer?file=https://www.cseg.cn/public.pdf"></iframe>')
