import json

import pytest
from fastapi.testclient import TestClient

from app import db, main, workbench
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
        archive_default_topics(c)
        alice = c.post('/api/users', json={
            'username': 'alice', 'display_name': '华东业务 Alice', 'password': 'testing-user-password',
        })
        assert alice.status_code == 200, alice.text
        login(c, 'alice')
        archive_default_topics(c)
        login(c, 'admin')
        yield c


def login(client, username):
    response = client.post('/api/login', json={
        'username': username,
        'password': 'testing-only-password' if username == 'admin' else 'testing-user-password',
    })
    assert response.status_code == 200, response.text
    return client.get('/api/me').json()['id']


def archive_default_topics(client):
    # Team statistics below describe exactly the custom topics in each scenario.
    # Verify initial installation, then archive only marker-backed defaults using
    # their owner's API; the durable markers still prevent reinstallation.
    user_id = client.get('/api/me').json()['id']
    with db.connect() as connection:
        default_ids = {row['subscription_id'] for row in connection.execute(
            'SELECT subscription_id FROM subscription_defaults WHERE user_id=?', (user_id,),
        )}
    initial = client.get('/api/my-subscriptions').json()
    assert initial['total'] == 4 and {item['id'] for item in initial['items']} == default_ids
    for topic_id in default_ids:
        assert client.delete(f'/api/subscriptions/{topic_id}').status_code == 200
    assert client.get('/api/my-subscriptions').json()['total'] == 0


def topic(client, name='控制专题', keywords=None, **fields):
    response = client.post('/api/subscriptions', json={
        'name': name, 'keywords': keywords or ['控制'], **fields,
    })
    assert response.status_code == 200, response.text
    return response.json()['id']


def notice(suffix, title='控制系统采购公告'):
    item = analyze({
        'title': title, 'url': f'https://example.com/team/{suffix}',
        'source_id': 'cecep', 'source_name': '公开来源', 'source_kind': '集团采购',
        'content': '控制设备采购需求。', 'published_at': '2026-10-01',
    }, [], db.DEFAULT_SETTINGS)
    assert db.upsert(item) == 'inserted'
    with db.connect() as c:
        return c.execute('SELECT id FROM opportunities WHERE url=?', (item['url'],)).fetchone()[0]


def dashboard(client, **params):
    response = client.get('/api/team-dashboard', params=params)
    assert response.status_code == 200, response.text
    return response.json()


def store_push(user_id, ident, topic_ids, pushed_at, legacy=False, **private):
    payload = {'id': ident, 'title': '控制系统采购公告',
               'matched_topics' if legacy else 'associated_topics':
                   [{'id': topic_id, 'name': '历史主题'} for topic_id in topic_ids], **private}
    with db.connect() as c:
        c.execute('INSERT INTO pushes(user_id,opportunity_id,payload,pushed_at,read_at) VALUES(?,?,?,?,?)',
                  (user_id, ident, db.dumps(payload), pushed_at, 'read-private-token'))


@pytest.mark.parametrize('username,expected', [(None, 401), ('alice', 403)])
def test_team_dashboard_is_admin_only(client, username, expected):
    if username:
        login(client, username)
    else:
        client.post('/api/logout')
    assert client.get('/api/team-dashboard').status_code == expected


def test_team_dashboard_separates_team_topics_from_admin_personal_workspace(client):
    admin_id = client.get('/api/me').json()['id']
    admin_topic = topic(client, '管理员自己的主题')
    alice_id = login(client, 'alice')
    alice_topic = topic(client, '员工自己的主题', ['控制', 'DCS'], industry='工业', offering='优化控制')
    client.put('/api/automation', json={'enabled': False, 'interval_minutes': 120})
    login(client, 'admin')
    result = dashboard(client)
    assert result['summary'] == {
        'total_users': 2, 'subscribed_users': 2, 'total_topics': 2, 'enabled_topics': 2,
        'pushes_7d': 0, 'paused_automation_users': 1, 'no_match_topics': 2,
        'scheduler_enabled': False,
    }
    assert result['total'] == 2 and {item['id'] for item in result['items']} == {admin_topic, alice_topic}
    alice = next(item for item in result['items'] if item['id'] == alice_topic)
    assert (alice['user_id'], alice['owner_name'], alice['owner_username'], alice['owner_enabled']) == (
        alice_id, '华东业务 Alice', 'alice', True)
    assert alice['automation'] == {'enabled': False, 'interval_minutes': 120, 'last_run': None, 'last_count': 0}
    assert alice['industry'] == '工业' and alice['offering'] == '优化控制'
    admin = next(item for item in result['items'] if item['id'] == admin_topic)
    assert admin['automation'] == {'enabled': True, 'interval_minutes': 60, 'last_run': None, 'last_count': 0}
    assert {u['id'] for u in result['users']} == {admin_id, alice_id}
    assert client.get('/api/my-subscriptions').json()['total'] == 1
    assert client.get('/api/my-subscriptions').json()['items'][0]['id'] == admin_topic
    assert client.get('/api/pushes').json()['total'] == 0
    assert client.get('/api/followups').json()['total'] == 0


def test_team_delivery_counts_deduplicate_announcements_and_preserve_first_delivery_time(client, monkeypatch):
    monkeypatch.setattr(db, 'now', lambda: '2026-10-13T12:00:00+08:00')
    admin_id = client.get('/api/me').json()['id']
    admin_topic = topic(client, '管理员控制')
    alice_id = login(client, 'alice')
    first_topic = topic(client, '控制主题')
    second_topic = topic(client, '采购主题', ['采购'])
    ident = notice('shared')
    older = notice('older')
    store_push(alice_id, ident, [first_topic, first_topic, second_topic, admin_topic], '2026-10-13T09:00:00+08:00')
    store_push(admin_id, ident, [admin_topic], '2026-10-06T12:00:00+08:00', legacy=True)
    store_push(admin_id, older, [admin_topic], '2026-10-06T11:59:59+08:00', legacy=True)
    login(client, 'admin')
    result = dashboard(client)
    assert result['summary']['pushes_7d'] == 2
    items = {item['id']: item for item in result['items']}
    assert (items[first_topic]['push_count'], items[second_topic]['push_count']) == (1, 1)
    assert items[first_topic]['pushes_7d'] == items[second_topic]['pushes_7d'] == 1
    assert items[first_topic]['last_push'] == '2026-10-13T09:00:00+08:00'
    assert items[admin_topic]['push_count'] == 2 and items[admin_topic]['pushes_7d'] == 1
    assert items[admin_topic]['last_push'] == '2026-10-06T12:00:00+08:00'
    assert all(item['match_count'] == 2 for item in items.values())
    assert client.get('/api/pushes').json()['total'] == 2


def test_team_dashboard_retains_disabled_owners_and_topics_but_ignores_archived_topics(client):
    inactive_topic = topic(client, '停用主题', enabled=False)
    deleted_topic = topic(client, '已归档主题')
    assert client.delete(f'/api/subscriptions/{deleted_topic}').status_code == 200
    alice_id = login(client, 'alice')
    disabled_owner_topic = topic(client, '停用账号的主题')
    notice('active')
    login(client, 'admin')
    assert client.patch(f'/api/users/{alice_id}', json={'enabled': False}).status_code == 200
    result = dashboard(client)
    assert result['summary']['total_users'] == 2 and result['summary']['subscribed_users'] == 2
    assert result['summary']['total_topics'] == 2 and result['summary']['enabled_topics'] == 0
    assert result['summary']['no_match_topics'] == result['summary']['paused_automation_users'] == 0
    assert {item['id'] for item in result['items']} == {inactive_topic, disabled_owner_topic}
    assert all(item['recall_status'] == 'paused' and item['match_count'] == 0 for item in result['items'])
    assert next(item for item in result['items'] if item['id'] == disabled_owner_topic)['owner_enabled'] is False
    assert dashboard(client, status='disabled')['total'] == 2
    assert dashboard(client, status='enabled')['total'] == 0


def test_team_recall_uses_enabled_adapted_sources_and_distinguishes_empty_library(client):
    no_matches = topic(client, '等待公共数据', ['不存在的词'])
    unavailable = topic(client, '停用来源', source_ids=['cecep'])
    custom_topic = topic(client, '自定义来源', source_ids=[])
    historical = notice('historical')
    with db.connect() as c:
        c.execute("UPDATE sources SET config=json_set(config,'$.enabled',json('false')) WHERE id='cecep'")
        # An enabled reference entry still has no adapter and cannot count as a usable collector.
        c.execute("UPDATE sources SET config=json_set(config,'$.enabled',json('true')) WHERE id='jilin'")
        custom = {'id': 'custom_team', 'name': '自定义公开来源', 'enabled': True, 'custom': True,
                  'adapter': 'html', 'note': 'private-source-token', 'url': 'https://example.com/list'}
        c.execute('INSERT INTO sources(id,config,status) VALUES(?,?,?)', ('custom_team', db.dumps(custom), '待采集'))
        c.execute('UPDATE subscriptions SET config=json_set(config,\'$.source_ids\',json(?)) WHERE id=?',
                  (json.dumps(['custom_team']), custom_topic))
    result = dashboard(client)
    items = {item['id']: item for item in result['items']}
    from app.collectors import BUILTIN_SOURCE_IDS
    assert items[no_matches]['collection_source_count'] == len(BUILTIN_SOURCE_IDS)
    assert items[no_matches]['recall_status'] == 'no_matches' and items[no_matches]['match_count'] == 0
    assert items[unavailable]['collection_source_count'] == 0
    assert items[unavailable]['recall_status'] == 'source_unavailable' and items[unavailable]['match_count'] == 1
    assert items[custom_topic]['collection_source_count'] == 1
    assert items[custom_topic]['recall_status'] == 'no_matches'
    assert result['summary']['no_match_topics'] == 2
    assert dashboard(client, status='source_unavailable')['items'][0]['id'] == unavailable
    assert dashboard(client, status='no_matches')['total'] == 2
    assert client.get(f'/api/opportunities/{historical}').status_code == 200
    with db.connect() as c:
        c.execute("UPDATE sources SET config=json_set(config,'$.enabled',json('false'))")
    result = dashboard(client)
    assert result['summary']['no_match_topics'] == 0
    assert all(item['recall_status'] == 'source_unavailable' for item in result['items'])


def test_team_dashboard_filters_before_paging_and_keeps_summary_global(client):
    admin_topic = topic(client, '管理员专题', ['其他'])
    alice_id = login(client, 'alice')
    ids = [topic(client, f'业务主题 {n:02d}', ['DCS', '控制'], enabled=n != 0) for n in range(12)]
    client.put('/api/automation', json={'enabled': False, 'interval_minutes': 30})
    with db.connect() as c:
        for n, ident in enumerate(ids):
            c.execute('UPDATE subscriptions SET updated_at=? WHERE id=?', (f'2026-10-13T08:{59-n:02d}:00+08:00', ident))
        c.execute('UPDATE subscriptions SET updated_at=? WHERE id=?', ('2026-10-01T08:00:00+08:00', admin_topic))
    login(client, 'admin')
    all_topics = dashboard(client)
    filtered = dashboard(client, q='dcs', owner_id=alice_id, status='enabled', page=2, page_size=5)
    assert filtered['summary'] == all_topics['summary']
    assert (filtered['total'], filtered['page'], filtered['page_size']) == (11, 2, 5)
    assert [item['id'] for item in filtered['items']] == ids[6:11]
    assert dashboard(client, q='华东业务')['total'] == 12
    assert dashboard(client, q='ALICE')['total'] == 12
    assert dashboard(client, q='业务主题 11')['items'][0]['id'] == ids[11]
    assert dashboard(client, status='automation_paused')['total'] == 11
    assert dashboard(client, status='disabled')['items'][0]['id'] == ids[0]
    assert dashboard(client, owner_id=99999)['total'] == 0
    assert dashboard(client, page=10000)['items'] == []
    assert len(dashboard(client, page_size=20)['items']) == 13


@pytest.mark.parametrize('query', [
    'page=0', 'page=10001', 'page=99999999999999999999999999999',
    'page_size=4', 'page_size=7', 'page_size=21', 'owner_id=0', 'owner_id=-1',
    'status=unknown', 'q=' + 'x' * 151,
])
def test_team_dashboard_rejects_invalid_filters(client, query):
    assert client.get('/api/team-dashboard?' + query).status_code == 422


def test_team_dashboard_does_not_write_rows_or_leak_private_data(client, monkeypatch):
    ident = notice('private')
    admin_id = client.get('/api/me').json()['id']
    admin_topic = topic(client, '管理员主题')
    alice_id = login(client, 'alice')
    alice_topic = topic(client, '员工主题')
    client.patch(f'/api/opportunities/{ident}', json={'status': '已排除', 'note': 'followup-private-token'})
    with db.connect() as c:
        c.execute('INSERT INTO topic_analyses VALUES(?,?,?,?,?,?,?)',
                  (alice_id, alice_topic, ident, 'hash', 'topic-hash', db.dumps({'summary': 'ai-private-token'}), db.now()))
        c.execute('INSERT INTO model_settings VALUES(1,?)', (db.dumps({'encrypted_key': 'model-private-token'}),))
        c.execute('UPDATE opportunities SET payload=json_set(payload,\'$.ai_analysis\',json(?)) WHERE id=?',
                  (db.dumps({'summary': 'public-ai-not-needed-token'}), ident))
    store_push(alice_id, ident, [alice_topic], db.now(), note='push-note-private-token', ai='push-ai-private-token')
    login(client, 'admin')

    def database_snapshot():
        with db.connect() as c:
            tables = [row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {table: [tuple(row) for row in c.execute('SELECT * FROM "' + table + '" ORDER BY rowid')] for table in tables}

    before = database_snapshot()
    response = client.get('/api/team-dashboard')
    assert response.status_code == 200, response.text
    assert database_snapshot() == before
    result = response.json()
    assert result['summary']['scheduler_enabled'] is False
    assert result['summary']['paused_automation_users'] == 0
    expected = {'id', 'user_id', 'name', 'industry', 'offering', 'keywords', 'exclude_keywords',
                'provinces', 'stages', 'source_ids', 'enabled', 'created_at', 'updated_at', 'owner_name',
                'owner_username', 'owner_enabled', 'automation', 'match_count', 'push_count', 'pushes_7d',
                'last_push', 'collection_source_count', 'recall_status'}
    assert all(set(item) == expected for item in result['items'])
    assert all(set(user) == {'id', 'username', 'display_name', 'enabled'} for user in result['users'])
    assert all(set(source) == {'id', 'name', 'enabled'} for source in result['sources'])
    for secret in ['followup-private-token', 'ai-private-token', 'model-private-token',
                   'public-ai-not-needed-token', 'push-note-private-token', 'push-ai-private-token',
                   'read-private-token', 'password_hash', 'token_hash', 'radar_session', 'last_error']:
        assert secret not in response.text
    assert {item['id']: item['match_count'] for item in result['items']} == {admin_topic: 1, alice_topic: 1}
    assert client.get('/api/followups').json()['total'] == 0
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM automations').fetchone()[0] == 0
    monkeypatch.setenv('SCHEDULER_ENABLED', 'true')
    assert dashboard(client)['summary']['scheduler_enabled'] is True
    assert dashboard(client)['summary']['paused_automation_users'] == 0
