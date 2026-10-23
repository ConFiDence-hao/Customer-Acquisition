"""The public directory includes planned sources without enabling collection."""
import json
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app import accounts, collectors, db, main


NEW_SOURCES = {
    'dynagreen': ('绿色动力', '集团采购'),
    'wangneng': ('旺能环境', '前期公示'),
    'junxin': ('军信股份', '集团采购'),
    'bece': ('首创环境', '集团采购'),
    'beeg': ('北控环保 · 集团环境业务入口', '集团采购'),
    'jilin': ('吉林公共资源交易', '公共资源'),
    'datong': ('大同公共资源交易', '公共资源'),
    'bjx-environment': ('北极星 · 环保', '行业门户'),
}


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(db, 'ROOT', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'testing-only-password')
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-32-characters-long')
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')
    main.FAILURES.clear()
    db.initialize()


def test_sources_are_registered_with_only_verified_adapters_enabled(library):
    collectors.register_sources()
    entries = {item['id']: item for item in collectors.source_rows()}
    assert set(NEW_SOURCES).issubset(entries)
    assert len(entries) == 29
    for ident, (name, kind) in NEW_SOURCES.items():
        item = entries[ident]
        assert item['name'] == name and item['kind'] == kind
        assert item['enabled'] == item['adapter_available']
        assert item['status'] == ('待采集' if item['adapter_available'] else '未接入')
        assert urlsplit(item['url']).scheme in ('http', 'https') and urlsplit(item['url']).hostname
        assert item['note'] and item['last_success'] is None
    assert {ident for ident, item in entries.items() if item['enabled']} == collectors.BUILTIN_SOURCE_IDS


def test_commercial_directory_entries_explicitly_require_authorization(library):
    collectors.register_sources()
    entries = {item['id']: item for item in collectors.source_rows()}
    for ident in ('jianyu', 'qianlima', 'chinabidding'):
        assert entries[ident]['requires_authorization'] is True
        assert entries[ident]['enabled'] is False
        assert entries[ident]['status'] == '未接入'
    assert entries['bidcenter']['requires_authorization'] is False
    assert entries['bidcenter']['enabled'] is True
    assert entries['bidi']['requires_authorization'] is False
    assert entries['bidi']['enabled'] is True


@pytest.mark.parametrize('ident', ['cecep', 'dynagreen'])
def test_registration_is_idempotent_and_preserves_admin_source_configuration(library, ident):
    config = dict(id=ident, name='管理员维护名称', url='https://example.com/admin-source', kind='集团采购',
                  enabled=False, note='保留管理员说明', include_keywords=['管理员关键词'], api_key='private-test-token')
    with db.connect() as connection:
        connection.execute('INSERT INTO sources(id,config,status,last_success,last_error,last_count) VALUES(?,?,?,?,?,?)',
                           (ident, db.dumps(config), '管理员停用', '2026-10-01', 'internal-test-error', 17))
    collectors.register_sources()
    collectors.register_sources()
    with db.connect() as connection:
        stored = connection.execute('SELECT * FROM sources WHERE id=?', (ident,)).fetchone()
        assert json.loads(stored['config']) == config
        assert (stored['status'], stored['last_success'], stored['last_error'], stored['last_count']) == ('管理员停用', '2026-10-01', 'internal-test-error', 17)
        assert connection.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 29


def test_regular_user_can_read_complete_directory_without_private_source_configuration(library):
    with db.connect() as connection:
        connection.execute('INSERT INTO users(username,display_name,password_hash,role,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                           ('alice', 'Alice', accounts.password_hash('a-strong-test-password'), 'user', db.now(), db.now()))
    with TestClient(main.app) as client:
        assert client.post('/api/login', json={'username': 'alice', 'password': 'a-strong-test-password'}).status_code == 200
        with db.connect() as connection:
            original = connection.execute('SELECT config FROM sources WHERE id=?', ('cecep',)).fetchone()[0]
            config = {**json.loads(original), 'api_key': 'private-test-token', 'item_selector': '.private-selector'}
            connection.execute('UPDATE sources SET config=?,last_error=? WHERE id=?',
                               (db.dumps(config), 'private-test-error', 'cecep'))
        response = client.get('/api/public-sources')
        assert response.status_code == 200
        items = response.json()['items']
        assert len(items) == 29
        assert set(NEW_SOURCES).issubset({item['id'] for item in items})
        assert {item['kind'] for item in items} == {'集团采购', '公共资源', '行业门户', '商业聚合', '前期公示'}
        assert all(set(item).isdisjoint({'config', 'api_key', 'item_selector', 'last_error'}) for item in items)
        assert 'private-test-token' not in response.text and 'private-test-error' not in response.text
        assert client.get('/api/sources').status_code == 403
        assert client.post('/api/collect').status_code == 403


def test_source_adapter_availability_is_shared_by_directory_and_topic_validation(library):
    collectors.register_sources()
    entries = {item['id']: item for item in collectors.source_rows()}
    assert entries['hbzhan']['adapter_available'] is True
    assert entries['jilin']['adapter_available'] is False
    with TestClient(main.app) as client:
        assert client.post('/api/login', json={'username': 'admin', 'password': 'testing-only-password'}).status_code == 200
        selectable = {item['id'] for item in client.get('/api/source-catalog').json()['items']}
        assert 'hbzhan' in selectable and 'jilin' not in selectable
        assert client.patch('/api/source-configs/hbzhan/state', json={'enabled': True}).status_code == 200


def test_public_partial_reason_never_exposes_raw_diagnostic_text(library):
    collectors.register_sources()
    with db.connect() as c:
        c.execute("UPDATE sources SET status='部分可用',last_error=? WHERE id='bjx'",
                  ('详情页要求访问验证 secret-diagnostic-token',))
    with TestClient(main.app) as client:
        assert client.post('/api/login', json={'username': 'admin', 'password': 'testing-only-password'}).status_code == 200
        response = client.get('/api/public-sources')
        item = next(item for item in response.json()['items'] if item['id'] == 'bjx')
        assert '访问验证' in item['availability_note']
        assert 'secret-diagnostic-token' not in response.text


@pytest.mark.parametrize('error,code,label', [
    ('详情页要求访问验证', 'browser_verification', '浏览器验证'),
    ('PDF正文要求登录或返回非PDF内容', 'login_required', '需要登录'),
    ('浏览器组件未安装，暂不可启动', 'browser_unavailable', '浏览器组件未就绪'),
    ('OCR识别失败，保留标题', 'ocr_failed', 'OCR识别失败'),
    ('扫描PDF无可提取正文，需人工阅读', 'image_body', '正文为图片'),
    ('公告正文容器缺失或内容不足', 'detail_missing', '详情正文未取得'),
    ('补充列表未读到，保留已取得候选', 'pagination_failed', '补页未完成'),
    ('本轮已达到浏览器渲染页数上限，保留列表线索', 'browser_budget_exhausted', '浏览器页数已达上限'),
])
def test_public_source_names_the_reading_problem_without_raw_errors(library, error, code, label):
    collectors.register_sources()
    with db.connect() as c:
        c.execute("UPDATE sources SET status='部分可用',last_error=? WHERE id='bjx'",
                  (error + ' https://private.example/secret-path?token=secret-marker',))
    item = next(item for item in main.public_sources()['items'] if item['id'] == 'bjx')
    assert item['availability'] == 'partial'
    assert item['availability_label'] == label
    assert [issue['code'] for issue in item['issues']] == [code]
    assert set(item['issues'][0]) == {'code', 'label', 'note'}
    assert 'secret-marker' not in json.dumps(item, ensure_ascii=False)
    assert 'secret-path' not in json.dumps(item, ensure_ascii=False)


def test_public_source_preserves_multiple_known_causes_and_discards_arbitrary_issue_fields(library, monkeypatch):
    source = {
        'id': 'custom_multi', 'name': '公开监测', 'url': 'https://example.com/private-token/feed',
        'kind': '自定义监测', 'custom': True, 'enabled': True, 'adapter_available': True,
        'status': '部分可用', 'last_error': 'OCR识别失败；补页未完成；private-raw-marker',
        'issues': [{'code': 'login_required', 'note': 'private-issue-marker', 'url': 'https://secret.example/'}],
    }
    monkeypatch.setattr(main, 'source_rows', lambda: [source])
    item = main.public_sources()['items'][0]
    assert 'issues' in item
    assert {issue['code'] for issue in item['issues']} == {'login_required', 'ocr_failed', 'pagination_failed'}
    assert item['url'] == 'https://example.com/'
    rendered = json.dumps(item, ensure_ascii=False)
    assert all(secret not in rendered for secret in ('private-token', 'private-raw-marker', 'private-issue-marker', 'secret.example'))


@pytest.mark.parametrize('adapted,expected', [(True, 'paused'), (False, 'pending')])
def test_disabled_source_does_not_present_legacy_runtime_error_as_current_problem(library, monkeypatch, adapted, expected):
    source = {
        'id': 'custom_old', 'name': '旧监测', 'url': 'https://example.com/',
        'enabled': False, 'adapter_available': adapted, 'status': '部分可用',
        'last_error': 'OCR识别失败 secret-old-error',
    }
    monkeypatch.setattr(main, 'source_rows', lambda: [source])
    item = main.public_sources()['items'][0]
    assert item['availability'] == expected
    assert 'issues' in item and item['issues'] == []
    assert 'OCR' not in item['availability_label'] + item['availability_note']


def test_public_source_unknown_problem_has_safe_specific_fallback(library, monkeypatch):
    source = {
        'id': 'unknown', 'name': '公开监测', 'url': 'https://example.com/',
        'enabled': True, 'adapter_available': True, 'status': '部分可用',
        'last_error': 'unrecognized secret-diagnostic-token',
    }
    monkeypatch.setattr(main, 'source_rows', lambda: [source])
    item = main.public_sources()['items'][0]
    assert item['availability_label'] == '读取未完成'
    assert item['issues'][0]['code'] == 'read_incomplete'
    assert 'secret-diagnostic-token' not in json.dumps(item, ensure_ascii=False)


def test_safe_failure_note_can_be_reused_by_collectors_without_exposing_details():
    from app import source_health
    note = source_health.failure_note({
        'last_error': '图片OCR识别失败 https://secret.example/key?token=private-marker',
    })
    assert 'OCR' in note
    assert 'secret.example' not in note and 'private-marker' not in note


def test_missing_ocr_runtime_is_distinguished_from_a_failed_recognition_attempt():
    from app import source_health
    health = source_health.public_health({
        'enabled': True, 'status': '部分可用',
        'last_error': 'OCR组件未就绪，图片正文保留原图查阅',
    })
    assert health['availability_label'] == 'OCR组件未就绪'
    assert {issue['code'] for issue in health['issues']} == {'ocr_unavailable', 'image_body'}
