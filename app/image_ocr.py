"""Bounded CPU OCR fallback for verified CSEG and BJX public notice images.

Network reads stay in PublicClient, so its origin and robots checks still apply.
The OCR worker receives bytes only and runs with a hard subprocess timeout.
"""
from __future__ import annotations

import base64
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup


MAX_IMAGES = 6
MAX_IMAGE_BYTES = 4_000_000
MAX_TOTAL_BYTES = 12_000_000
MAX_PIXELS = 24_000_000
OCR_TIMEOUT = 30.0
ARTICLE_BUDGET = 45.0
MAX_TEXT_CHARS = 65000
_IMAGE_PATH = re.compile(r'^/d/file/content/20\d{2}/(?:0[1-9]|1[0-2])/[A-Za-z0-9_.-]+\.(?:jpe?g|png|webp)$', re.I)
_BJX_ARTICLE_PATH = re.compile(r'^/news/20\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])/\d+\.shtml$')
_BJX_IMAGE_PATH = re.compile(r'^/news/UploadFile/20\d{2}(?:0[1-9]|1[0-2])/[A-Za-z0-9_.-]+\.(?:png|jpg)$', re.I)
_PROTECTION = re.compile(r'CF_APP_WAF|aliyunCaptcha|captcha-container|访问验证|人机验证', re.I)
_NOTICE = '【图片OCR识别正文，可能存在识别错误，请核对原图】\n'


class OCRNotReady(RuntimeError):
    """The optional local CPU OCR runtime or its models are unavailable."""


def _origin(parts, hostname='www.cseg.cn') -> bool:
    return (parts.scheme == 'https' and parts.hostname == hostname
            and parts.port in (None, 443) and parts.username is None
            and parts.password is None and not parts.fragment)


def _article_url(url: str, source_id='cseg') -> bool:
    try:
        parts = urlsplit(url)
        if source_id in ('bjx', 'gfcl'):
            return (_origin(parts, 'huanbao.bjx.com.cn') and not parts.query
                    and _BJX_ARTICLE_PATH.fullmatch(parts.path) is not None)
        if source_id != 'cseg' or not _origin(parts) or parts.path != '/index.php':
            return False
        query = parse_qs(parts.query, keep_blank_values=True, max_num_fields=4)
        return (set(query) == {'a', 'catid', 'id'} and query['a'] == ['shows']
                and query['catid'] == ['15'] and len(query['id']) == 1
                and re.fullmatch(r'\d{1,12}', query['id'][0]) is not None)
    except (TypeError, ValueError):
        return False


def _image_url(url: str, source_id='cseg') -> bool:
    try:
        parts = urlsplit(url)
        if source_id in ('bjx', 'gfcl'):
            return (_origin(parts, 'img01.mybjx.net') and not parts.query
                    and _BJX_IMAGE_PATH.fullmatch(parts.path) is not None)
        if source_id != 'cseg':
            return False
        return _origin(parts) and not parts.query and _IMAGE_PATH.fullmatch(parts.path) is not None
    except (TypeError, ValueError):
        return False


def _fail(message: str, urls: list[str], *, cause=None):
    # Lazy import avoids a circular import when collectors integrates this helper.
    from .collectors import SourceUnavailable
    error = SourceUnavailable(message)
    error.image_urls = list(urls)
    error.acquisition_method = 'image_ocr'
    raise error from cause


def extract_image_article(client, html: str, page_url: str, source_id: str) -> tuple[str, list[str]]:
    """Return OCR text plus original images, or no fallback when not applicable.

    A failed or partial image article raises SourceUnavailable, with image_urls
    retained on the exception. It must never be published as complete OCR text.
    """
    from .collectors import SourceUnavailable

    if not _article_url(page_url, source_id):
        return '', []
    if _PROTECTION.search(html):
        _fail('图片OCR识别失败：页面要求访问验证', [])
    soup = BeautifulSoup(html, 'html.parser')
    if source_id == 'cseg':
        container = soup.select_one('.content-STRO') or soup.select_one('.content')
    else:
        container = soup.select_one('.cc-article') or soup.select_one('#article')
    if container is None or container.find_parent(['nav', 'header', 'footer']) is not None:
        return '', []
    for node in container.select('script,style,nav,header,footer,noscript'):
        node.decompose()
    public_text = container.get_text('\n', strip=True)
    if len(public_text) >= 120:
        return '', []
    urls = []
    for img in container.select('img[src]'):
        url = urljoin(page_url, img['src'].strip())
        if _image_url(url, source_id) and url not in urls:
            urls.append(url)
    if not urls:
        return '', []
    if len(urls) > MAX_IMAGES:
        _fail('图片正文超过单篇图片数量上限，保留原图查阅', urls)
    deadline = time.monotonic() + ARTICLE_BUDGET
    payloads = []
    size = 0
    for url in urls:
        if time.monotonic() >= deadline:
            _fail('图片OCR识别失败：读取原图超时', urls)
        try:
            data = client.get_bytes(url)
        except (SourceUnavailable, httpx.HTTPError, OSError, ValueError) as exc:
            reason='原图robots规则禁止自动读取' if 'robots' in str(exc) else '原图无法公开读取，已停止'
            _fail('图片OCR识别失败：'+reason, urls, cause=exc)
        if not isinstance(data, bytes) or not data:
            _fail('图片OCR识别失败：原图不是有效图片字节', urls)
        if len(data) > MAX_IMAGE_BYTES:
            _fail('图片正文单张字节超过上限，保留原图查阅', urls)
        size += len(data)
        if size > MAX_TOTAL_BYTES:
            _fail('图片正文总字节超过上限，保留原图查阅', urls)
        payloads.append(data)
    remaining = min(OCR_TIMEOUT, deadline - time.monotonic())
    if remaining <= 0:
        _fail('图片OCR识别失败：读取原图超时', urls)
    try:
        texts = _run_ocr(payloads, remaining)
    except OCRNotReady as exc:
        _fail('OCR组件未就绪，图片正文保留原图查阅', urls, cause=exc)
    except subprocess.TimeoutExpired as exc:
        _fail('图片OCR识别失败：识别超时，已终止OCR进程', urls, cause=exc)
    except (ValueError, TypeError, RuntimeError, OSError) as exc:
        _fail('图片OCR识别失败，图片正文保留原图查阅', urls, cause=exc)
    if (len(texts) != len(urls) or any(not isinstance(text, str) or not text.strip() for text in texts)
            or len(''.join(texts).strip()) < 120):
        _fail('图片OCR识别失败：未取得完整且足量的正文', urls)
    sections = ([public_text] if public_text else []) + [text.strip() for text in texts]
    return (_NOTICE + '\n\n'.join(sections))[:MAX_TEXT_CHARS], urls


def _run_ocr(payloads: list[bytes], timeout: float) -> list[str]:
    request = json.dumps({'images': [base64.b64encode(data).decode('ascii') for data in payloads]}).encode('ascii')
    result = subprocess.run(
        [sys.executable, '-B', '-m', 'app.image_ocr', '--worker'],
        input=request, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).resolve().parent.parent), timeout=timeout, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    if result.returncode != 0 or len(result.stdout) > 2_000_000:
        raise ValueError('OCR worker failed')
    lines = result.stdout.decode('utf-8').splitlines()
    if not lines:
        raise ValueError('OCR worker returned no data')
    response = json.loads(lines[-1])
    if not isinstance(response, dict):
        raise ValueError('OCR worker returned invalid data')
    if response.get('error') == 'not_ready':
        raise OCRNotReady()
    texts = response.get('texts')
    if not isinstance(texts, list) or not all(isinstance(text, str) for text in texts):
        raise ValueError('OCR worker returned no text')
    return texts


def _recognize_payloads(payloads: list[bytes]) -> list[str]:
    """RapidOCR 1.4.4 API; imported only inside the bounded worker process."""
    from PIL import Image
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR(det_use_cuda=False, cls_use_cuda=False, rec_use_cuda=False,
                      det_use_dml=False, cls_use_dml=False, rec_use_dml=False,
                      intra_op_num_threads=1, inter_op_num_threads=1,
                      print_verbose=False, text_score=0.6)
    texts = []
    for data in payloads:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                raise ValueError('图片像素超过上限')
            if image.format not in ('JPEG', 'PNG', 'WEBP'):
                raise ValueError('图片格式未适配')
            image.load()
            rows, _ = engine(image.convert('RGB'))
        lines = []
        for row in rows or []:
            if not isinstance(row, (list, tuple)) or len(row) < 3 or not isinstance(row[1], str):
                continue
            score = float(row[2])
            if math.isfinite(score) and score >= 0.6 and row[1].strip():
                lines.append(row[1].strip())
        texts.append('\n'.join(lines)[:MAX_TEXT_CHARS])
    return texts


def _worker_main():
    try:
        request = json.loads(sys.stdin.buffer.read(18_000_001))
        images = request['images']
        if not isinstance(images, list) or not 1 <= len(images) <= MAX_IMAGES:
            raise ValueError('invalid image count')
        payloads = [base64.b64decode(item, validate=True) for item in images]
        if (any(not item or len(item) > MAX_IMAGE_BYTES for item in payloads)
                or sum(map(len, payloads)) > MAX_TOTAL_BYTES):
            raise ValueError('invalid image bytes')
        result = {'texts': _recognize_payloads(payloads)}
    except (ImportError, ModuleNotFoundError):
        result = {'error': 'not_ready'}
    except Exception:
        result = {'error': 'failed'}
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == '__main__' and sys.argv[1:] == ['--worker']:
    _worker_main()
