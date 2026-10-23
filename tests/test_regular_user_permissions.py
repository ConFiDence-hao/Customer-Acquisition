import json

import pytest
from fastapi.testclient import TestClient

from app import ai, db, main
from app.analysis import analyze


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(db, 'ROOT', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'testing-only-password')
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-32-characters-long')
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')
    main.FAILURES.clear()
    with TestClient(main.app) as c:
        login(c, 'admin')
        response = c.post('/api/users', json={
            'username': 'alice', 'display_name': 'Alice',
            'password': 'testing-user-password',
        })
        assert response.status_code == 200, response.text
        yield c


def login(client, username):
    password = 'testing-only-password' if username == 'admin' else 'testing-user-password'
    response = client.post('/api/login', json={'username': username, 'password': password})
    assert response.status_code == 200, response.text


def notice():
    item = analyze({
        'title': '垃圾焚烧智慧控制系统改造招标',
        'url': 'https://example.com/regular-permissions',
        'source_id': 'cecep', 'source_name': '公开采购来源',
        'source_kind': '集团采购', 'published_at': db.now(),
        'content': '投标截止时间：2099-12-10 12:00\n' + '公开正文证据。' * 30,
    }, [], db.DEFAULT_SETTINGS)
    item['ai_analysis'] = {'summary': '已有公共解读'}
    assert db.upsert(item) == 'inserted'
    with db.connect() as c:
        return c.execute('SELECT id FROM opportunities WHERE url=?', (item['url'],)).fetchone()[0]


@pytest.mark.parametrize('endpoint', [
    '/api/sources', '/api/runs', '/api/collection-schedule', '/api/collection-progress',
])
def test_regular_users_cannot_read_collection_management(client, endpoint):
    login(client, 'alice')
    response = client.get(endpoint)
    assert response.status_code == 403, response.text


@pytest.mark.parametrize('method,endpoint,payload', [
    ('POST', '/api/collect', None),
    ('PUT', '/api/collection-schedule', {'enabled': True, 'interval_minutes': 30}),
    ('POST', '/api/source-configs', {'name': '网站', 'url': 'https://example.com/list'}),
])
def test_regular_users_cannot_write_collection_management(client, method, endpoint, payload):
    login(client, 'alice')
    response = client.request(method, endpoint, json=payload)
    assert response.status_code == 403, response.text
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 0


def test_source_catalog_requires_login_and_returns_only_subscribable_names(client):
    with db.connect() as c:
        cecep = json.loads(c.execute('SELECT config FROM sources WHERE id=?', ('cecep',)).fetchone()[0])
        cecep['enabled'] = False
        c.execute('UPDATE sources SET config=? WHERE id=?', (db.dumps(cecep), 'cecep'))
        for source in [
            {'id': 'custom_disabled', 'name': '自定义来源', 'enabled': False, 'custom': True},
            {'id': 'enabled_reference', 'name': '已启用来源', 'enabled': True},
            {'id': 'disabled_reference', 'name': '未接入入口', 'enabled': False},
        ]:
            source.update(url='https://example.com/list', item_selector='.private-rule', include_keywords=['private-rule'])
            c.execute('INSERT INTO sources(id,config,status,last_error) VALUES(?,?,?,?)',
                      (source['id'], db.dumps(source), '异常', 'private-source-error'))
    client.post('/api/logout')
    assert client.get('/api/source-catalog').status_code == 401
    login(client, 'alice')
    response = client.get('/api/source-catalog')
    assert response.status_code == 200, response.text
    items = response.json()['items']
    assert all(set(item) == {'id', 'name'} for item in items)
    ids = {item['id'] for item in items}
    from app.collectors import BUILTIN_SOURCE_IDS
    assert ids == BUILTIN_SOURCE_IDS | {'custom_disabled', 'enabled_reference'}
    assert 'private-rule' not in response.text and 'private-source-error' not in response.text
    # Catalogue IDs use the same availability rule as personal subscription validation.
    subscribed = client.post('/api/subscriptions', json={'name': '控制专题', 'keywords': ['控制'], 'source_ids': sorted(ids)})
    assert subscribed.status_code == 200, subscribed.text


def test_public_sources_require_login(client):
    client.post('/api/logout')
    assert client.get('/api/public-sources').status_code == 401


@pytest.mark.parametrize('username', ['admin', 'alice'])
def test_public_sources_show_all_registered_entries_without_management_secrets(client, username):
    ident = notice()
    with db.connect() as c:
        for source_id in ('cecep', 'gfcl', 'cseg'):
            source = json.loads(c.execute('SELECT config FROM sources WHERE id=?', (source_id,)).fetchone()[0])
            source['enabled'] = False
            c.execute('UPDATE sources SET config=? WHERE id=?', (db.dumps(source), source_id))
        for source in [
            {'id': 'custom_history', 'name': '历史来源', 'enabled': False},
            {'id': 'custom_active', 'name': '正在使用的来源', 'enabled': True},
            {'id': 'custom_unused', 'name': '从未产出的来源', 'enabled': False},
        ]:
            source.update(custom=True, url='https://example.com/public-site', kind='自定义监测',
                          note='private-source-note', item_selector='.private-rule', exclude_keywords=['private-rule'])
            c.execute('INSERT INTO sources(id,config,status,last_error,last_success,last_count) VALUES(?,?,?,?,?,?)',
                      (source['id'], db.dumps(source), '异常', 'private-source-error', db.now(), 99))
        payload = json.loads(c.execute('SELECT payload FROM opportunities WHERE id=?', (ident,)).fetchone()[0])
        payload['observed_source_ids'] = ['gfcl', 'custom_history']
        c.execute('UPDATE opportunities SET payload=? WHERE id=?', (db.dumps(payload), ident))
    login(client, username)
    response = client.get('/api/public-sources')
    assert response.status_code == 200, response.text
    items = {item['id']: item for item in response.json()['items']}
    with db.connect() as c:
        registered = {row[0] for row in c.execute('SELECT id FROM sources')}
    assert set(items) == registered
    assert all(set(item) == {'id', 'name', 'url', 'kind', 'enabled', 'historical',
                            'availability', 'availability_label', 'availability_note', 'issues', 'collection_state'} for item in items.values())
    assert all(item['collection_state'] in ('success', 'failure', 'pending', 'paused') for item in items.values())
    assert all(items[source_id]['enabled'] is False for source_id in ('cecep', 'gfcl', 'custom_history'))
    assert items['custom_active']['url'] == 'https://example.com/'
    assert items['custom_active']['availability'] == 'unavailable'
    assert items['custom_unused']['availability'] == 'paused'
    assert items['grandblue']['availability'] == 'pending'
    assert items['cecep']['historical'] and items['gfcl']['historical']
    assert items['custom_history']['historical']
    assert not items['custom_unused']['historical'] and not items['cseg']['historical']
    assert all(marker not in response.text for marker in ('private-rule', 'private-source-note', 'private-source-error'))
    # The subscription catalogue keeps its separate availability rule and minimal shape.
    catalog = client.get('/api/source-catalog').json()['items']
    assert all(set(item) == {'id', 'name'} for item in catalog)
    assert {'cseg', 'custom_unused'} <= {item['id'] for item in catalog}
    management = client.get('/api/sources')
    assert management.status_code == (200 if username == 'admin' else 403)
    if username == 'admin':assert 'private-source-error' in management.text


@pytest.mark.parametrize('observed', ['not-json', {'source': 'cseg'}, '["cseg"]'])
def test_public_sources_ignore_legacy_non_array_observed_source_values(client, observed):
    ident = notice()
    with db.connect() as c:
        for source_id in ('cecep', 'cseg'):
            source = json.loads(c.execute('SELECT config FROM sources WHERE id=?', (source_id,)).fetchone()[0])
            source['enabled'] = False
            c.execute('UPDATE sources SET config=? WHERE id=?', (db.dumps(source), source_id))
        payload = json.loads(c.execute('SELECT payload FROM opportunities WHERE id=?', (ident,)).fetchone()[0])
        payload['observed_source_ids'] = observed
        c.execute('UPDATE opportunities SET payload=? WHERE id=?', (db.dumps(payload), ident))
    login(client, 'alice')
    response = client.get('/api/public-sources')
    assert response.status_code == 200, response.text
    items = {item['id']: item for item in response.json()['items']}
    assert items['cecep']['historical']
    assert not items['cseg']['historical']


@pytest.mark.parametrize('path', ['/feed?auth=private-url-token', '/feed?key=private-url-token', '/private-url-token/feed'])
def test_public_directory_shows_custom_site_homepage_without_data_channel_credentials(client, path):
    login(client, 'admin')
    created = client.post('/api/source-configs', json={
        'name': 'custom-private-channel', 'url': 'https://example.com' + path, 'enabled': False,
    })
    assert created.status_code == 200, created.text
    ident = created.json()['id']
    login(client, 'alice')
    response = client.get('/api/public-sources')
    item = next(item for item in response.json()['items'] if item['id'] == ident)
    assert item['url'] == 'https://example.com/'
    assert 'private-url-token' not in response.text
    login(client, 'admin')
    config = next(item for item in client.get('/api/sources').json()['items'] if item['id'] == ident)
    assert config['url'] == 'https://example.com' + path


def test_regular_dashboard_contains_public_statistics_and_data_freshness_only(client):
    ident = notice()
    last_seen = '2026-10-13T08:30:00+08:00'
    with db.connect() as c:
        c.execute('UPDATE opportunities SET last_seen_at=? WHERE id=?', (last_seen, ident))
        c.execute('INSERT INTO runs(started_at,status,details,error) VALUES(?,?,?,?)',
                  (db.now(), '部分完成', db.dumps([{'source': 'private-run-marker'}]), 'private-run-error'))
    login(client, 'alice')
    response = client.get('/api/dashboard')
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == {'stats', 'top_regions', 'category_counts', 'daily'}
    assert set(data['stats']) == {'total', 'actionable', 'high_priority', 'companies', 'last_update'}
    assert data['stats']['total'] == 1 and data['stats']['last_update'] == last_seen
    assert 'private-run-marker' not in response.text and 'private-run-error' not in response.text
    assert client.get('/api/opportunities').json()['total'] == 1
    assert client.get(f'/api/opportunities/{ident}').json()['ai_analysis']['summary'] == '已有公共解读'


def test_empty_regular_dashboard_has_no_data_update_time(client):
    login(client, 'alice')
    data = client.get('/api/dashboard').json()
    assert data['stats']['total'] == 0
    assert data['stats']['last_update'] is None


def test_regular_settings_expose_only_public_score_threshold(client):
    login(client, 'alice')
    response = client.get('/api/settings')
    assert response.status_code == 200, response.text
    assert response.json() == {'min_score': db.DEFAULT_SETTINGS['min_score']}


def test_regular_report_does_not_reveal_collection_errors(client):
    notice()
    with db.connect() as c:
        c.execute('UPDATE sources SET status=?,last_error=? WHERE id=?', ('异常', 'private-source-error', 'cecep'))
    login(client, 'alice')
    response = client.get('/api/report')
    assert response.status_code == 200, response.text
    assert response.json()['warnings'] == []
    assert 'private-source-error' not in response.text


@pytest.mark.parametrize('query', ['', '?subscription_id=0'])
def test_regular_public_ai_writes_are_denied_without_changing_shared_result(client, monkeypatch, query):
    ident = notice()
    monkeypatch.setattr(ai, 'explain', lambda item: {'summary': '不应写入公共库'})
    login(client, 'alice')
    response = client.post(f'/api/opportunities/{ident}/analyze{query}')
    assert response.status_code == 403, response.text
    assert client.get(f'/api/opportunities/{ident}').json()['ai_analysis'] == {'summary': '已有公共解读'}


def test_regular_users_can_analyze_only_own_topics_and_keep_private_followups(client, monkeypatch):
    ident = notice()
    foreign = client.post('/api/subscriptions', json={'name': '管理员专题', 'keywords': ['控制']}).json()['id']
    client.patch(f'/api/opportunities/{ident}', json={'status': '跟进中', 'note': 'admin test note'})
    login(client, 'alice')
    own = client.post('/api/subscriptions', json={'name': '我的专题', 'keywords': ['控制']}).json()['id']
    client.patch(f'/api/opportunities/{ident}', json={'status': '已联系', 'note': 'alice test note'})
    monkeypatch.setattr(ai, 'explain', lambda item: {'summary': '我的专题解读'})
    assert client.post(f'/api/opportunities/{ident}/analyze?subscription_id={foreign}').status_code == 404
    response = client.post(f'/api/opportunities/{ident}/analyze?subscription_id={own}')
    assert response.status_code == 200, response.text
    assert response.json()['ai_analysis']['summary'] == '我的专题解读'
    assert (response.json()['status'], response.json()['note']) == ('已联系', 'alice test note')
    assert client.get(f'/api/opportunities/{ident}').json()['ai_analysis'] == {'summary': '已有公共解读'}
    login(client, 'admin')
    detail = client.get(f'/api/opportunities/{ident}').json()
    assert (detail['status'], detail['note']) == ('跟进中', 'admin test note')


def test_admin_retains_management_data_and_public_ai_generation(client, monkeypatch):
    ident = notice()
    with db.connect() as c:
        c.execute('INSERT INTO runs(started_at,status,details) VALUES(?,?,?)', (db.now(), '完成', '[]'))
        c.execute('UPDATE sources SET status=?,last_error=? WHERE id=?', ('异常', 'private-source-error', 'cecep'))
    for endpoint in ['/api/sources', '/api/runs', '/api/collection-schedule', '/api/collection-progress']:
        assert client.get(endpoint).status_code == 200
    source = next(item for item in client.get('/api/sources').json()['items'] if item['id'] == 'cecep')
    assert source['url'] and source['last_error'] == 'private-source-error'
    dashboard = client.get('/api/dashboard').json()
    assert dashboard['latest_run']['status'] == '完成'
    assert {'last_run', 'next_run', 'sources_ok', 'sources_total'} <= set(dashboard['stats'])
    assert 'ai' in dashboard
    assert client.get('/api/settings').json()['interval_minutes'] == db.DEFAULT_SETTINGS['interval_minutes']
    assert any('private-source-error' in warning for warning in client.get('/api/report').json()['warnings'])
    monkeypatch.setattr(ai, 'explain', lambda item: {'summary': '管理员公共解读'})
    response = client.post(f'/api/opportunities/{ident}/analyze')
    assert response.status_code == 200, response.text
    login(client, 'alice')
    assert client.get(f'/api/opportunities/{ident}').json()['ai_analysis']['summary'] == '管理员公共解读'


def test_subscription_preview_describes_existing_public_notice_matching(client):
    notice()
    login(client, 'alice')
    response = client.post('/api/subscriptions/preview', json={'name': '控制专题', 'keywords': ['控制']})
    assert response.status_code == 200 and response.json()['total'] == 1
    assert '已入库' in response.json()['notice']
    assert '参与后续采集' not in response.json()['notice']
