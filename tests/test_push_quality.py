"""The push gate keeps the inbox to fresh, open, title-relevant matches."""
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import clock, db, main, subscriptions, workbench
from app.analysis import analyze

pytestmark = pytest.mark.push_gate


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(db, 'ROOT', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'testing-only-password')
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-32-characters-long')
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')
    main.FAILURES.clear()
    with TestClient(main.app) as client:
        assert client.post('/api/login', json={'username': 'admin', 'password': 'testing-only-password'}).status_code == 200
        for topic in client.get('/api/my-subscriptions').json()['items']:
            assert client.delete(f"/api/subscriptions/{topic['id']}").status_code == 200
        yield client


def days_ago(days):
    return (clock.now() - timedelta(days=days)).isoformat(timespec='seconds')


def notice(title, url, published, content='本项目采购烟气净化设备。'):
    db.upsert(analyze({'title': title, 'url': 'https://example.com/' + url, 'source_id': 'cecep',
                       'source_name': '来源', 'source_kind': '集团采购', 'published_at': published, 'content': content},
                      [], db.DEFAULT_SETTINGS))


def topic(client, **changes):
    response = client.post('/api/subscriptions', json={'name': '烟气', 'keywords': ['烟气净化'], **changes})
    assert response.status_code == 200
    return response.json()


def pushed_titles(client):
    workbench.dispatch_due(force=True)
    return {x['title'] for x in client.get('/api/pushes?page_size=20').json()['items']}


def test_fresh_open_title_match_is_pushed_but_closed_and_old_are_not(client):
    notice('烟气净化系统改造招标公告', 'fresh', days_ago(2))
    notice('烟气净化系统改造中标结果公告', 'result', days_ago(2))
    notice('烟气净化系统改造招标公告（历史）', 'old', days_ago(60))
    topic(client)
    assert pushed_titles(client) == {'烟气净化系统改造招标公告'}


def test_body_only_hit_needs_core_title(client):
    notice('焚烧厂烟气提标改造工程招标', 'core', days_ago(1))
    notice('垃圾吊钢丝绳询比采购公告', 'routine', days_ago(1), '烟气净化采用SNCR+半干法；本次采购钢丝绳。')
    topic(client)
    assert pushed_titles(client) == {'焚烧厂烟气提标改造工程招标'}


def test_explicit_result_stage_is_still_pushed(client):
    notice('烟气净化系统改造中标结果公告', 'result', days_ago(2))
    topic(client, stages=['中标结果'])
    assert pushed_titles(client) == {'烟气净化系统改造中标结果公告'}


def test_diagnosis_counts_filtered_matches_separately(client):
    notice('烟气净化系统改造招标公告', 'fresh', days_ago(2))
    notice('烟气净化系统改造终止公告', 'stopped', days_ago(2))
    notice('烟气净化设备采购公告', 'old', days_ago(90))
    created = topic(client)
    result = client.get(f"/api/subscriptions/{created['id']}/diagnosis").json()
    assert result['counts']['matching'] == 3
    assert result['counts']['undelivered'] == 1
    assert result['counts']['push_filtered'] == 2
    reasons = {x['code']: x['count'] for x in result['push_filters']}
    assert reasons['closed'] == 1 and reasons['stale'] == 1


def test_read_all_marks_only_selected_topic(client):
    notice('烟气净化系统改造招标公告', 'a', days_ago(1))
    notice('DCS控制系统升级招标公告', 'b', days_ago(1), '控制系统升级')
    first = topic(client)
    topic(client, name='控制', keywords=['DCS'])
    workbench.dispatch_due(force=True)
    assert client.get('/api/pushes').json()['unread'] == 2
    result = client.patch(f"/api/pushes/read-all?subscription_id={first['id']}").json()
    assert result['updated'] == 1
    assert client.get('/api/pushes').json()['unread'] == 1
    assert client.patch('/api/pushes/read-all').json()['updated'] == 1
    assert client.get('/api/pushes').json()['unread'] == 0
    assert client.patch('/api/pushes/read-all?subscription_id=99999').status_code == 404
