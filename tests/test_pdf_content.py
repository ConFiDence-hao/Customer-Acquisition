"""Bounded PDF extraction keeps completed pages when later pages fail."""
import importlib
import io
import json
import subprocess
import sys
import time
import types

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def module():
    return importlib.import_module('app.pdf_content')


def pdf_bytes(texts):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=500, height=700)
        if text:
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                     NameObject('/Subtype'): NameObject('/Type1'),
                                     NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({
                NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(('BT /F1 10 Tf 10 600 Td (' + text + ') Tj ET').encode('ascii'))
            page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def in_process(monkeypatch, recognitions):
    mod = module()
    calls = []

    class OCR:
        def __init__(self, data):
            calls.append('init')

        def recognize(self, page_index):
            calls.append(page_index)
            result = recognitions[page_index]
            if isinstance(result, Exception):
                raise result
            return result

        def close(self):
            calls.append('close')

    monkeypatch.setattr(mod, '_PageOCR', OCR)

    def run(data, max_pages):
        events = []
        mod._extract_pages(data, max_pages, events.append)
        return b''.join(json.dumps(event).encode('utf-8') + b'\n' for event in events), False, 0

    monkeypatch.setattr(mod, '_run_worker', run)
    return mod, calls


def test_native_pdf_extracts_real_text_in_subprocess():
    text, meta = module().extract_pdf(pdf_bytes(['Public procurement notice', 'Second page']))
    assert 'Public procurement notice' in text and 'Second page' in text
    assert meta['pdf_pages_total'] == 2
    assert meta['pdf_pages_read'] == 2
    assert meta['pdf_ocr_pages'] == 0
    assert meta['pdf_failed_pages'] == []
    assert meta['content_coverage'] == 'complete'
    assert meta['detail_status'] == '正文可读'
    assert not meta['pdf_truncated']


def test_31_page_pdf_reads_only_first_30_even_if_caller_requests_more(monkeypatch):
    mod, calls = in_process(monkeypatch, {})
    text, meta = mod.extract_pdf(pdf_bytes([f'Page {i:02d}' for i in range(1, 32)]), max_pages=99)
    assert 'Page 30' in text and 'Page 31' not in text
    assert meta['pdf_pages_total'] == 31 and meta['pdf_pages_read'] == 30
    assert meta['pdf_truncated'] is True
    assert meta['content_coverage'] == 'partial' and meta['detail_status'] == '正文不完整'
    assert '前30页' in meta['extraction_warning']
    assert not any(isinstance(call, int) for call in calls)


def test_smaller_requested_page_budget_is_respected(monkeypatch):
    mod, _ = in_process(monkeypatch, {})
    text, meta = mod.extract_pdf(pdf_bytes(['First', 'Second']), max_pages=1)
    assert 'First' in text and 'Second' not in text
    assert meta['pdf_pages_read'] == 1 and '前1页' in meta['extraction_warning']


def test_mixed_native_and_scan_pages_use_one_ocr_instance(monkeypatch):
    mod, calls = in_process(monkeypatch, {1: '扫描页采购范围', 2: '扫描页投标截止时间'})
    text, meta = mod.extract_pdf(pdf_bytes(['Native public notice', None, None]))
    assert 'Native public notice' in text and '扫描页采购范围' in text
    assert calls == ['init', 1, 2, 'close']
    assert meta['pdf_pages_read'] == 3 and meta['pdf_ocr_pages'] == 2
    assert meta['pdf_ocr_page_numbers'] == [2, 3]
    assert meta['content_coverage'] == 'complete'
    assert 'OCR' in meta['extraction_warning']


def test_failed_scan_does_not_discard_native_or_later_success(monkeypatch):
    mod, calls = in_process(monkeypatch, {1: RuntimeError('backend failed'), 2: '后续扫描页正文'})
    text, meta = mod.extract_pdf(pdf_bytes(['Native notice', None, None]))
    assert 'Native notice' in text and '后续扫描页正文' in text
    assert meta['pdf_pages_read'] == 2 and meta['pdf_failed_pages'] == [2]
    assert meta['pdf_ocr_pages'] == 1
    assert meta['content_coverage'] == 'partial' and meta['detail_status'] == '正文不完整'
    assert '第2页' in meta['extraction_warning'] and '识别失败' in meta['extraction_warning']
    assert calls == ['init', 1, 2, 'close']


def test_wholly_empty_ocr_has_diagnosis_and_no_fabricated_text(monkeypatch):
    mod, _ = in_process(monkeypatch, {0: ''})
    text, meta = mod.extract_pdf(pdf_bytes([None]))
    assert text == ''
    assert meta['pdf_pages_total'] == 1 and meta['pdf_pages_read'] == 0
    assert meta['pdf_failed_pages'] == [1]
    assert meta['content_coverage'] == 'list_only' and meta['detail_status'] == '仅列表线索'
    assert '未识别出文字' in meta['extraction_warning']


def test_missing_ocr_runtime_preserves_native_page_and_reason(monkeypatch):
    mod, _ = in_process(monkeypatch, {1: ModuleNotFoundError('pypdfium2')})
    text, meta = mod.extract_pdf(pdf_bytes(['Native', None]))
    assert text == 'Native'
    assert 'OCR组件未就绪' in meta['extraction_warning']
    assert meta['pdf_failed_pages'] == [2]


def test_text_limit_is_marked_and_later_pages_not_ocrd(monkeypatch):
    mod, calls = in_process(monkeypatch, {0: '文' * 66000, 1: 'second'})
    text, meta = mod.extract_pdf(pdf_bytes([None, None]))
    assert len(text) == 65000
    assert meta['pdf_text_truncated'] is True and meta['pdf_truncated'] is True
    assert meta['content_coverage'] == 'partial'
    assert '65000' in meta['extraction_warning']
    assert calls == ['init', 0, 'close']


def test_real_timeout_keeps_flushed_completed_page(monkeypatch):
    mod = module()
    events = [{'event': 'document', 'pages_total': 2},
              {'event': 'page', 'page': 1, 'text': 'Completed before timeout', 'method': 'native'}]
    script = 'import sys,time;sys.stdout.write(' + repr(''.join(json.dumps(e) + '\n' for e in events)) + ');sys.stdout.flush();time.sleep(5)'
    monkeypatch.setattr(mod, '_worker_command', lambda: [sys.executable, '-B', '-c', script])
    monkeypatch.setattr(mod, 'PDF_TIMEOUT', 0.7)
    start = time.monotonic()
    text, meta = mod.extract_pdf(pdf_bytes(['First', 'Second']))
    assert time.monotonic() - start < 3
    assert text == 'Completed before timeout'
    assert meta['pdf_pages_total'] == 2 and meta['pdf_pages_read'] == 1
    assert meta['pdf_timed_out'] is True and meta['content_coverage'] == 'partial'
    assert '超时' in meta['extraction_warning']


def test_invalid_pdf_has_unknown_page_total_and_real_diagnosis():
    mod = module()
    with pytest.raises(ValueError, match='PDF文件无法解析'):
        mod.extract_pdf(b'%PDF-1.7\ninvalid')


@pytest.mark.parametrize('data', [b'', b'not a pdf', b'%PDF-' + b'0' * 20_000_000], ids=['empty', 'not-pdf', 'over-20mb'])
def test_invalid_or_oversized_input_is_rejected_before_subprocess(monkeypatch, data):
    mod = module()
    monkeypatch.setattr(mod, '_run_worker', lambda *args: pytest.fail('worker must not start'))
    with pytest.raises(ValueError, match='PDF文件无法解析|PDF输入超过20MB'):
        mod.extract_pdf(data)


def test_20mb_input_boundary_is_allowed_and_worker_request_stays_under_32mb(monkeypatch):
    mod = module()
    payload = pdf_bytes(['Notice'])
    payload += b' ' * (20_000_000 - len(payload))
    captured = []

    class Process:
        returncode = 0

        def communicate(self, input=None, timeout=None):
            captured.append((len(input), timeout))
            return b'{"event":"document","pages_total":1}\n{"event":"page","page":1,"text":"Notice","method":"native"}\n{"event":"done"}\n', None

    monkeypatch.setattr(mod.subprocess, 'Popen', lambda *args, **kwargs: Process())
    text, meta = mod.extract_pdf(payload)
    assert text == 'Notice' and meta['content_coverage'] == 'complete'
    assert captured[0][0] < 32_000_000 and captured[0][1] == 90.0


def test_ocr_engine_is_loaded_once_and_rendered_resources_are_closed(monkeypatch):
    mod = module()
    calls = []

    class Image:
        size = (1000, 1400)
        def convert(self, mode):
            return self
        def close(self):
            calls.append('image_close')

    class Bitmap:
        def to_pil(self):
            return Image()
        def close(self):
            calls.append('bitmap_close')

    class Page:
        def get_size(self):
            return (500, 700)
        def render(self, **kwargs):
            assert kwargs['scale'] == 2.0
            return Bitmap()
        def close(self):
            calls.append('page_close')

    class Document:
        def __init__(self, data):
            calls.append('document_init')
        def __getitem__(self, index):
            return Page()
        def close(self):
            calls.append('document_close')

    def engine_factory():
        calls.append('engine_init')
        return lambda image: ([[None, '公开招标公告', 0.98], [None, '低置信', 0.1]], None)

    monkeypatch.setitem(sys.modules, 'pypdfium2', types.SimpleNamespace(PdfDocument=Document))
    monkeypatch.setattr(mod, '_make_ocr_engine', engine_factory)
    reader = mod._PageOCR(b'pdf')
    try:
        assert reader.recognize(0) == '公开招标公告'
        assert reader.recognize(1) == '公开招标公告'
    finally:
        reader.close()
    assert calls.count('engine_init') == 1 and calls.count('document_init') == 1
    assert calls.count('page_close') == 2 and calls.count('bitmap_close') == 2
    assert calls.count('document_close') == 1


def test_huge_page_is_rejected_before_pixel_allocation(monkeypatch):
    mod = module()
    closed = []
    class Page:
        def get_size(self):
            return (100_000, 100_000)
        def render(self, **kwargs):
            pytest.fail('oversized bitmap must not be allocated')
        def close(self):
            closed.append(True)
    class Document:
        def __init__(self, data): pass
        def __getitem__(self, index): return Page()
        def close(self): pass
    monkeypatch.setitem(sys.modules, 'pypdfium2', types.SimpleNamespace(PdfDocument=Document))
    reader = mod._PageOCR(b'pdf')
    with pytest.raises(ValueError, match='像素'):
        reader.recognize(0)
    reader.close()
    assert closed == [True]


def test_failed_ocr_engine_initialization_is_not_repeated(monkeypatch):
    mod = module()
    calls = []
    class Image:
        size = (1000, 1400)
        def convert(self, mode): return self
        def close(self): pass
    class Bitmap:
        def to_pil(self): return Image()
        def close(self): pass
    class Page:
        def get_size(self): return (500, 700)
        def render(self, **kwargs): return Bitmap()
        def close(self): pass
    class Document:
        def __init__(self, data): pass
        def __getitem__(self, index): return Page()
        def close(self): pass
    def unavailable():
        calls.append('init')
        raise ModuleNotFoundError('OCR models unavailable')
    monkeypatch.setitem(sys.modules, 'pypdfium2', types.SimpleNamespace(PdfDocument=Document))
    monkeypatch.setattr(mod, '_make_ocr_engine', unavailable)
    reader = mod._PageOCR(b'pdf')
    try:
        for index in range(2):
            with pytest.raises(ModuleNotFoundError):
                reader.recognize(index)
    finally:
        reader.close()
    assert calls == ['init']
