"""Verified first-party procurement adapters, without supplier login or captcha workarounds."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .analysis import DATE, date_precision, parse_date

SOURCE_IDS = frozenset({'bece'})
EXTRA_HOSTS = frozenset()
CANONICAL_URLS = {'bece': 'https://ecp.capitaleco-pro.com/'}
COVERAGE_NOTES = {'bece': '公开首页及招标/非招标采购栏目各最多3页，合计最多7次列表读取、候选200条；PDF优先读取文字，扫描页本地OCR，最多前30页，未完成部分保留原文入口。'}

_NOTICE_PATH = re.compile(r'^/sdny_(?:bulletin|changeBulletin|winningperson|resultBulletin|failBulletin|competeAndresult)/20\d{2}-\d{2}-\d{2}/\d+\.html$')
_PROTECTION = re.compile(r'CF_APP_WAF|aliyunCaptcha|captcha-container|访问验证|人机验证', re.I)


def _public_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (parts.scheme == 'https' and parts.hostname == 'ecp.capitaleco-pro.com'
                and parts.port in (None, 443) and parts.username is None and parts.password is None)
    except ValueError:
        return False


def get_candidates(client, source: dict, settings: dict) -> list[dict]:
    from .collectors import SourceUnavailable, relevant,walk_listing_pages,disclosed_listing_links
    if source['id'] != 'bece' or not _public_url(source['url']):
        raise SourceUnavailable('采购列表地址尚未核实')
    def valid_list(target):
        parts=urlsplit(target);query=parse_qs(parts.query)
        return (_public_url(target) and not parts.fragment and parts.path=='/sccms/category/list.html'
                and query.get('categoryId',[''])[0] in ('2','13') and set(query)<={'dates','categoryId','tabName','page'}
                and query.get('page',['1'])[0] in ('1','2','3'))
    links=lambda html,url:disclosed_listing_links(html,url,valid_list,turn_page=True)
    items = {}; observed = 0
    for current,html in walk_listing_pages(client,[source['url']],links,max_pages=7):
        soup = BeautifulSoup(html, 'html.parser')
        for anchor in soup.select('a[href]'):
            url = urljoin(current, anchor['href'])
            parts = urlsplit(url)
            if not _public_url(url) or not _NOTICE_PATH.fullmatch(parts.path):continue
            observed += 1
            title_node = anchor.select_one('.title')
            title = anchor.get('title') or (title_node or anchor).get_text(' ', strip=True)
            title = re.sub(r'\s+', ' ', title).strip()
            if len(title) < 10 or not relevant(title, settings):continue
            date_node = anchor.select_one('.data,time,.date')
            date_match = re.search(DATE, date_node.get_text(' ', strip=True)) if date_node else None
            publication = date_match.group(0).strip() if date_match else None
            if publication and not parse_date(publication):publication = None
            url = urlunsplit(parts._replace(query='', fragment=''))
            items[url] = {'title': title, 'url': url, 'published_at': publication,
                          'published_at_precision': date_precision(publication)}
    if not observed:
        raise SourceUnavailable('未发现公开采购公告链接，列表可能为空或结构发生变化')
    return sorted(items.values(), key=lambda item: item.get('published_at') or '', reverse=True)[:200]


def read_detail(client, raw: dict, source: dict) -> tuple[str, str, str | None]:
    from .collectors import SourceUnavailable, read_pdf_attachment
    url = raw['url']
    if source['id'] != 'bece' or not _public_url(url) or not _NOTICE_PATH.fullmatch(urlsplit(url).path):
        raise SourceUnavailable('采购公告地址尚未核实')
    html = client.get(url)
    if _PROTECTION.search(html):
        raise SourceUnavailable('采购平台要求访问验证，本轮停止读取')
    soup = BeautifulSoup(html, 'html.parser')
    heading = soup.select_one('.topTitle h3')
    iframe = soup.select_one('.pdfbox iframe#pdfContainer[src]')
    if heading is None or iframe is None:
        raise SourceUnavailable('公告正文PDF或标题容器缺失，不能将导航与项目概况当成正文')
    target = parse_qs(urlsplit(iframe['src']).query).get('file', [''])[0]
    if not _public_url(target) or urlsplit(target).path != '/scdzzb/cgUploadController.do':
        raise SourceUnavailable('采购公告PDF地址超出已核实的同源附件入口')
    title = re.sub(r'^\[[^\]]+\]\s*', '', heading.get_text(' ', strip=True))
    metadata = []; publication = None
    for label in soup.select('table.blockContent th'):
        value = label.find_next_sibling('td')
        if value is None:
            continue
        name = label.get_text(' ', strip=True).rstrip('：:')
        text = value.get_text(' ', strip=True)
        metadata.append(name+'：'+text)
        if name in ('发布时间', '发布日期', '信息发布时间'):
            match = re.search(DATE, text)
            if match and parse_date(match.group(0)):
                publication = match.group(0).strip()
    body = read_pdf_attachment(client, html, raw)
    raw['pdf_body_empty']=not bool(body.strip())
    if not body and metadata:
        raw.update(content_coverage='partial',detail_status='正文不完整',
                   acquisition_method='html_metadata',
                   extraction_warning='仅取得公告页基本信息；'+raw.get('extraction_warning','PDF正文未取得，请查看来源原附件。'))
    combined=('\n'.join(metadata)+'\n\n'+body).strip()
    if len(combined)>65000:
        raw.update(pdf_text_truncated=True,pdf_truncated=True,content_coverage='partial',detail_status='正文不完整',
            extraction_warning='；'.join(filter(None,[raw.get('extraction_warning'),
                '公告基本信息和PDF正文合计超过65000字符，已截断；其余内容请查看来源原文。'])))
    return combined[:65000], title, publication
