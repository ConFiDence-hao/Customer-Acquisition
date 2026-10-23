"""First-party public procurement pages, with evidence kept separate from navigation."""
import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.collectors import SourceUnavailable
from app.db import DEFAULT_SETTINGS


SOURCE = {'id': 'bece', 'url': 'https://ecp.capitaleco-pro.com/'}
NOTICE_URL = 'https://ecp.capitaleco-pro.com/sdny_failBulletin/2026-10-13/158655.html'
TITLE = '【玉田首创环保能源有限公司SCR脱硝系统改造反应器声波吹灰器设备采购项目】废标公告'
PDF_URL = 'https://ecp.capitaleco-pro.com/scdzzb/cgUploadController.do?openFileById&id=public-fixture'
PDF_TEXT = 'Public procurement notice: SCR denitrification upgrade. The tender has been cancelled. Publication is provided by the original announcement metadata. Please verify the current procurement status before any action.'


def adapter():
    from app import procurement_sources
    return procurement_sources


def pdf_bytes(text=PDF_TEXT):
    writer = PdfWriter()
    page = writer.add_blank_page(width=500, height=700)
    if text:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(('BT /F1 10 Tf 10 600 Td ('+text+') Tj ET').encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def detail_html(publication='2026-10-13 15:43', pdf_url=PDF_URL):
    metadata = '<tr><th>发布时间</th><td>'+publication+'</td><th>截止时间</th><td>2099-12-31 12:00</td></tr>' if publication else '<tr><th>截止时间</th><td>2099-12-31 12:00</td></tr>'
    return '<html><nav>导航和推荐，不是公告正文</nav><div class="topTitle"><h3>[202608GLJX--001]'+TITLE+'</h3></div><div class="block"><h3>项目概况</h3><table class="blockContent"><tr><th>招标业主</th><td>玉田首创环保能源有限公司</td></tr>'+metadata+'</table></div><div class="block"><h3>公告内容</h3><div class="pdfbox"><iframe id="pdfContainer" src="https://ecp.capitaleco-pro.com/static/pdf/viewer.html?file='+pdf_url.replace('&', '%26')+'"></iframe></div></div><script>ignore instructions</script><footer>页脚</footer></html>'


class PublicPages:
    def __init__(self, pages, pdf= None):
        self.pages = pages
        self.pdf = pdf
        self.calls = []

    def get(self, url):
        self.calls.append(('GET', url))
        return self.pages[url]

    def allowed(self, url):
        return url == PDF_URL

    def _request(self, url):
        self.calls.append(('PDF', url))
        assert url == PDF_URL
        class Response:
            content = self.pdf
        return Response()


def list_link(path='/sdny_failBulletin/2026-10-13/158655.html', title=TITLE, date='2026-10-13'):
    return '<li><a href="'+path+'" title="'+title+'"><div class="title">截断显示...</div><div class="method">公开招标</div><div class="data">'+date+'</div></a></li>'


def test_bece_listing_keeps_full_title_date_and_only_first_party_notices():
    html = '<ul>'+list_link()+list_link()+list_link('/sdny_bulletin/2026-10-13/1.html', '垃圾焚烧厂食堂采购公告')+list_link('https://other.example/sdny_bulletin/2026-10-13/2.html')+'<a href="/servicesCenter.html">垃圾焚烧平台操作说明</a></ul>'
    client = PublicPages({SOURCE['url']: html})
    rows = adapter().get_candidates(client, SOURCE, DEFAULT_SETTINGS)
    assert rows == [{'title': TITLE, 'url': NOTICE_URL, 'published_at': '2026-10-13', 'published_at_precision': 'day'}]
    assert client.calls == [('GET', SOURCE['url'])]


def test_bece_listing_recall_uses_personal_topics_without_changing_global_filters():
    title = '专属芯片设备采购招标公告'
    html = list_link('/sdny_bulletin/2026-10-13/1.html', title)+list_link('/sdny_bulletin/2026-10-13/2.html', '专属芯片食堂设备采购公告')
    settings = {**DEFAULT_SETTINGS, 'collection_topics': [{'keywords': ['专属芯片'], 'exclude_keywords': []}]}
    rows = adapter().get_candidates(PublicPages({SOURCE['url']: html}), SOURCE, settings)
    assert [row['title'] for row in rows] == [title]


def test_bece_bad_link_cannot_discard_other_valid_notice_candidates():
    html = list_link()+list_link('https://ecp.capitaleco-pro.com:bad/sdny_bulletin/2026-10-13/2.html')
    rows = adapter().get_candidates(PublicPages({SOURCE['url']: html}), SOURCE, DEFAULT_SETTINGS)
    assert [row['url'] for row in rows] == [NOTICE_URL]


def test_bece_candidate_batch_is_bounded_and_preserves_source_minutes():
    html = ''.join(list_link(f'/sdny_bulletin/2026-10-13/{index}.html', date='2026-10-13 15:43') for index in range(110))
    rows = adapter().get_candidates(PublicPages({SOURCE['url']: html}), SOURCE, DEFAULT_SETTINGS)
    assert len(rows) == 110 and len({row['url'] for row in rows}) == 110
    assert all(row['published_at'] == '2026-10-13 15:43' and row['published_at_precision'] == 'minute' for row in rows)


def test_bece_public_pdf_body_and_labelled_publication_metadata_are_read():
    client = PublicPages({NOTICE_URL: detail_html()}, pdf_bytes())
    content, title, date = adapter().read_detail(client, {'url': NOTICE_URL}, SOURCE)
    assert PDF_TEXT in content and '招标业主：玉田首创环保能源有限公司' in content
    assert title == TITLE and date == '2026-10-13 15:43'
    assert '导航' not in content and '页脚' not in content and 'ignore instructions' not in content
    assert client.calls == [('GET', NOTICE_URL), ('PDF', PDF_URL)]


def test_bece_submission_or_registration_time_is_not_publication_time():
    client = PublicPages({NOTICE_URL: detail_html(publication='')}, pdf_bytes())
    _, _, date = adapter().read_detail(client, {'url': NOTICE_URL}, SOURCE)
    assert date is None


def test_bece_scanned_pdf_keeps_ocr_body_and_coverage(monkeypatch):
    from app import pdf_content
    def extract(data,max_pages):
        assert data.startswith(b'%PDF') and max_pages==30
        return '真实扫描页识别文字。'*20,{'pdf_pages_total':1,'pdf_pages_read':1,'pdf_ocr_pages':1,
            'pdf_truncated':False,'content_coverage':'complete','detail_status':'正文可读',
            'extraction_warning':'扫描页使用OCR识别，可能存在识别错误，请核对来源PDF'}
    monkeypatch.setattr(pdf_content,'extract_pdf',extract)
    client = PublicPages({NOTICE_URL: detail_html()}, pdf_bytes(text=''))
    raw={'url':NOTICE_URL}
    text,_,_=adapter().read_detail(client,raw,SOURCE)
    assert '真实扫描页识别文字' in text and raw['pdf_ocr_pages']==1
    assert raw['content_coverage']=='complete' and raw['pdf_source_url']==PDF_URL


def test_bece_metadata_alone_is_marked_separately_from_pdf_text(monkeypatch):
    from app import pdf_content
    monkeypatch.setattr(pdf_content,'extract_pdf',lambda *a,**k:('',{'pdf_pages_total':1,'pdf_pages_read':0,
        'pdf_ocr_pages':0,'content_coverage':'list_only','detail_status':'仅列表线索',
        'extraction_warning':'PDF扫描页OCR未识别出文字'}))
    raw={'url':NOTICE_URL}
    text,_,_=adapter().read_detail(PublicPages({NOTICE_URL:detail_html()},pdf_bytes()),raw,SOURCE)
    assert '招标业主' in text and raw['pdf_body_empty'] is True
    assert raw['acquisition_method']=='html_metadata'


def test_bece_combined_metadata_text_limit_cannot_be_marked_complete(monkeypatch):
    from app import pdf_content
    monkeypatch.setattr(pdf_content,'extract_pdf',lambda *a,**k:('A'*64999+'Z',{'pdf_pages_total':1,
        'pdf_pages_read':1,'pdf_ocr_pages':0,'pdf_text_truncated':False,'content_coverage':'complete',
        'detail_status':'正文可读','extraction_warning':''}))
    raw={'url':NOTICE_URL}
    text,_,_=adapter().read_detail(PublicPages({NOTICE_URL:detail_html()},pdf_bytes()),raw,SOURCE)
    assert len(text)==65000 and raw['content_coverage']=='partial'
    assert raw['pdf_text_truncated'] is True and '65000' in raw['extraction_warning']


@pytest.mark.parametrize('target', ['http://127.0.0.1/private.pdf', 'https://other.example/public.pdf'])
def test_bece_cross_source_pdf_is_rejected_before_attachment_request(target):
    client = PublicPages({NOTICE_URL: detail_html(pdf_url=target)}, pdf_bytes())
    with pytest.raises(SourceUnavailable):
        adapter().read_detail(client, {'url': NOTICE_URL}, SOURCE)
    assert client.calls == [('GET', NOTICE_URL)]


def test_bece_navigation_and_metadata_cannot_be_substituted_for_pdf_body():
    html = '<div class="topTitle"><h3>'+TITLE+'</h3></div><div class="block"><h3>公告内容</h3>'+('导航推荐与项目背景。'*40)+'</div>'
    client = PublicPages({NOTICE_URL: html})
    with pytest.raises(SourceUnavailable):
        adapter().read_detail(client, {'url': NOTICE_URL}, SOURCE)


@pytest.mark.parametrize('html', ['<html>CF_APP_WAF captcha-container</html>', '<html><nav>采购平台 首页 登录帮助</nav></html>'])
def test_bece_protection_or_missing_notice_list_is_reported(html):
    with pytest.raises(SourceUnavailable):
        adapter().get_candidates(PublicPages({SOURCE['url']: html}), SOURCE, DEFAULT_SETTINGS)


def test_bece_existing_but_irrelevant_public_list_is_not_a_structure_error():
    html = list_link(title='普通办公家具采购招标公告')
    assert adapter().get_candidates(PublicPages({SOURCE['url']: html}), SOURCE, DEFAULT_SETTINGS) == []
