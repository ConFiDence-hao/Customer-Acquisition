from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app import ai, db, main, workbench
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
        for username, name in [('alice', 'Alice 业务'), ('bobby', 'Bobby 业务')]:
            response = c.post('/api/users', json={
                'username': username, 'display_name': name, 'password': 'testing-user-password',
            })
            assert response.status_code == 200, response.text
        yield c


def login(client, username):
    response = client.post('/api/login', json={
        'username': username,
        'password': 'testing-only-password' if username == 'admin' else 'testing-user-password',
    })
    assert response.status_code == 200, response.text
    return client.get('/api/me').json()['id']


def notice(suffix, title='控制系统采购公告'):
    raw = {'title': title, 'url': f'https://example.com/team-followups/{suffix}',
           'source_id': 'cecep', 'source_name': '公开来源', 'source_kind': '集团采购',
           'published_at': '2026-10-13', 'content': '控制系统采购需求。'}
    assert db.upsert(analyze(raw, [], db.DEFAULT_SETTINGS)) == 'inserted'
    with db.connect() as c:
        return c.execute('SELECT id FROM opportunities WHERE url=?', (raw['url'],)).fetchone()[0]


def save(client, ident, status='跟进中', note=''):
    response = client.patch(f'/api/opportunities/{ident}', json={'status': status, 'note': note})
    assert response.status_code == 200, response.text
    return response.json()


def claim(client, ident):
    response = client.post(f'/api/opportunities/{ident}/claim')
    assert response.status_code == 200, response.text
    return response.json()


def assign(client, ident, user_id):
    response = client.put(f'/api/opportunities/{ident}/assignee', json={'user_id': user_id})
    assert response.status_code == 200, response.text
    return response.json()


def team_list(client, **params):
    response = client.get('/api/team-followups', params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_existing_private_followups_never_automatically_create_public_ownership(client, monkeypatch):
    ident = notice('private-only')
    login(client, 'alice')
    saved = save(client, ident, '已联系', '仅 Alice 可见')
    assert saved.get('team_followup') is None
    assert saved.get('is_team_owner') is False
    topic = client.post('/api/subscriptions', json={'name': '控制专题', 'keywords': ['控制']}).json()['id']
    monkeypatch.setattr(ai, 'explain', lambda item: {'summary': '个人 AI token'})
    assert client.post(f'/api/opportunities/{ident}/analyze?subscription_id={topic}').status_code == 200
    login(client, 'admin')
    assert team_list(client)['total'] == 0
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM team_followups').fetchone()[0] == 0
    db.initialize()
    assert team_list(client)['total'] == 0
    assert client.get(f'/api/opportunities/{ident}').json()['team_followup'] is None


def test_claim_preserves_private_notes_and_is_idempotent_without_resetting_shared_stage(client, monkeypatch):
    ident = notice('claim')
    alice_id = login(client, 'alice')
    save(client, ident, '已排除', 'alice-private-token')
    monkeypatch.setattr(db, 'now', lambda: '2026-10-13T10:00:00+08:00')
    first = claim(client, ident)
    expected = {'owner_id': alice_id, 'owner_name': 'Alice 业务', 'owner_enabled': True,
                'status': '跟进中', 'claimed_at': '2026-10-13T10:00:00+08:00',
                'updated_at': '2026-10-13T10:00:00+08:00', 'owner_revision': 1, 'assignment_source': 'claim'}
    assert first['team_followup'] == expected
    assert first['status'] == '跟进中' and first['note'] == 'alice-private-token'
    assert first['has_followup'] is True and first['is_team_owner'] is True
    saved = save(client, ident, '已联系', 'alice-private-token')
    assert saved['team_followup']['status'] == '已联系'
    with db.connect() as c:
        before = [tuple(row) for row in c.execute('SELECT * FROM audit ORDER BY id')]
    monkeypatch.setattr(db, 'now', lambda: '2026-10-13T10:30:00+08:00')
    repeated = claim(client, ident)
    assert repeated['team_followup'] == saved['team_followup']
    assert repeated['status'] == '已联系'
    with db.connect() as c:
        assert [tuple(row) for row in c.execute('SELECT * FROM audit ORDER BY id')] == before


def test_concurrent_claims_have_exactly_one_owner(client):
    ident = notice('race')
    sessions = []
    for username in ['alice', 'bobby']:
        uid = login(client, username)
        sessions.append((uid, client.cookies.get('radar_session')))
    barrier = Barrier(2)

    def request_claim(session):
        requester = TestClient(main.app)
        requester.cookies.set('radar_session', session[1])
        try:
            barrier.wait(timeout=10)
            response = requester.post(f'/api/opportunities/{ident}/claim')
            return session[0], response.status_code
        finally:
            requester.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(request_claim, sessions))
    assert sorted(status for _, status in results) == [200, 409]
    winner = next(uid for uid, status in results if status == 200)
    with db.connect() as c:
        assert c.execute('SELECT owner_id FROM team_followups WHERE opportunity_id=?', (ident,)).fetchone()[0] == winner
        assert c.execute('SELECT COUNT(*) FROM followups WHERE opportunity_id=?', (ident,)).fetchone()[0] == 1


def test_nonowners_cannot_claim_release_or_assign_and_only_change_their_private_record(client):
    ident = notice('non-owner')
    alice_id = login(client, 'alice')
    original = claim(client, ident)['team_followup']
    bobby_id = login(client, 'bobby')
    assert client.post(f'/api/opportunities/{ident}/claim').status_code == 409
    assert client.delete(f'/api/opportunities/{ident}/claim').status_code == 403
    assert client.put(f'/api/opportunities/{ident}/assignee', json={'user_id': bobby_id}).status_code == 403
    current = save(client, ident, '已归档', 'bobby-private-token')
    assert current['team_followup'] == original and current['status'] == '已归档'
    assert current['is_team_owner'] is False
    login(client, 'admin')
    current = client.get(f'/api/opportunities/{ident}').json()
    assert current['team_followup']['owner_id'] == alice_id
    assert current['note'] == '' and current['status'] == '待研判'
    assert 'bobby-private-token' not in client.get('/api/team-followups').text


def test_owner_release_keeps_private_followup_and_records_audit(client):
    ident = notice('release')
    login(client, 'alice')
    claim(client, ident)
    save(client, ident, '已联系', 'keep-after-release-token')
    response = client.delete(f'/api/opportunities/{ident}/claim')
    assert response.status_code == 200, response.text
    assert response.json()['team_followup'] is None
    detail = client.get(f'/api/opportunities/{ident}').json()
    assert detail['status'] == '已联系' and detail['note'] == 'keep-after-release-token'
    assert detail['has_followup'] is True and detail['is_team_owner'] is False
    assert client.get('/api/followups').json()['total'] == 1
    with db.connect() as c:
        actions = [row['action'] for row in c.execute('SELECT action FROM audit ORDER BY id')]
        assert '领取线索' in actions and '放弃线索' in actions
    login(client, 'bobby')
    assert claim(client, ident)['team_followup']['owner_name'] == 'Bobby 业务'


def test_admin_assignment_and_transfer_leave_target_private_records_untouched(client, monkeypatch):
    # Keep private saves earlier than the explicit assignment/transfer timestamps.
    monkeypatch.setattr(db, 'now', lambda: '2026-10-13T10:00:00+08:00')
    ident = notice('assignment')
    alice_id = login(client, 'alice')
    saved = save(client, ident, '已归档', 'alice-original-note-token')
    bobby_id = login(client, 'bobby')
    save(client, ident, '已排除', 'bobby-original-note-token')
    with db.connect() as c:
        before = [tuple(row) for row in c.execute('SELECT * FROM followups ORDER BY user_id')]
    login(client, 'admin')
    monkeypatch.setattr(db, 'now', lambda: '2026-10-13T11:00:00+08:00')
    assigned = assign(client, ident, alice_id)
    assert assigned['team_followup']['owner_id'] == alice_id
    assert assigned['team_followup']['status'] == '跟进中'
    assert assigned['note'] == '' and assigned['has_followup'] is False
    with db.connect() as c:
        assert [tuple(row) for row in c.execute('SELECT * FROM followups ORDER BY user_id')] == before
    login(client, 'alice')
    detail = client.get(f'/api/opportunities/{ident}').json()
    assert detail['status'] == '跟进中' and detail['note'] == 'alice-original-note-token'
    assert detail['followup_updated_at'] == '2026-10-13T11:00:00+08:00'
    assert detail['has_followup'] is True and detail['is_team_owner'] is True
    assert client.get('/api/followups?status=跟进中').json()['total'] == 1
    assert client.get('/api/followups?status=已归档').json()['total'] == 0
    save(client, ident, '已联系', 'alice-new-private-note')
    login(client, 'admin')
    unchanged = client.get(f'/api/opportunities/{ident}').json()['team_followup']
    assert assign(client, ident, alice_id)['team_followup'] == unchanged
    monkeypatch.setattr(db, 'now', lambda: '2026-10-13T11:30:00+08:00')
    transferred = assign(client, ident, bobby_id)
    assert transferred['team_followup']['owner_id'] == bobby_id and transferred['team_followup']['status'] == '已联系'
    login(client, 'bobby')
    assert client.get(f'/api/opportunities/{ident}').json()['status'] == '已联系'
    assert client.get(f'/api/opportunities/{ident}').json()['note'] == 'bobby-original-note-token'
    login(client, 'alice')
    assert client.get(f'/api/opportunities/{ident}').json()['status'] == '已联系'
    assert client.get(f'/api/opportunities/{ident}').json()['note'] == 'alice-new-private-note'
    assert saved['status'] == '已归档'


def test_assigned_notice_without_personal_save_enters_my_followups_and_same_titles_stay_separate(client):
    first = notice('same-a')
    second = notice('same-b')
    login(client, 'alice')
    alice_id = client.get('/api/me').json()['id']
    login(client, 'admin')
    assign(client, first, alice_id)
    login(client, 'alice')
    listed = client.get('/api/followups').json()
    assert listed['total'] == 1
    item = listed['items'][0]
    assert item['id'] == first and item['has_followup'] is False and item['is_team_owner'] is True
    assert item['note'] == '' and item['status'] == '跟进中'
    assert client.get(f'/api/opportunities/{second}').json()['team_followup'] is None
    save(client, first, '已联系', 'saved after assignment')
    assert client.get('/api/followups').json()['total'] == 1
    login(client, 'admin')
    assert client.delete(f'/api/opportunities/{first}/claim').status_code == 200
    assign(client, second, alice_id)
    assert client.delete(f'/api/opportunities/{second}/claim').status_code == 200
    login(client, 'alice')
    assert client.get('/api/followups').json()['total'] == 1
    assert client.get('/api/followups').json()['items'][0]['id'] == first


def test_owner_save_updates_today_push_my_followup_and_keeps_delivery_snapshot_immutable(client, monkeypatch):
    ident = notice('sync')
    alice_id = login(client, 'alice')
    client.post('/api/subscriptions', json={'name': '控制主题', 'keywords': ['控制']})
    workbench.dispatch_due(force=True)
    pushed = client.get('/api/pushes').json()['items'][0]
    client.patch(f"/api/pushes/{pushed['push_id']}/read")
    with db.connect() as c:
        original = dict(c.execute('SELECT * FROM pushes WHERE id=?', (pushed['push_id'],)).fetchone())
    claim(client, ident)
    monkeypatch.setattr(db, 'now', lambda: '2026-10-13T12:00:00+08:00')
    saved = save(client, ident, '已联系', 'owner-private-token')
    expected = saved['team_followup']
    assert expected['owner_id'] == alice_id and expected['status'] == '已联系'
    assert expected['updated_at'] == '2026-10-13T12:00:00+08:00'
    details = [client.get(f'/api/opportunities/{ident}').json(),
               client.get('/api/opportunities?min_score=0').json()['items'][0],
               client.get('/api/followups').json()['items'][0],
               client.get('/api/pushes').json()['items'][0]]
    assert all(item['team_followup'] == expected and item['status'] == '已联系' for item in details)
    with db.connect() as c:
        assert dict(c.execute('SELECT * FROM pushes WHERE id=?', (pushed['push_id'],)).fetchone()) == original
    login(client, 'bobby')
    item = client.get(f'/api/opportunities/{ident}').json()
    assert item['team_followup'] == expected and item['status'] == '待研判' and item['note'] == ''
    login(client, 'admin')
    assert team_list(client)['items'][0]['team_followup'] == expected
    assert 'owner-private-token' not in client.get('/api/team-followups').text


def test_disabled_owner_remains_visible_until_admin_releases_or_reassigns(client):
    ident = notice('disabled-owner')
    alice_id = login(client, 'alice')
    claim(client, ident)
    bobby_id = login(client, 'bobby')
    login(client, 'admin')
    assert client.patch(f'/api/users/{alice_id}', json={'enabled': False}).status_code == 200
    team = client.get(f'/api/opportunities/{ident}').json()['team_followup']
    assert team['owner_id'] == alice_id and team['owner_enabled'] is False
    assert client.put(f'/api/opportunities/{ident}/assignee', json={'user_id': alice_id}).status_code == 409
    assert assign(client, ident, bobby_id)['team_followup']['owner_enabled'] is True
    assert client.delete(f'/api/opportunities/{ident}/claim').status_code == 200
    assert team_list(client)['total'] == 0


def test_my_followup_effective_state_and_latest_personal_or_team_time_drive_filter_and_order(client):
    first = notice('sort-a')
    second = notice('sort-b')
    third = notice('sort-c')
    alice_id = login(client, 'alice')
    save(client, first, '已归档', 'personal-first')
    save(client, second, '已联系', 'personal-second')
    with db.connect() as c:
        c.execute('UPDATE followups SET updated_at=? WHERE opportunity_id=?', ('2026-10-01T08:00:00+08:00', first))
        c.execute('UPDATE followups SET updated_at=? WHERE opportunity_id=?', ('2026-10-05T08:00:00+08:00', second))
    login(client, 'admin')
    assign(client, first, alice_id)
    assign(client, third, alice_id)
    with db.connect() as c:
        c.execute('UPDATE team_followups SET updated_at=? WHERE opportunity_id=?', ('2026-10-06T08:00:00+08:00', first))
        c.execute('UPDATE team_followups SET updated_at=? WHERE opportunity_id=?', ('2026-10-04T08:00:00+08:00', third))
    login(client, 'alice')
    listed = client.get('/api/followups').json()
    assert listed['total'] == 3 and [item['id'] for item in listed['items']] == [first, second, third]
    assert [item['id'] for item in client.get('/api/followups?status=跟进中').json()['items']] == [first, third]
    assert client.get('/api/followups?status=已归档').json()['total'] == 0
    assert client.get('/api/followups?q=personal-first').json()['items'][0]['id'] == first


def test_team_followup_list_has_global_summary_filters_paging_and_strict_public_whitelist(client):
    alice_id = login(client, 'alice')
    ids = [notice(f'list-{n}', title=f'控制公告 {n:02d}') for n in range(12)]
    for n, ident in enumerate(ids):
        claim(client, ident)
        save(client, ident, '已联系' if n < 2 else '已归档' if n == 2 else '跟进中', f'private-note-{n}')
    with db.connect() as c:
        for n, ident in enumerate(ids):
            c.execute('UPDATE team_followups SET updated_at=? WHERE opportunity_id=?', (f'2026-10-13T08:{59-n:02d}:00+08:00', ident))
    login(client, 'admin')
    listed = team_list(client)
    assert listed['summary'] == {'total': 12, 'following': 9, 'contacted': 2, 'closed': 1}
    assert listed['total'] == 12
    filtered = team_list(client, owner_id=alice_id, status='跟进中', page=2, page_size=5)
    assert filtered['summary'] == listed['summary'] and filtered['total'] == 9
    assert [item['id'] for item in filtered['items']] == ids[8:12]
    assert team_list(client, q='Alice')['total'] == 12
    assert team_list(client, q='控制公告 11')['items'][0]['id'] == ids[11]
    assert team_list(client, q='private-note')['total'] == 0
    assert team_list(client, owner_id=9999)['total'] == 0
    assert team_list(client, page=10000)['items'] == []
    assert len(team_list(client, page_size=20)['items']) == 12
    for item in listed['items']:
        assert set(item) == {'id', 'title', 'source_name', 'published_at', 'published_at_precision', 'team_followup', 'project_ai_status', 'project_ai_stale'}
        assert item['project_ai_status'] in ('queued','running','succeeded','stale','failed','waiting_config')
        assert set(item['team_followup']) == {'owner_id', 'owner_name', 'owner_enabled', 'status', 'claimed_at', 'updated_at', 'owner_revision', 'assignment_source'}
    assert all(set(user) == {'id', 'display_name', 'enabled'} for user in listed['users'])
    assert 'private-note-' not in client.get('/api/team-followups').text


@pytest.mark.parametrize('method,path,body', [
    ('POST', '/api/opportunities/9999/claim', None),
    ('DELETE', '/api/opportunities/9999/claim', None),
    ('PUT', '/api/opportunities/9999/assignee', {'user_id': 1}),
])
def test_ownership_actions_reject_missing_notices(client, method, path, body):
    assert client.request(method, path, json=body).status_code == 404


@pytest.mark.parametrize('method,path,body', [
    ('POST', '/api/opportunities/1/claim', None),
    ('DELETE', '/api/opportunities/1/claim', None),
    ('PUT', '/api/opportunities/1/assignee', {'user_id': 1}),
    ('GET', '/api/team-followups', None),
])
def test_ownership_api_requires_login(client, method, path, body):
    client.post('/api/logout')
    assert client.request(method, path, json=body).status_code == 401


def test_regular_users_cannot_read_aggregate_team_followups(client):
    login(client, 'alice')
    assert client.get('/api/team-followups').status_code == 403


@pytest.mark.parametrize('query', [
    'page=0', 'page=10001', 'page_size=7', 'page_size=21', 'owner_id=0',
    'status=not-a-status', 'q=' + 'x' * 151,
])
def test_team_followup_query_validation(client, query):
    assert client.get('/api/team-followups?' + query).status_code == 422


def test_assignment_requires_valid_enabled_target_and_does_not_change_owner_on_rejection(client):
    ident = notice('invalid-target')
    alice_id = login(client, 'alice')
    original = claim(client, ident)['team_followup']
    login(client, 'admin')
    for body, code in [({'user_id': 0}, 422), ({'user_id': 9999}, 404), ({}, 422)]:
        assert client.put(f'/api/opportunities/{ident}/assignee', json=body).status_code == code
        assert client.get(f'/api/opportunities/{ident}').json()['team_followup'] == original
    assert client.patch(f'/api/users/{alice_id}', json={'enabled': False}).status_code == 200
    assert client.put(f'/api/opportunities/{ident}/assignee', json={'user_id': alice_id}).status_code == 409
