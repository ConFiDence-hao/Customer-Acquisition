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
            'username': 'alice', 'display_name': 'Alice', 'password': 'testing-user-password',
        })
        assert response.status_code == 200, response.text
        yield c


def login(client, username):
    password = 'testing-only-password' if username == 'admin' else 'testing-user-password'
    response = client.post('/api/login', json={'username': username, 'password': password})
    assert response.status_code == 200, response.text


def notice(suffix, title='垃圾焚烧控制系统改造公告', buyer='公开采购公司', published='2026-10-13'):
    item = analyze({
        'title': title, 'url': f'https://example.com/followups/{suffix}',
        'source_id': 'cecep', 'source_name': '公开来源', 'source_kind': '集团采购',
        'published_at': published,
        'content': '投标截止时间：2099-12-10 12:00\n' + '公开正文证据。' * 30,
    }, [], db.DEFAULT_SETTINGS)
    item['buyer'] = buyer
    assert db.upsert(item) == 'inserted'
    with db.connect() as c:
        return c.execute('SELECT id FROM opportunities WHERE url=?', (item['url'],)).fetchone()[0]


def save(client, ident, status='待研判', note='', updated_at=None):
    response = client.patch(f'/api/opportunities/{ident}', json={'status': status, 'note': note})
    assert response.status_code == 200, response.text
    if updated_at:
        user_id = client.get('/api/me').json()['id']
        with db.connect() as c:
            c.execute('UPDATE followups SET updated_at=? WHERE user_id=? AND opportunity_id=?',
                      (updated_at, user_id, ident))
    return response.json()


def test_followups_require_authentication(client):
    client.post('/api/logout')
    assert client.get('/api/followups').status_code == 401


@pytest.mark.parametrize('username', ['admin', 'alice'])
def test_browsing_and_topic_ai_do_not_create_followup_rows(client, monkeypatch, username):
    ident = notice('unsaved')
    login(client, username)
    topic = client.post('/api/subscriptions', json={'name': '控制专题', 'keywords': ['控制']}).json()['id']
    for endpoint in [f'/api/opportunities/{ident}', f'/api/opportunities/{ident}?subscription_id={topic}']:
        item = client.get(endpoint).json()
        assert (item['status'], item['note']) == ('待研判', '')
        assert item['has_followup'] is False and item['followup_updated_at'] is None
    listed = client.get('/api/opportunities').json()['items'][0]
    assert listed['has_followup'] is False and listed['followup_updated_at'] is None
    monkeypatch.setattr(ai, 'explain', lambda item: {'summary': '专题解读'})
    response = client.post(f'/api/opportunities/{ident}/analyze?subscription_id={topic}')
    assert response.status_code == 200, response.text
    assert response.json()['has_followup'] is False
    assert client.get('/api/followups').json() == {'items': [], 'total': 0, 'page': 1, 'page_size': 10}
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM followups').fetchone()[0] == 0


def test_saved_default_state_appears_without_subscription_push_or_public_score_threshold(client):
    ident = notice('saved-low-score', title='普通设备询价公告')
    with db.connect() as c:
        c.execute('UPDATE settings SET value=json_patch(value,?) WHERE id=1', (db.dumps({'min_score': 100}),))
    login(client, 'alice')
    with db.connect() as c:
        topics_before = [tuple(row) for row in c.execute('SELECT * FROM subscriptions ORDER BY id')]
    assert client.get('/api/opportunities?min_score=100').json()['total'] == 0
    assert client.get('/api/followups').json()['total'] == 0
    saved = save(client, ident)
    response = client.get('/api/followups')
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['total'] == 1 and data['page'] == 1 and data['page_size'] == 10
    item = data['items'][0]
    assert item['id'] == ident and (item['status'], item['note']) == ('待研判', '')
    assert item['has_followup'] is True
    assert item['followup_updated_at'] == saved['followup_updated_at']
    assert item['title'] == '普通设备询价公告' and item['source_name'] == '公开来源'
    with db.connect() as c:
        assert [tuple(row) for row in c.execute('SELECT * FROM subscriptions ORDER BY id')] == topics_before
        assert c.execute('SELECT COUNT(*) FROM pushes').fetchone()[0] == 0


@pytest.mark.parametrize('username', ['admin', 'alice'])
def test_saved_followup_metadata_is_current_across_detail_topic_and_list(client, username):
    ident = notice('first')
    same_title = notice('second')
    login(client, username)
    topic = client.post('/api/subscriptions', json={'name': '控制专题', 'keywords': ['控制']}).json()['id']
    old_time = '2026-10-01T09:00:00+08:00'
    save(client, ident, '跟进中', '核实采购范围', old_time)
    expected = {'status': '跟进中', 'note': '核实采购范围', 'has_followup': True, 'followup_updated_at': old_time}
    items = [
        client.get(f'/api/opportunities/{ident}').json(),
        client.get(f'/api/opportunities/{ident}?subscription_id={topic}').json(),
        next(item for item in client.get('/api/opportunities').json()['items'] if item['id'] == ident),
        client.get('/api/followups').json()['items'][0],
    ]
    assert all({key: item[key] for key in expected} == expected for item in items)
    other = client.get(f'/api/opportunities/{same_title}').json()
    assert other['has_followup'] is False and other['note'] == ''
    updated = save(client, ident, '已联系', '已更新跟进计划')
    assert updated['followup_updated_at'] != old_time
    assert updated['has_followup'] is True
    listed = client.get('/api/followups').json()
    assert listed['total'] == 1 and listed['items'][0]['status'] == '已联系'
    assert listed['items'][0]['note'] == '已更新跟进计划'
    save(client, same_title, '已归档', '另一批次公告')
    listed = client.get('/api/followups').json()
    assert listed['total'] == 2
    assert {item['id'] for item in listed['items']} == {ident, same_title}
    assert {item['note'] for item in listed['items']} == {'已更新跟进计划', '另一批次公告'}
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM followups').fetchone()[0] == 2


def test_followups_and_note_search_are_private_even_for_admin(client):
    ident = notice('private')
    save(client, ident, '跟进中', 'admin-private-token')
    login(client, 'alice')
    assert client.get('/api/followups').json()['total'] == 0
    assert client.get(f'/api/opportunities/{ident}').json()['has_followup'] is False
    save(client, ident, '已联系', 'alice-private-token')
    alice = client.get('/api/followups').json()['items'][0]
    assert (alice['status'], alice['note']) == ('已联系', 'alice-private-token')
    assert client.get('/api/followups?q=admin-private-token').json()['total'] == 0
    login(client, 'admin')
    admin = client.get('/api/followups').json()['items'][0]
    assert (admin['status'], admin['note']) == ('跟进中', 'admin-private-token')
    assert client.get('/api/followups?q=alice-private-token').json()['total'] == 0
    assert 'alice-private-token' not in client.get('/api/followups').text


def test_followups_search_title_buyer_and_own_note_and_combine_status(client):
    title_hit = notice('title', title='DCS 控制系统采购公告', buyer='华南设备有限公司')
    buyer_hit = notice('buyer', title='烟气提标采购公告', buyer='ACME 华东能源有限公司')
    note_hit = notice('note', title='工业设备询价公告', buyer='华北电力有限公司')
    archived = notice('archived', title='已结束设备公告')
    login(client, 'alice')
    save(client, title_hit, '跟进中', '一般咨询')
    save(client, buyer_hit, '已联系', '核实资料')
    save(client, note_hit, '已排除', '安排 alpha 现场沟通')
    save(client, archived, '已归档', '历史研究')
    for query, expected in [('dcs', [title_hit]), ('acme', [buyer_hit]), ('ALPHA', [note_hit]), ('不存在', [])]:
        response = client.get('/api/followups', params={'q': query})
        assert response.status_code == 200, response.text
        assert [item['id'] for item in response.json()['items']] == expected
        assert response.json()['total'] == len(expected)
    assert client.get('/api/followups').json()['total'] == 4
    assert client.get('/api/followups?status=已归档').json()['items'][0]['id'] == archived
    assert client.get('/api/followups?q=acme&status=已联系').json()['total'] == 1
    assert client.get('/api/followups?q=alpha&status=跟进中').json()['total'] == 0


def test_followup_sorting_precedes_pagination_and_supports_all_page_sizes(client):
    ids = [notice(str(n), title=f'跟进商机 {n:02d}', published=f'2026-10-{n+1:02d}') for n in range(12)]
    notice('not-saved', title='尚未保存的公开资讯', published='2026-10-13')
    login(client, 'alice')
    for n, ident in enumerate(ids):
        save(client, ident, '跟进中', f'现场核实 {n}', f'2026-10-13T08:{59-n:02d}:00+08:00')
    default = client.get('/api/followups').json()
    assert default['total'] == 12 and default['page_size'] == 10
    assert [item['id'] for item in default['items']] == ids[:10]
    second = client.get('/api/followups?page_size=5&page=2').json()
    assert (second['total'], second['page'], second['page_size']) == (12, 2, 5)
    assert [item['id'] for item in second['items']] == ids[5:10]
    assert [item['id'] for item in client.get('/api/followups?page_size=5&page=3').json()['items']] == ids[10:12]
    published = client.get('/api/followups?sort=published_at&page_size=5&page=2').json()
    assert [item['id'] for item in published['items']] == [ids[6], ids[5], ids[4], ids[3], ids[2]]
    assert len(client.get('/api/followups?page_size=20').json()['items']) == 12
    assert [item['id'] for item in client.get('/api/followups?page_size=10&page=2').json()['items']] == ids[10:12]
    assert client.get('/api/followups?page_size=5&page=4').json()['items'] == []
    last_page = client.get('/api/followups?page=10000')
    assert last_page.status_code == 200 and last_page.json()['items'] == []


@pytest.mark.parametrize('query', [
    'page=0', 'page=10001', 'page=9999999999999999999999999999999999999999999999999',
    'page_size=4', 'page_size=7', 'page_size=21', 'status=未知', 'sort=score',
])
def test_followup_query_rejects_invalid_filters(client, query):
    response = client.get('/api/followups?' + query)
    assert response.status_code == 422, response.text
