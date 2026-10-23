"""Extract public PDF bytes with bounded native text and local CPU OCR.

The caller owns network/robots validation. One isolated process handles the
whole PDF, flushes each completed page, and is killed at the total deadline.
"""
from __future__ import annotations

import base64
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys


MAX_PAGES = 30
MAX_PDF_BYTES = 20_000_000
MAX_WORKER_INPUT_BYTES = 32_000_000
MAX_WORKER_OUTPUT_BYTES = 2_000_000
MAX_PIXELS = 24_000_000
MAX_TEXT_CHARS = 65000
PDF_TIMEOUT = 90.0
RENDER_SCALE = 2.0  # 144 dpi for ordinary PDF canvas units.


class PDFContentError(ValueError):
    """The supplied bytes cannot be processed as a public PDF."""


def _empty_metadata(page_limit: int) -> dict:
    return {
        'pdf_pages_total': None, 'pdf_pages_read': 0, 'pdf_ocr_pages': 0,
        'pdf_failed_pages': [], 'pdf_read_page_numbers': [],
        'pdf_ocr_page_numbers': [], 'pdf_page_errors': {},
        'pdf_pages_attempted': 0, 'pdf_truncated': False,
        'pdf_text_truncated': False, 'pdf_timed_out': False,
        'pdf_page_limit': page_limit, 'pdf_input_limit_bytes': MAX_PDF_BYTES,
        'pdf_max_render_pixels': MAX_PIXELS, 'pdf_text_limit_chars': MAX_TEXT_CHARS,
        'pdf_timeout_seconds': PDF_TIMEOUT, 'extraction_warning': '',
        'content_coverage': 'list_only', 'detail_status': '仅列表线索',
    }


def extract_pdf(data: bytes, max_pages: int = MAX_PAGES) -> tuple[str, dict]:
    """Return text and coverage facts; unknown PDF page totals remain None.

    ``pdf_pages_read``/``pdf_ocr_pages`` count nonempty successfully read pages.
    ``pdf_failed_pages`` contains only known failed 1-based page numbers, never
    pages omitted by the cap/deadline. All methods process at most the first 30.
    """
    limit = min(max_pages, MAX_PAGES) if isinstance(max_pages, int) and not isinstance(max_pages, bool) else 0
    meta = _empty_metadata(max(limit, 0))
    if limit <= 0:
        meta['extraction_warning'] = 'PDF页数限制无效，未读取正文'
        return '', meta
    if not isinstance(data, bytes) or not data:
        raise PDFContentError('PDF文件无法解析：内容为空或字节格式无效')
    if len(data) > MAX_PDF_BYTES:
        raise PDFContentError('PDF输入超过20MB上限，未读取正文，请查阅来源PDF')
    if b'%PDF-' not in data[:1024]:
        raise PDFContentError('PDF文件无法解析：文件头无效')
    try:
        output, timed_out, returncode = _run_worker(data, limit)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        meta['extraction_warning'] = 'PDF提取进程未完成（' + type(exc).__name__ + '），请查阅来源PDF'
        return '', meta
    return _collect_output(output, limit, timed_out, returncode)


def _worker_command() -> list[str]:
    return [sys.executable, '-B', '-m', 'app.pdf_content', '--worker']


def _run_worker(data: bytes, max_pages: int) -> tuple[bytes, bool, int]:
    request = json.dumps({'pdf': base64.b64encode(data).decode('ascii'),
                          'max_pages': max_pages}).encode('ascii')
    if len(request) > MAX_WORKER_INPUT_BYTES:
        raise ValueError('PDF worker input exceeds limit')
    process = subprocess.Popen(
        _worker_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, cwd=str(Path(__file__).resolve().parent.parent),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    timed_out = False
    try:
        output, _ = process.communicate(input=request, timeout=PDF_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        process.kill()
        try:
            output, _ = process.communicate(timeout=2.0)
        except subprocess.TimeoutExpired as cleanup_exc:
            # An abnormal shutdown must still retain already flushed progress.
            output = cleanup_exc.output or exc.output or b''
            if process.stdout is not None:
                process.stdout.close()
    return output, timed_out, process.returncode if process.returncode is not None else -1


def _collect_output(output: bytes, limit: int, timed_out: bool, returncode: int) -> tuple[str, dict]:
    meta = _empty_metadata(limit)
    warnings = []
    pages: dict[int, str] = {}
    methods: dict[int, str] = {}
    errors: dict[int, str] = {}
    done = False
    if len(output) > MAX_WORKER_OUTPUT_BYTES:
        output = output[:MAX_WORKER_OUTPUT_BYTES]
        warnings.append('PDF提取输出超过上限，保留已完成部分')
        meta['pdf_truncated'] = True
    for line in output.decode('utf-8', errors='replace').splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue  # A library log or a cut-off final line is not page data.
        if not isinstance(event, dict):
            continue
        kind = event.get('event')
        if kind == 'document':
            total = event.get('pages_total')
            if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
                meta['pdf_pages_total'] = total
        elif kind in ('page', 'page_error'):
            page = event.get('page')
            total = meta['pdf_pages_total']
            if (not isinstance(page, int) or isinstance(page, bool)
                    or not 1 <= page <= limit or (total is not None and page > total)):
                continue
            if kind == 'page':
                text = event.get('text')
                method = event.get('method')
                if isinstance(text, str) and text.strip() and method in ('native', 'ocr'):
                    pages[page] = text.strip()
                    methods[page] = method
                    errors.pop(page, None)
                    if event.get('text_truncated') is True:
                        meta['pdf_text_truncated'] = True
            elif page not in pages:
                reason = event.get('reason')
                errors[page] = reason if isinstance(reason, str) and reason else 'PDF页读取失败'
        elif kind == 'error':
            reason = event.get('reason')
            if isinstance(reason, str) and reason:
                if not pages and meta['pdf_pages_total'] is None and reason.startswith('PDF解析失败'):
                    raise PDFContentError('PDF文件无法解析：' + reason)
                warnings.append(reason)
        elif kind == 'done':
            done = True
            meta['pdf_text_truncated'] |= event.get('text_truncated') is True
    text = '\n\n'.join(pages[page] for page in sorted(pages))
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        meta['pdf_text_truncated'] = True
    meta['pdf_read_page_numbers'] = sorted(pages)
    meta['pdf_ocr_page_numbers'] = [page for page in sorted(pages) if methods[page] == 'ocr']
    meta['pdf_pages_read'] = len(pages)
    meta['pdf_ocr_pages'] = len(meta['pdf_ocr_page_numbers'])
    meta['pdf_failed_pages'] = sorted(errors)
    meta['pdf_page_errors'] = {str(page): errors[page] for page in sorted(errors)}
    meta['pdf_pages_attempted'] = len(set(pages) | set(errors))
    total = meta['pdf_pages_total']
    cap_applied = total is not None and total > limit
    if cap_applied:
        warnings.append(f'PDF共{total}页，仅处理前{limit}页；其余页面请查阅来源PDF')
    if meta['pdf_ocr_pages']:
        warnings.append('扫描页使用OCR识别，可能存在识别错误，请核对来源PDF')
    warnings.extend(f'第{page}页：{errors[page]}' for page in sorted(errors))
    if meta['pdf_text_truncated']:
        warnings.append(f'PDF正文达到{MAX_TEXT_CHARS}字符上限，保留已完成部分，请查阅来源PDF')
    if timed_out:
        warnings.append(f'PDF提取超时（{PDF_TIMEOUT:g}秒总时限），已终止进程并保留已完成页面')
    elif returncode != 0 or not done:
        warnings.append('PDF提取进程未完整完成，保留已完成页面，请查阅来源PDF')
    if total == 0:
        warnings.append('PDF不包含可读取页面')
    if not text and not warnings:
        warnings.append('PDF未取得可读文字，请查阅来源PDF')
    meta['pdf_timed_out'] = timed_out
    meta['pdf_truncated'] |= cap_applied or meta['pdf_text_truncated'] or timed_out
    complete = (bool(text) and total is not None and total > 0
                and len(pages) == total and not errors and not meta['pdf_truncated']
                and done and returncode == 0)
    if text:
        meta['content_coverage'] = 'complete' if complete else 'partial'
        meta['detail_status'] = '正文可读' if complete else '正文不完整'
    meta['extraction_warning'] = '；'.join(dict.fromkeys(warnings))
    return text, meta


def _make_ocr_engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(det_use_cuda=False, cls_use_cuda=False, rec_use_cuda=False,
                    det_use_dml=False, cls_use_dml=False, rec_use_dml=False,
                    intra_op_num_threads=1, inter_op_num_threads=1,
                    print_verbose=False, text_score=0.6)


class _PageOCR:
    """One lazy PDFium document and one lazy CPU OCR engine for all scan pages."""
    def __init__(self, data: bytes):
        self.data = data
        self.document = None
        self.engine = None
        self.initialization_error = None

    def recognize(self, page_index: int) -> str:
        if self.initialization_error is not None:
            raise self.initialization_error
        if self.document is None:
            try:
                import pypdfium2
                self.document = pypdfium2.PdfDocument(self.data)
            except Exception as exc:
                self.initialization_error = exc
                raise
        page = self.document[page_index]
        bitmap = image = None
        try:
            width, height = page.get_size()
            if (not math.isfinite(width) or not math.isfinite(height)
                    or width <= 0 or height <= 0
                    or math.ceil(width * RENDER_SCALE) * math.ceil(height * RENDER_SCALE) > MAX_PIXELS):
                raise ValueError(f'PDF扫描页渲染像素超过{MAX_PIXELS}上限或尺寸无效')
            bitmap = page.render(scale=RENDER_SCALE, rev_byteorder=True, limit_image_cache=True)
            image = bitmap.to_pil().convert('RGB')
            if image.size[0] * image.size[1] > MAX_PIXELS:
                raise ValueError(f'PDF扫描页渲染像素超过{MAX_PIXELS}上限')
            if self.engine is None:
                try:
                    self.engine = _make_ocr_engine()
                except Exception as exc:
                    self.initialization_error = exc
                    raise
            rows, _ = self.engine(image)
            lines = []
            for row in rows or []:
                if not isinstance(row, (list, tuple)) or len(row) < 3 or not isinstance(row[1], str):
                    continue
                try:
                    score = float(row[2])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(score) and score >= 0.6 and row[1].strip():
                    lines.append(row[1].strip())
            return '\n'.join(lines)
        finally:
            if image is not None:
                image.close()
            if bitmap is not None:
                bitmap.close()
            page.close()

    def close(self):
        if self.document is not None:
            self.document.close()
        self.document = None
        self.engine = None


def _extract_pages(data: bytes, max_pages: int, emit):
    """Worker-side page extraction. Emits only facts obtained from this PDF."""
    ocr = None
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            emit({'event': 'error', 'reason': 'PDF已加密，未读取正文，请查阅来源PDF'})
            emit({'event': 'done'})
            return
        total = len(reader.pages)
        emit({'event': 'document', 'pages_total': total})
        text_size = 0
        text_truncated = False
        for index in range(min(max_pages, MAX_PAGES, total)):
            page_number = index + 1
            try:
                try:
                    text = (reader.pages[index].extract_text() or '').strip()
                except Exception:
                    text = ''  # PDFium may still render a page with a broken text layer.
                method = 'native'
                if not text:
                    if ocr is None:
                        ocr = _PageOCR(data)
                    text = ocr.recognize(index).strip()
                    method = 'ocr'
                if not text:
                    emit({'event': 'page_error', 'page': page_number,
                          'reason': 'PDF扫描页OCR未识别出文字'})
                    continue
                separator = 2 if text_size else 0
                remaining = MAX_TEXT_CHARS - text_size - separator
                clipped = len(text) > remaining
                text = text[:max(remaining, 0)]
                if text:
                    emit({'event': 'page', 'page': page_number, 'method': method,
                          'text': text, 'text_truncated': clipped})
                    text_size += len(text) + separator
                if clipped or text_size >= MAX_TEXT_CHARS:
                    text_truncated = clipped or index + 1 < total
                    break
            except (ImportError, ModuleNotFoundError):
                emit({'event': 'page_error', 'page': page_number,
                      'reason': 'PDF扫描页OCR组件未就绪，请查阅来源PDF'})
            except Exception as exc:
                if isinstance(exc, ValueError) and '像素' in str(exc):
                    reason = str(exc)
                else:
                    reason = 'PDF扫描页OCR识别失败（' + type(exc).__name__ + '）'
                emit({'event': 'page_error', 'page': page_number, 'reason': reason})
        emit({'event': 'done', 'text_truncated': text_truncated})
    except (ImportError, ModuleNotFoundError):
        emit({'event': 'error', 'reason': 'PDF解析组件未就绪'})
    except Exception as exc:
        emit({'event': 'error', 'reason': 'PDF解析失败（' + type(exc).__name__ + '）'})
    finally:
        if ocr is not None:
            try:
                ocr.close()
            except Exception:
                pass


def _worker_main():
    def emit(event):
        print(json.dumps(event, ensure_ascii=True), flush=True)
    try:
        raw = sys.stdin.buffer.read(MAX_WORKER_INPUT_BYTES + 1)
        if len(raw) > MAX_WORKER_INPUT_BYTES:
            raise ValueError('worker input exceeds limit')
        request = json.loads(raw)
        data = base64.b64decode(request['pdf'], validate=True)
        limit = request['max_pages']
        if (not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PAGES
                or not data or len(data) > MAX_PDF_BYTES or b'%PDF-' not in data[:1024]):
            raise ValueError('invalid PDF worker input')
        _extract_pages(data, limit, emit)
    except Exception as exc:
        emit({'event': 'error', 'reason': 'PDF提取输入无效（' + type(exc).__name__ + '）'})


if __name__ == '__main__' and sys.argv[1:] == ['--worker']:
    _worker_main()
