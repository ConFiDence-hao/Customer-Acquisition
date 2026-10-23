"""Subscription changes request a real first check without falsifying run times."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db, main, subscriptions, workbench
from app.analysis import analyze


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(db, 'ROOT', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'testing-only-password')
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-32-characters-long')
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')
    stamp = datetime.now(db.TZ).isoformat(timespec='seconds')
    monkeypatch.setattr(db, 'now', lambda: stamp)
    main.FAILURES.clear()
    with TestClient(main.app) as client:
        assert client.post('/api/login', json={'username': 'admin', 'password': 'testing-only-password'}).status_code == 200
        for topic in client.get('/api/my-subscriptions').json()['items']:
            assert client.delete(f"/api/subscriptions/{topic['id']}").status_code == 200
        workbench.dispatch_due(force=True)
        yield client


def notice():
    item = analyze({
        'title': '独立燃烧控制系统改造招标', 'url': 'https://example.com/first-push',
        'source_id': 'cecep', 'source_name': '公开来源', 'content': '本项目采购独立燃烧控制系统。',
        'published_at': db.now(),
    }, [], db.DEFAULT_SETTINGS)
    assert db.upsert(item) == 'inserted'


def create_topic(client, **changes):
    response = client.post('/api/subscriptions', json={
        'name': '独立燃烧控制', 'keywords': ['独立燃烧控制'], **changes,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_new_topic_is_checked_before_interval_even_with_same_second_timestamp(client):
    notice()
    before = client.get('/api/automation').json()
    assert datetime.fromisoformat(before['next_run']) > datetime.now(db.TZ)
    topic = create_topic(client)
    assert topic['updated_at'] == before['last_run']
    workbench.dispatch_due()
    result = client.get('/api/pushes').json()
    assert result['total'] == 1 and result['items'][0]['title'] == '独立燃烧控制系统改造招标'
    task = client.get('/api/automation').json()
    assert task['pending_check'] is False and task['interval_minutes'] == 60
    assert datetime.fromisoformat(task['next_run']) == datetime.fromisoformat(task['last_run']) + timedelta(minutes=60)


def test_enabling_topic_checks_it_without_waiting_for_old_plan(client):
    notice()
    topic = create_topic(client, enabled=False)
    workbench.dispatch_due()
    assert client.get('/api/pushes').json()['total'] == 0
    assert client.put(f"/api/subscriptions/{topic['id']}", json={**topic, 'enabled': True}).status_code == 200
    workbench.dispatch_due()
    assert client.get('/api/pushes').json()['total'] == 1


def test_disabled_plan_keeps_pending_check_until_reenabled(client):
    notice()
    create_topic(client)
    assert client.put('/api/automation', json={'enabled': False, 'interval_minutes': 60}).status_code == 200
    workbench.dispatch_due()
    assert client.get('/api/pushes').json()['total'] == 0
    assert client.get('/api/automation').json()['pending_check'] is True
    assert client.put('/api/automation', json={'enabled': True, 'interval_minutes': 60}).status_code == 200
    workbench.dispatch_due()
    assert client.get('/api/pushes').json()['total'] == 1
    assert client.get('/api/automation').json()['pending_check'] is False


def test_change_during_matching_discards_stale_results_and_retries(client, monkeypatch):
    notice()
    topic = create_topic(client)
    last_run = client.get('/api/automation').json()['last_run']
    original = subscriptions.recommendations
    def change_after_matching(user, topics):
        matches = original(user, topics)
        response = client.put(f"/api/subscriptions/{topic['id']}", json={**topic, 'keywords': ['无关采购']})
        assert response.status_code == 200
        return matches
    monkeypatch.setattr(subscriptions, 'recommendations', change_after_matching)
    workbench.dispatch_due()
    assert client.get('/api/pushes').json()['total'] == 0
    assert client.get('/api/automation').json()['last_run'] == last_run
    assert client.get('/api/automation').json()['pending_check'] is True
    monkeypatch.setattr(subscriptions, 'recommendations', original)
    workbench.dispatch_due()
    assert client.get('/api/pushes').json()['total'] == 0
    assert client.get('/api/automation').json()['pending_check'] is False


def test_matching_failure_does_not_consume_pending_request(client, monkeypatch):
    notice()
    create_topic(client)
    original = subscriptions.recommendations
    def fail_matching(*args):
        raise RuntimeError('temporary test failure')
    monkeypatch.setattr(subscriptions, 'recommendations', fail_matching)
    with pytest.raises(RuntimeError, match='temporary test failure'):
        workbench.dispatch_due()
    assert client.get('/api/automation').json()['pending_check'] is True
    monkeypatch.setattr(subscriptions, 'recommendations', original)
    workbench.dispatch_due()
    assert client.get('/api/pushes').json()['total'] == 1


def test_new_topic_association_keeps_first_delivery_and_read_state(client):
    notice()
    first = create_topic(client)
    workbench.dispatch_due()
    item = client.get('/api/pushes').json()['items'][0]
    assert client.patch(f"/api/pushes/{item['push_id']}/read").status_code == 200
    original = client.get('/api/pushes').json()['items'][0]
    second = create_topic(client, name='第二个燃烧主题')
    workbench.dispatch_due()
    inbox = client.get('/api/pushes').json()
    assert inbox['total'] == 1
    current = inbox['items'][0]
    assert current['push_id'] == original['push_id'] and current['pushed_at'] == original['pushed_at']
    assert current['read_at'] == original['read_at']
    assert {topic['id'] for topic in current['associated_topics']} == {first['id'], second['id']}


@pytest.mark.parametrize('last_run_relation', ['older', 'same_second'])
def test_restart_backfills_legacy_pending_topic_once_without_resetting_last_run(client, last_run_relation):
    notice()
    create_topic(client)
    past = (datetime.now(db.TZ) - timedelta(minutes=10)).isoformat(timespec='seconds') if last_run_relation == 'older' else db.now()
    with db.connect() as connection:
        connection.execute('DELETE FROM subscription_checks WHERE user_id=1')
        connection.execute('UPDATE automations SET last_run=? WHERE user_id=1', (past,))
    db.initialize()
    task = client.get('/api/automation').json()
    assert task['last_run'] == past and task['pending_check'] is True
    workbench.dispatch_due()
    assert client.get('/api/pushes').json()['total'] == 1
    db.initialize()
    assert client.get('/api/automation').json()['pending_check'] is False


def test_admin_edit_requests_check_for_subscription_owner_not_actor(client):
    alice = client.post('/api/users', json={'username': 'alice', 'display_name': 'Alice', 'password': 'testing-user-password'}).json()
    workbench.dispatch_due(force=True)
    assert client.post('/api/login', json={'username': 'alice', 'password': 'testing-user-password'}).status_code == 200
    topic = create_topic(client, enabled=False)
    workbench.dispatch_due(force=True)
    notice()
    assert client.post('/api/login', json={'username': 'admin', 'password': 'testing-only-password'}).status_code == 200
    assert client.put(f"/api/subscriptions/{topic['id']}", json={**topic, 'enabled': True}).status_code == 200
    workbench.dispatch_due()
    assert client.get('/api/pushes').json()['total'] == 0
    assert client.post('/api/login', json={'username': 'alice', 'password': 'testing-user-password'}).status_code == 200
    assert client.get('/api/me').json()['id'] == alice['id']
    assert client.get('/api/pushes').json()['total'] == 1
