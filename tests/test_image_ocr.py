"""OCR fallback keeps publisher images and failures distinct from HTML text."""
import base64
import importlib
import json
import subprocess
import types

import httpx
import pytest

from app.collectors import SourceUnavailable


PAGE = 'https://www.cseg.cn/index.php?a=shows&catid=15&id=301'
IMAGES = [
    'https://www.cseg.cn/d/file/content/2019/12/5e01ace80add3.jpg',
    'https://www.cseg.cn/d/file/content/2019/12/5e01ace87484a.jpg',
    'https://www.cseg.cn/d/file/content/2019/12/5e01ace8866f1.jpg',
]
HTML = '<nav><img src="/logo.jpg"></nav><div class="content-STRO">' + ''.join(
    '<img src="'+url.replace('https://www.cseg.cn', '')+'">' for url in IMAGES) + '</div>'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=')
TEXT = '垃圾焚烧发电项目设备采购公开招标公告。投标人应按公告要求提供资质材料，采购范围包括焚烧炉及其配套控制系统，报名、开标和投标截止时间均以原始公告图片为准。'


def module():
    return importlib.import_module('app.image_ocr')


class PublicImages:
    def __init__(self, values=None):
        self.calls = []
        self.values = values or {url: PNG for url in IMAGES}

    def get_bytes(self, url):
        self.calls.append(url)
        value = self.values[url]
        if isinstance(value, Exception):
            raise value
        return value


def backend(monkeypatch, payload):
    def run(command, **kwargs):
        assert '--worker' in command
        assert kwargs['timeout'] <= 30
        inputs = json.loads(kwargs['input'])
        assert all(base64.b64decode(value) == PNG for value in inputs['images'])
        return subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b'')
    monkeypatch.setattr(module().subprocess, 'run', run)


def test_image_only_article_reads_three_same_origin_images_in_page_order(monkeypatch):
    backend(monkeypatch, {'texts': [TEXT, TEXT, TEXT]})
    client = PublicImages()
    text, urls = module().extract_image_article(client, HTML, PAGE, 'cseg')
    assert urls == IMAGES and client.calls == IMAGES
    assert text.count(TEXT) == 3
    assert '图片OCR识别' in text and '核对原图' in text
    assert '/logo.jpg' not in text


@pytest.mark.parametrize('source,page', [
    ('bjx', PAGE), ('cseg', 'https://unverified.example/index.php?a=shows&catid=15&id=301'),
    ('cseg', 'https://www.cseg.cn/index.php?a=lists&catid=15'),
])
def test_other_sources_or_non_notice_pages_do_not_download_images(source, page):
    client = PublicImages({})
    assert module().extract_image_article(client, HTML, page, source) == ('', [])
    assert client.calls == []


def test_sufficient_html_body_does_not_invoke_ocr_or_network():
    client = PublicImages({})
    html = '<div class="content-STRO">'+TEXT*3+HTML+'</div>'
    assert module().extract_image_article(client, html, PAGE, 'cseg') == ('', [])
    assert client.calls == []


def test_images_outside_article_container_are_never_downloaded():
    client = PublicImages({})
    assert module().extract_image_article(client, '<nav>'+HTML+'</nav>', PAGE, 'cseg') == ('', [])
    assert client.calls == []


def test_duplicate_and_unsafe_images_never_expand_public_reads(monkeypatch):
    backend(monkeypatch, {'texts': [TEXT*2]})
    unsafe = ['https://other.example/d/file/content/2019/12/evil.jpg',
              'http://127.0.0.1/private.jpg', '/logo.jpg',
              'https://www.cseg.cn:8000/d/file/content/2019/12/evil.jpg',
              '/d/file/content/2019/12/evil.jpg?token=private',
              'file:///C:/private.jpg', 'data:image/png;base64,AAAA']
    html = '<div class="content">'+''.join('<img src="'+u+'">' for u in [IMAGES[0], IMAGES[0], *unsafe])+'</div>'
    client = PublicImages()
    text, urls = module().extract_image_article(client, html, PAGE, 'cseg')
    assert urls == [IMAGES[0]] and client.calls == [IMAGES[0]]
    assert TEXT in text


def test_image_limit_rejects_partial_article_before_any_download():
    html = '<div class="content-STRO">'+''.join('<img src="/d/file/content/2019/12/page'+str(i)+'.jpg">' for i in range(7))+'</div>'
    client = PublicImages()
    with pytest.raises(SourceUnavailable, match='图片.*上限'):
        module().extract_image_article(client, html, PAGE, 'cseg')
    assert client.calls == []


def test_oversized_image_fails_with_source_urls_and_no_later_reads(monkeypatch):
    monkeypatch.setattr(module(), 'MAX_IMAGE_BYTES', 16)
    client = PublicImages({IMAGES[0]: b'x'*17})
    with pytest.raises(SourceUnavailable, match='图片.*字节') as caught:
        module().extract_image_article(client, HTML, PAGE, 'cseg')
    assert caught.value.image_urls == IMAGES
    assert client.calls == [IMAGES[0]]


def test_cumulative_download_limit_never_returns_partial_ocr(monkeypatch):
    monkeypatch.setattr(module(), 'MAX_TOTAL_BYTES', len(PNG)*2-1)
    client = PublicImages()
    with pytest.raises(SourceUnavailable, match='图片.*总字节'):
        module().extract_image_article(client, HTML, PAGE, 'cseg')
    assert client.calls == IMAGES[:2]


def test_restricted_image_fails_without_bypass_or_later_requests():
    client = PublicImages({IMAGES[0]: SourceUnavailable('robots规则不允许')})
    with pytest.raises(SourceUnavailable, match='图片OCR识别失败') as caught:
        module().extract_image_article(client, HTML, PAGE, 'cseg')
    assert caught.value.image_urls == IMAGES and client.calls == IMAGES[:1]


@pytest.mark.parametrize('payload,message', [
    ({'error': 'not_ready'}, 'OCR组件未就绪'),
    ({'error': 'failed'}, '图片OCR识别失败'),
    ({'texts': ['', '', '']}, '图片OCR识别失败'),
    ({'texts': [TEXT, '', TEXT]}, '图片OCR识别失败'),
    ({'texts': ['短文本', '短文本', '短文本']}, '图片OCR识别失败'),
])
def test_unready_failed_or_incomplete_ocr_never_becomes_successful_body(monkeypatch, payload, message):
    backend(monkeypatch, payload)
    with pytest.raises(SourceUnavailable, match=message) as caught:
        module().extract_image_article(PublicImages(), HTML, PAGE, 'cseg')
    assert caught.value.image_urls == IMAGES


def test_subprocess_timeout_is_reported_with_image_sources(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
    monkeypatch.setattr(module().subprocess, 'run', timeout)
    with pytest.raises(SourceUnavailable, match='图片OCR识别失败.*超时') as caught:
        module().extract_image_article(PublicImages(), HTML, PAGE, 'cseg')
    assert caught.value.image_urls == IMAGES


@pytest.mark.parametrize('stdout', [b'', b'not-json', b'{"texts":[1,2,3]}'])
def test_worker_output_protocol_errors_remain_recognizable_ocr_failures(monkeypatch, stdout):
    monkeypatch.setattr(module().subprocess, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout, b''))
    with pytest.raises(SourceUnavailable, match='图片OCR识别失败') as caught:
        module().extract_image_article(PublicImages(), HTML, PAGE, 'cseg')
    assert caught.value.image_urls == IMAGES


def test_article_time_budget_stops_further_image_reads(monkeypatch):
    ticks = iter([0.0, 0.0, 46.0])
    monkeypatch.setattr(module().time, 'monotonic', lambda: next(ticks))
    client = PublicImages()
    with pytest.raises(SourceUnavailable, match='图片OCR识别失败.*超时'):
        module().extract_image_article(client, HTML, PAGE, 'cseg')
    assert client.calls == IMAGES[:1]


def test_access_verification_is_not_processed_as_an_image_article():
    client = PublicImages()
    with pytest.raises(SourceUnavailable, match='访问验证'):
        module().extract_image_article(client, 'captcha-container'+HTML, PAGE, 'cseg')
    assert client.calls == []


def test_ocr_result_keeps_reading_order_and_filters_low_confidence(monkeypatch):
    class Image:
        size = (800, 1200)
        format = 'PNG'
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def load(self): pass
        def convert(self, mode): return self
    class Engine:
        def __call__(self, image):
            return ([([0, 0], '第一行中文', .99), ([0, 0], '不可靠内容', .2), ([0, 0], '第二行中文', .8)], [0.1, 0.1, 0.1])
    fake = types.ModuleType('rapidocr_onnxruntime')
    fake.RapidOCR = lambda **kwargs: Engine()
    pil = types.ModuleType('PIL')
    pil.Image = types.SimpleNamespace(open=lambda data: Image())
    monkeypatch.setitem(__import__('sys').modules, 'rapidocr_onnxruntime', fake)
    monkeypatch.setitem(__import__('sys').modules, 'PIL', pil)
    assert module()._recognize_payloads([PNG]) == ['第一行中文\n第二行中文']


def test_excessive_pixel_dimensions_are_rejected_before_decoding(monkeypatch):
    class Image:
        size = (100000, 100000)
        format = 'PNG'
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def load(self): raise AssertionError('oversized pixels must not be decoded')
    fake = types.ModuleType('rapidocr_onnxruntime')
    fake.RapidOCR = lambda **kwargs: lambda image: ([], [])
    pil = types.ModuleType('PIL')
    pil.Image = types.SimpleNamespace(open=lambda data: Image())
    monkeypatch.setitem(__import__('sys').modules, 'rapidocr_onnxruntime', fake)
    monkeypatch.setitem(__import__('sys').modules, 'PIL', pil)
    with pytest.raises(ValueError, match='像素'):
        module()._recognize_payloads([PNG])


BJX_PAGE = 'https://huanbao.bjx.com.cn/news/20261014/1516189.shtml'
BJX_IMAGE = 'https://img01.mybjx.net/news/UploadFile/202610/89435c10e1c947c3827932da3b4ad55c.png'
BJX_PUBLIC_TEXT = '北极星公开正文原文。' * 10


@pytest.mark.parametrize('source,container', [('bjx', 'class="cc-article"'), ('gfcl', 'id="article"')])
def test_bjx_short_public_body_is_preserved_with_verified_image_ocr(monkeypatch, source, container):
    backend(monkeypatch, {'texts': [TEXT * 2]})
    html = '<nav><img src="https://img01.mybjx.net/logo.png"></nav><div '+container+'><p>'+BJX_PUBLIC_TEXT+'</p><img src="'+BJX_IMAGE+'"></div>'
    client = PublicImages({BJX_IMAGE: PNG})
    content, urls = module().extract_image_article(client, html, BJX_PAGE, source)
    assert urls == [BJX_IMAGE] and client.calls == [BJX_IMAGE]
    assert BJX_PUBLIC_TEXT in content and TEXT * 2 in content
    assert content.index(BJX_PUBLIC_TEXT) < content.index(TEXT)
    assert '图片OCR识别正文' in content and '核对原图' in content
    assert 'logo.png' not in content


def test_bjx_image_reads_reject_other_hosts_paths_queries_and_duplicate_sources(monkeypatch):
    backend(monkeypatch, {'texts': [TEXT * 2]})
    unsafe = [
        BJX_IMAGE.replace('img01.mybjx.net', 'unverified.example'),
        BJX_IMAGE.replace('img01.mybjx.net', 'img01.mybjx.net.unverified.example'),
        BJX_IMAGE.replace('https://', 'http://'),
        BJX_IMAGE.replace('.net/', '.net:8000/'),
        BJX_IMAGE + '?token=private',
        BJX_IMAGE + '#fragment',
        BJX_IMAGE.replace('/news/UploadFile/', '/other/UploadFile/'),
        BJX_IMAGE.replace('/202610/', '/202613/'),
        BJX_IMAGE.replace('.png', '.pdf'),
        'https://user:pass@img01.mybjx.net/news/UploadFile/202610/private.png',
        IMAGES[0],
    ]
    html = '<div class="cc-article">'+''.join('<img src="'+url+'">' for url in [BJX_IMAGE, BJX_IMAGE, *unsafe])+'</div>'
    client = PublicImages({BJX_IMAGE: PNG})
    content, urls = module().extract_image_article(client, html, BJX_PAGE, 'bjx')
    assert urls == [BJX_IMAGE] and client.calls == [BJX_IMAGE]
    assert TEXT in content


@pytest.mark.parametrize('source,page', [
    ('cseg', BJX_PAGE),
    ('other', BJX_PAGE),
    ('bjx', BJX_PAGE.replace('huanbao.bjx.com.cn', 'unverified.example')),
    ('gfcl', BJX_PAGE.replace('/news/', '/other/')),
    ('bjx', BJX_PAGE + '?token=private'),
    ('bjx', BJX_PAGE + '#fragment'),
    ('bjx', BJX_PAGE.replace('.cn/', '.cn:8000/')),
    ('bjx', BJX_PAGE.replace('https://', 'http://')),
])
def test_unverified_bjx_article_pages_never_download_images(source, page):
    html = '<div class="cc-article"><img src="'+BJX_IMAGE+'"></div>'
    client = PublicImages({BJX_IMAGE: PNG})
    assert module().extract_image_article(client, html, page, source) == ('', [])
    assert client.calls == []


def test_bjx_already_readable_text_and_images_outside_body_do_not_use_ocr():
    client = PublicImages({BJX_IMAGE: PNG})
    for html in [
        '<div class="cc-article">'+TEXT*3+'<img src="'+BJX_IMAGE+'"></div>',
        '<nav><div id="article"><img src="'+BJX_IMAGE+'"></div></nav>',
        '<div class="cc-article">简短正文</div><footer><img src="'+BJX_IMAGE+'"></footer>',
    ]:
        assert module().extract_image_article(client, html, BJX_PAGE, 'bjx') == ('', [])
    assert client.calls == []


def test_bjx_image_budget_rejects_more_than_six_images_before_download():
    html = '<div class="cc-article">'+''.join(
        '<img src="https://img01.mybjx.net/news/UploadFile/202610/page'+str(i)+'.jpg">' for i in range(7))+'</div>'
    client = PublicImages({BJX_IMAGE: PNG})
    with pytest.raises(SourceUnavailable, match='图片.*上限'):
        module().extract_image_article(client, html, BJX_PAGE, 'gfcl')
    assert client.calls == []


def test_bjx_ocr_failure_keeps_image_sources_and_never_publishes_partial_body(monkeypatch):
    backend(monkeypatch, {'texts': ['短文本']})
    html = '<div class="cc-article"><p>'+BJX_PUBLIC_TEXT+'</p><img src="'+BJX_IMAGE+'"></div>'
    with pytest.raises(SourceUnavailable, match='图片OCR识别失败') as caught:
        module().extract_image_article(PublicImages({BJX_IMAGE: PNG}), html, BJX_PAGE, 'bjx')
    assert caught.value.image_urls == [BJX_IMAGE]
    assert caught.value.acquisition_method == 'image_ocr'
