"""Default business topics are personal records, installed once per account."""
import pytest
from fastapi.testclient import TestClient

from app import db, main


NAMES = {
    '智慧焚烧与燃烧优化', '烟气净化与排放提标',
    '自动化控制与智慧管控', '飞灰处置',
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(db, 'ROOT', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'testing-only-password')
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-32-characters-long')
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')
    main.FAILURES.clear()
    with TestClient(main.app) as client:
        assert client.post('/api/login', json={
            'username': 'admin', 'password': 'testing-only-password',
        }).status_code == 200
        yield client


def create_user(client, username='alice'):
    response = client.post('/api/users', json={
        'username': username, 'display_name': username,
        'password': 'testing-user-password',
    })
    assert response.status_code == 200, response.text
    return response.json()['id']


def login_user(client, username='alice'):
    assert client.post('/api/login', json={
        'username': username, 'password': 'testing-user-password',
    }).status_code == 200


def test_initial_admin_and_new_employee_receive_four_owned_topics(client):
    admin = client.get('/api/my-subscriptions').json()['items']
    assert {topic['name'] for topic in admin} == NAMES
    assert all(topic['user_id'] == 1 and topic['enabled'] for topic in admin)
    alice_id = create_user(client)
    login_user(client)
    topics = client.get('/api/subscriptions').json()['items']
    assert {topic['name'] for topic in topics} == NAMES
    assert all(topic['user_id'] == alice_id and topic['enabled'] for topic in topics)
    by_name = {topic['name']: topic for topic in topics}
    assert by_name['智慧焚烧与燃烧优化']['keywords'] == [
        '智慧焚烧', 'AI智能燃烧', '智能燃烧控制', '智能优化控制',
        'APC', '锅炉优化', '炉膛火焰图像',
    ]
    assert by_name['烟气净化与排放提标']['keywords'] == [
        '烟气超低排放', '烟气提标改造', '烟气净化', 'SCR脱硝', 'NOx提标',
    ]
    assert by_name['自动化控制与智慧管控']['keywords'] == ['DCS组态', '智慧管控']
    assert by_name['飞灰处置']['keywords'] == ['飞灰处置']
    assert all(topic['industry'] == '垃圾焚烧发电' for topic in topics)


def test_restart_preserves_renamed_disabled_and_archived_defaults(client):
    create_user(client)
    login_user(client)
    topics = client.get('/api/subscriptions').json()['items']
    assert len(topics) == 4
    first, second = topics[:2]
    edited = {**first, 'name': '我的自定义燃烧业务', 'keywords': ['自定义关键词'], 'enabled': False}
    assert client.put(f"/api/subscriptions/{first['id']}", json=edited).status_code == 200
    assert client.delete(f"/api/subscriptions/{second['id']}").status_code == 200
    before = client.get('/api/subscriptions').json()['items']
    db.initialize()
    assert client.get('/api/subscriptions').json()['items'] == before
    assert len(before) == 3
    saved = next(topic for topic in before if topic['id'] == first['id'])
    assert saved['name'] == '我的自定义燃烧业务' and saved['keywords'] == ['自定义关键词']
    assert saved['enabled'] is False


def test_legacy_accounts_receive_missing_defaults_without_overwriting_existing_topics(client):
    alice_id = create_user(client)
    login_user(client)
    with db.connect() as connection:
        connection.execute('DELETE FROM subscriptions WHERE user_id=?', (alice_id,))
        connection.execute('DELETE FROM subscription_defaults WHERE user_id=?', (alice_id,))
        original = {
            'name': '飞灰处置', 'industry': '自定义行业', 'keywords': ['个人飞灰词'],
            'enabled': False, 'source_ids': ['cecep'],
        }
        topic_id = connection.execute(
            'INSERT INTO subscriptions(user_id,config,enabled,created_at,updated_at) VALUES(?,?,0,?,?)',
            (alice_id, db.dumps(original), db.now(), db.now()),
        ).lastrowid
    db.initialize()
    topics = client.get('/api/subscriptions').json()['items']
    assert len(topics) == 4 and {topic['name'] for topic in topics} == NAMES
    custom = next(topic for topic in topics if topic['id'] == topic_id)
    assert custom['keywords'] == ['个人飞灰词'] and not custom['enabled']
    assert custom['industry'] == '自定义行业' and custom['source_ids'] == ['cecep']
    db.initialize()
    assert client.get('/api/subscriptions').json()['items'] == topics


def test_archived_matching_legacy_topic_is_not_restored(client):
    alice_id = create_user(client)
    login_user(client)
    with db.connect() as connection:
        connection.execute('DELETE FROM subscription_defaults WHERE user_id=?', (alice_id,))
    topic = next(topic for topic in client.get('/api/subscriptions').json()['items'] if topic['name'] == '飞灰处置')
    assert client.delete(f"/api/subscriptions/{topic['id']}").status_code == 200
    db.initialize()
    assert '飞灰处置' not in {topic['name'] for topic in client.get('/api/subscriptions').json()['items']}
    assert client.get('/api/my-subscriptions').json()['total'] == 3


def test_duplicate_account_does_not_duplicate_default_subscriptions(client):
    alice_id = create_user(client)
    response = client.post('/api/users', json={
        'username': 'ALICE', 'display_name': 'duplicate', 'password': 'testing-user-password',
    })
    assert response.status_code == 409
    login_user(client)
    assert len(client.get('/api/subscriptions').json()['items']) == 4
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM subscriptions WHERE user_id=?', (alice_id,)).fetchone()[0] == 4


def test_default_topics_do_not_consume_existing_personal_subscription_quota(client):
    alice_id = create_user(client)
    login_user(client)
    with db.connect() as connection:
        for index in range(49):
            connection.execute(
                'INSERT INTO subscriptions(user_id,config,enabled,created_at,updated_at) VALUES(?,?,1,?,?)',
                (alice_id, db.dumps({'name': f'legacy-{index}', 'keywords': [f'legacy-{index}']}), db.now(), db.now()),
            )
    created = client.post('/api/subscriptions', json={'name': 'last-personal-slot', 'keywords': ['last-personal-slot']})
    assert created.status_code == 200, created.text
    assert len(client.get('/api/subscriptions').json()['items']) == 54
    assert client.post('/api/subscriptions', json={'name': 'over-quota', 'keywords': ['over-quota']}).status_code == 409
