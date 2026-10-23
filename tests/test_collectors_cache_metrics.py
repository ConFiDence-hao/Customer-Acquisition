"""Real persistence and HTTP transport checks for bounded collector caching."""
import json
from datetime import datetime, timedelta

import httpx
import pytest

from app import collectors, db, workbench


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(db, 'ROOT', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'testing-only-password')
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')
    stamp = db.now()
    monkeypatch.setattr(db, 'now', lambda: stamp)
    monkeypatch.setattr(collectors.time, 'sleep', lambda seconds: None)
    db.initialize()
    sources = {sid: dict(next(s for s in collectors.SOURCES if s['id'] == sid)) for sid in ('cseg', 'bjx')}
    with db.connect() as c:
        for source in sources.values():
            c.execute('INSERT INTO sources(id,config) VALUES(?,?)', (source['id'], db.dumps(source)))

    class Site:
        list_title = '垃圾焚烧智慧焚烧控制改造...'
        final_title = '垃圾焚烧智慧焚烧控制改造公开招标公告'
        date = '2099-10-10 15:43'
        list_date = '2099-10-10'
        body = '本项目采购智慧焚烧及烟气智能优化控制系统。' * 30
        rows = 1
        list_failure = False
        detail_failure = False

        def __init__(self):
            self.sources = sources
            self.requests = []
            self.clients = []

        def handler(self, request):
            self.requests.append(str(request.url))
            if request.url.path == '/robots.txt':
                return httpx.Response(404)
            if 'a=shows' in str(request.url):
                if self.detail_failure:
                    return httpx.Response(200, text='<body>CF_APP_WAF 人机验证</body>')
                return httpx.Response(200, text='<h1>' + self.final_title + '</h1>'
                    '<div class="content article-content">' + self.body + '</div>'
                    '<div>发布时间：' + self.date + '</div>')
            if self.list_failure:
                return httpx.Response(403)
            return httpx.Response(200, text='<body>公开公告列表</body>')

        def candidates(self, client, source, settings):
            client.get(source['url'])
            return [{'title': self.list_title, 'url': 'https://www.cseg.cn/index.php?a=shows&catid=15&id=' + str(991 + i),
                     'published_at': self.list_date, 'published_at_precision': 'day'} for i in range(self.rows)]

        def collect(self, sid='cseg'):
            return collectors.collect_source(self.sources[sid], [], db.DEFAULT_SETTINGS)

        def item(self):
            with db.connect() as c:
                row = c.execute('SELECT payload FROM opportunities ORDER BY id LIMIT 1').fetchone()
                return json.loads(row[0])

        def replace_payload(self, **updates):
            item = self.item()
            item.update(updates)
            with db.connect() as c:
                c.execute('UPDATE opportunities SET payload=? WHERE id=(SELECT min(id) FROM opportunities)', (db.dumps(item),))

        def detail_calls(self):
            return [url for url in self.requests if 'a=shows' in url]

    controlled = Site()
    original_client = collectors.PublicClient

    def client():
        instance = original_client()
        instance.client.close()
        instance.client = httpx.Client(transport=httpx.MockTransport(controlled.handler), trust_env=False)
        controlled.clients.append(instance)
        return instance

    monkeypatch.setattr(collectors, 'PublicClient', client)
    monkeypatch.setattr(collectors, 'html_candidates', controlled.candidates)
    return controlled


def test_truncated_list_title_reuses_full_body_title_without_versions_or_duplicate_pushes(site):
    first = site.collect()
    workbench.dispatch_due(force=True)
    with db.connect() as c:
        first_hash = c.execute('SELECT content_hash FROM opportunities').fetchone()[0]
        pushes = [dict(row) for row in c.execute('SELECT * FROM pushes')]
    assert pushes
    second = site.collect()
    workbench.dispatch_due(force=True)
    assert len(site.detail_calls()) == 1
    assert site.item()['title'] == site.final_title
    assert site.item()['list_title'] == site.list_title and site.item()['listing_source_id'] == 'cseg'
    assert first['cache_hits'] == 0 and first['detail_reads'] == 1
    assert second['cache_hits'] == 1 and second['detail_reads'] == 0
    assert second['unchanged'] == 1 and second['updated'] == 0
    with db.connect() as c:
        assert c.execute('SELECT count(*) FROM versions').fetchone()[0] == 0
        assert c.execute('SELECT content_hash FROM opportunities').fetchone()[0] == first_hash
        assert [dict(row) for row in c.execute('SELECT * FROM pushes')] == pushes


def test_changed_list_title_forces_detail_refresh_even_when_final_title_is_unchanged(site):
    site.collect()
    site.list_title = '垃圾焚烧智慧焚烧控制改造补充公告...'
    result = site.collect()
    assert len(site.detail_calls()) == 2
    assert result['detail_reads'] == 1 and result['cache_hits'] == 0
    assert site.item()['list_title'] == site.list_title
    # The list cache key itself is metadata, not a change to the actual notice.
    assert result['unchanged'] == 1
    with db.connect() as c:
        assert c.execute('SELECT count(*) FROM versions').fetchone()[0] == 0


def test_expired_body_cache_is_refetched(site):
    site.collect()
    site.replace_payload(detail_fetched_at=(datetime.now(db.TZ) - timedelta(hours=7)).isoformat())
    result = site.collect()
    assert len(site.detail_calls()) == 2 and result['cache_hits'] == 0 and result['detail_reads'] == 1


def test_same_url_in_another_channel_cannot_use_first_channels_list_title(site):
    site.collect('cseg')
    second = site.collect('bjx')
    third = site.collect('bjx')
    assert len(site.detail_calls()) == 2
    assert second['cache_hits'] == 0 and second['detail_reads'] == 1
    assert third['cache_hits'] == 1 and third['detail_reads'] == 0
    assert site.item()['source_id'] == 'cseg'  # Initial provenance remains intact.
    assert site.item()['listing_source_id'] == 'bjx'
    assert site.item()['observed_source_ids'] == ['bjx', 'cseg']


def test_legacy_matching_final_title_can_be_upgraded_without_a_fake_content_version(site):
    site.list_title = site.final_title
    site.collect()
    item = site.item()
    item.pop('list_title', None)
    item.pop('listing_source_id', None)
    with db.connect() as c:
        c.execute('UPDATE opportunities SET payload=?', (db.dumps(item),))
    result = site.collect()
    assert len(site.detail_calls()) == 1
    assert result['cache_hits'] == 1 and result['unchanged'] == 1
    with db.connect() as c:
        assert c.execute('SELECT count(*) FROM versions').fetchone()[0] == 0


def test_failed_new_image_ocr_does_not_relabel_preserved_old_body(site,monkeypatch):
    site.collect()
    site.replace_payload(acquisition_method='http_html',detail_fetched_at='2020-01-01T00:00:00+08:00')
    def fail(*args):
        error=collectors.SourceUnavailable('图片OCR识别失败')
        error.image_urls=['https://www.cseg.cn/new-unread-image.jpg']
        raise error
    monkeypatch.setattr(collectors,'read_public_detail',fail)
    site.collect()
    item=site.item()
    assert item['content']==site.body
    assert item['acquisition_method']=='http_html'
    assert not item.get('image_urls')
    assert item['attempted_image_urls']==['https://www.cseg.cn/new-unread-image.jpg']


def test_valid_partial_body_is_saved_and_reused_with_its_coverage(site,monkeypatch):
    def partial(client,raw,source):
        raw.update(content_coverage='partial',detail_status='正文不完整',
                   extraction_warning='已保留网页文字；图片未下载，请查看原文。',
                   pdf_pages_total=31,pdf_pages_read=30,pdf_truncated=True)
        return '已确认采购的真实短文字。','垃圾焚烧项目采购公告',None
    monkeypatch.setattr(collectors,'read_public_detail',partial)
    result=site.collect()
    item=site.item()
    assert item['content']=='已确认采购的真实短文字。'
    assert item['detail_status']=='正文不完整' and item['content_coverage']=='partial'
    assert result['errors']==[] and result['partial']==1
    monkeypatch.setattr(collectors,'read_public_detail',lambda *a:pytest.fail('Recent partial text should be cached'))
    second=site.collect()
    item=site.item()
    assert second['cache_hits']==1 and item['pdf_pages_read']==30 and item['pdf_truncated'] is True
    assert item['extraction_warning'] in item['risks']


def test_new_list_only_read_keeps_historical_body_with_honest_status(site,monkeypatch):
    site.collect()
    site.replace_payload(acquisition_method='http_html',detail_fetched_at='2020-01-01T00:00:00+08:00')
    def no_text(client,raw,source):
        raw.update(content_coverage='list_only',detail_status='仅列表线索',extraction_warning='仅取得列表，正文待核实。')
        return '', '', None
    monkeypatch.setattr(collectors,'read_public_detail',no_text)
    site.collect()
    item=site.item()
    assert item['content']==site.body and '保留历史正文' in item['detail_status']
    assert item['content_coverage']=='partial' and item['acquisition_method']=='http_html'


def test_pdf_metadata_only_preserves_historical_body_and_current_failure_evidence(site,monkeypatch):
    site.collect()
    site.replace_payload(acquisition_method='pdf_text',pdf_pages_total=2,pdf_pages_read=2,
        detail_fetched_at='2020-01-01T00:00:00+08:00')
    def failed_pdf(client,raw,source):
        raw.update(acquisition_method='html_metadata',pdf_body_empty=True,pdf_pages_total=3,
            pdf_pages_read=0,pdf_failed_pages=[1],content_coverage='partial',detail_status='正文不完整',
            extraction_warning='本轮PDF扫描页OCR超时，未取得正文')
        return '招标业主：公告页上的单位名称', '', None
    monkeypatch.setattr(collectors,'read_public_detail',failed_pdf)
    site.collect()
    item=site.item()
    assert item['content']==site.body and item['acquisition_method']=='pdf_text'
    assert item['pdf_pages_read']==2 and '保留历史正文' in item['detail_status']
    assert item['attempted_pdf_metadata']['pdf_pages_read']==0
    assert item['attempted_pdf_metadata']['pdf_failed_pages']==[1]
    assert 'OCR超时' in item['extraction_warning']


def test_legacy_truncated_title_without_channel_key_is_verified_once(site):
    site.collect()
    item = site.item()
    item.pop('list_title', None)
    item.pop('listing_source_id', None)
    with db.connect() as c:
        c.execute('UPDATE opportunities SET payload=?', (db.dumps(item),))
    second = site.collect()
    third = site.collect()
    assert len(site.detail_calls()) == 2
    assert second['cache_hits'] == 0 and second['detail_reads'] == 1
    assert third['cache_hits'] == 1 and third['detail_reads'] == 0


@pytest.mark.parametrize('updates', [
    {'content': '不足120字的旧正文'},
    {'detail_status': '详情暂不可读；保留历史正文'},
    {'detail_fetched_at': 'not-a-valid-timestamp'},
    {'detail_fetched_at': (datetime.now(db.TZ) + timedelta(days=1)).isoformat()},
])
def test_invalid_or_unverified_body_never_counts_as_a_cache_hit(site, updates):
    site.collect()
    site.replace_payload(**updates)
    result = site.collect()
    assert len(site.detail_calls()) == 2 and result['cache_hits'] == 0 and result['detail_reads'] == 1
    assert site.item()['detail_status'] == '正文可读'


def test_cached_metadata_keeps_minute_precision_and_does_not_clear_date_conflicts(site):
    site.collect()
    second = site.collect()
    assert site.item()['published_at'] == '2099-10-10T15:43:00+08:00'
    assert site.item()['published_at_precision'] == 'minute' and second['cache_hits'] == 1
    site.list_date = '2099-10-11'
    warning = '列表与页面发布日期不一致，保留列表日期，需核实原公告'
    site.replace_payload(publication_warning=warning)
    result = site.collect()
    assert result['cache_hits'] == 1 and len(site.detail_calls()) == 1
    assert site.item()['publication_warning'] == warning
    assert site.item()['detail_published_at'] == '2099-10-10 15:43'


def test_changed_list_date_creates_cache_conflict_until_fresh_detail_verifies_it(site):
    site.collect()
    assert not site.item().get('publication_warning')
    site.list_date = '2099-10-11'
    result = site.collect()
    assert result['cache_hits'] == 1 and len(site.detail_calls()) == 1
    warning = '列表与页面发布日期不一致，保留列表日期，需核实原公告'
    assert site.item()['publication_warning'] == warning
    assert site.item()['published_at'] == '2099-10-11T00:00:00+08:00'
    assert site.item()['detail_published_at'] == '2099-10-10 15:43'
    assert warning in site.item()['risks']
    # A later matching list date alone does not prove that the source's body
    # metadata was reverified; only a successful fresh detail read can clear it.
    site.list_date = '2099-10-10'
    site.collect()
    assert site.item()['publication_warning'] == warning and len(site.detail_calls()) == 1
    site.replace_payload(detail_fetched_at=(datetime.now(db.TZ) - timedelta(hours=7)).isoformat())
    refreshed = site.collect()
    assert refreshed['detail_reads'] == 1 and len(site.detail_calls()) == 2
    assert not site.item().get('publication_warning')
    assert warning not in site.item()['risks']
    assert site.item()['published_at'] == '2099-10-10T15:43:00+08:00'


def test_metrics_count_actual_http_and_detail_reads_without_counting_blocked_remaining_items(site):
    site.rows = 4
    site.detail_failure = True
    result = site.collect()
    assert result['found'] == 4 and result['restricted'] == 4
    assert len(site.detail_calls()) == 1
    assert result['detail_reads'] == 1 and result['cache_hits'] == 0
    assert result['http_requests'] == 3  # robots, public list, first challenged body
    assert isinstance(result['elapsed_seconds'], (int, float)) and result['elapsed_seconds'] >= 0


def test_metrics_are_returned_when_public_list_access_fails(site):
    site.list_failure = True
    result = site.collect()
    assert result['errors'] and result['found'] == 0
    assert result['http_requests'] == 2 and result['detail_reads'] == 0 and result['cache_hits'] == 0
    assert result['elapsed_seconds'] >= 0


def test_http_attempts_include_the_finite_retry_without_changing_the_call_budget(monkeypatch):
    monkeypatch.setattr(collectors.time, 'sleep', lambda seconds: None)
    requests = []
    def response(request):
        requests.append(request)
        return httpx.Response(503 if len(requests) == 1 else 200, text='public content')
    client = collectors.PublicClient()
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(response))
    try:
        assert client._request('https://www.cseg.cn/public').status_code == 200
        assert len(requests) == 2 and getattr(client, 'http_attempts', 0) == 2
        assert client.requests == 1
        client.requests = 500
        with pytest.raises(collectors.SourceUnavailable, match='请求数量上限'):
            client._request('https://www.cseg.cn/public')
        assert len(requests) == 2 and client.http_attempts == 2
        with pytest.raises(collectors.SourceUnavailable, match='白名单'):
            client._request('https://unverified.example/public')
        assert client.http_attempts == 2
    finally:
        client.close()
