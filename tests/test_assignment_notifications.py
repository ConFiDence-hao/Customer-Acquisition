"""Assignment acknowledgements belong to a current owner revision, not a list view."""
import pytest

from app import db
from test_team_followups import client, login, notice, claim, assign, save


def summary(client):
    response = client.get('/api/my-assignments/summary')
    assert response.status_code == 200, response.text
    return response.json()['unviewed_count']


def ack(client, ident, revision):
    return client.patch(f'/api/opportunities/{ident}/assignment-viewed', json={'owner_revision': revision})


def assigned(client, suffix='assigned'):
    ident = notice(suffix)
    alice = login(client, 'alice')
    login(client, 'admin')
    result = assign(client, ident, alice)
    login(client, 'alice')
    return ident, result['team_followup']['owner_revision']


def test_assignment_summary_ignores_list_filters(client):
    assert summary(client) == 0
    ident, revision = assigned(client)
    assert summary(client) == 1
    assert client.get('/api/followups?q=absent&page=2&page_size=5').json()['items'] == []
    detail = client.get(f'/api/opportunities/{ident}').json()
    assert summary(client) == 1  # Merely fetching data is not successful UI display.
    assert detail['assignment_unviewed'] is True
    assert detail['assignment_source'] == 'admin'
    assert ack(client, ident, revision).json()['unviewed_count'] == 0
    assert ack(client, ident, revision).status_code == 200
    detail = client.get(f'/api/opportunities/{ident}').json()
    assert detail['assignment_unviewed'] is False and detail['assignment_source'] == 'admin'


def test_detail_ack_only_clears_one_project(client):
    first, revision = assigned(client, 'first')
    assigned(client, 'second')
    assert summary(client) == 2
    assert ack(client, first, revision).json()['unviewed_count'] == 1


def test_repeat_assignment_is_idempotent(client):
    ident, revision = assigned(client)
    alice = client.get('/api/me').json()['id']
    assert ack(client, ident, revision).status_code == 200
    login(client, 'admin')
    repeated = assign(client, ident, alice)
    assert repeated['team_followup']['owner_revision'] == revision
    login(client, 'alice')
    assert summary(client) == 0
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM notifications WHERE kind='assignment' AND opportunity_id=?", (ident,)).fetchone()[0] == 1


def test_stale_assignment_ack(client):
    ident, first = assigned(client)
    alice = client.get('/api/me').json()['id']
    bobby = login(client, 'bobby')
    login(client, 'admin')
    second = assign(client, ident, bobby)['team_followup']['owner_revision']
    third = assign(client, ident, alice)['team_followup']['owner_revision']
    assert first < second < third
    login(client, 'alice')
    assert ack(client, ident, first).status_code == 409
    assert summary(client) == 1
    assert ack(client, ident, third).status_code == 200
    login(client, 'bobby')
    assert summary(client) == 0


def test_revision_survives_release(client):
    ident, first = assigned(client)
    assert client.delete(f'/api/opportunities/{ident}/claim').status_code == 200
    assert summary(client) == 0
    second = claim(client, ident)['team_followup']['owner_revision']
    assert second > first + 1
    assert claim(client, ident)['team_followup']['owner_revision'] == second
    assert summary(client) == 0
    assert ack(client, ident, first).status_code == 409


@pytest.mark.parametrize('username', ['admin', 'bobby'])
def test_other_accounts_cannot_ack_or_read_assignment_seen_state(client, username):
    ident, revision = assigned(client)
    login(client, username)
    assert ack(client, ident, revision).status_code == 403
    detail = client.get(f'/api/opportunities/{ident}').json()
    assert 'assignment_unviewed' not in detail
    if username == 'admin':
        assert 'assignment_unviewed' not in client.get('/api/team-followups').text


def test_assignment_endpoints_require_login_and_validate_revision(client):
    ident, revision = assigned(client)
    assert ack(client, ident, 0).status_code == 422
    client.post('/api/logout')
    assert client.get('/api/my-assignments/summary').status_code == 401
    assert ack(client, ident, revision).status_code == 401


def test_admin_notices_only_employee_claims_and_releases_not_stages_or_notes(client):
    ident = notice('events')
    login(client, 'alice')
    claim(client, ident)
    assert summary(client) == 0
    save(client, ident, '已联系', 'secret-personal-note-token')
    save(client, ident, '已联系', 'changed-secret-token')
    login(client, 'bobby')
    save(client, ident, '已联系', 'bobby-private-token')
    login(client, 'admin')
    response = client.get('/api/notifications')
    assert response.status_code == 200, response.text
    items = response.json()['items']
    assert [item['kind'] for item in items].count('claimed') == 1
    assert [item['kind'] for item in items].count('stage_changed') == 0
    assert 'secret' not in response.text and 'bobby-private' not in response.text
    login(client, 'bobby')
    assert client.get('/api/notifications').json()['items'] == []


def test_admin_inbox_hides_legacy_ai_and_stage_events_and_excludes_them_from_badge(client):
    ident=notice('old-ai-notifications');login(client,'admin');admin=client.get('/api/me').json()['id']
    with db.connect() as c:
        for kind in ('report_failed','report_ready','stage_changed','transferred'):
            c.execute('INSERT INTO notifications(user_id,kind,opportunity_id,owner_revision,event_key,payload,created_at) VALUES(?,?,?,?,?,?,?)',(admin,kind,ident,1,'legacy:'+kind,db.dumps({'title':'旧通知'}),db.now()))
    data=client.get('/api/notifications').json()
    assert data['items']==[] and data['unread_count']==0
    with db.connect() as c:assert c.execute('SELECT COUNT(*) FROM notifications WHERE user_id=?',(admin,)).fetchone()[0]==4


def test_admin_own_claim_does_not_send_employee_activity_notification(client):
    ident=notice('admin-personal-claim');login(client,'admin');claim(client,ident)
    assert client.get('/api/notifications').json()['items']==[]


def test_transfer_keeps_pushes_untouched_and_notifies_old_owner(client):
    ident, revision = assigned(client)
    bobby = login(client, 'bobby')
    login(client, 'admin')
    assign(client, ident, bobby)
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM pushes').fetchone()[0] == 0
    login(client, 'alice')
    assert summary(client) == 0
    items = client.get('/api/notifications').json()['items']
    transferred = next(item for item in items if item['kind'] == 'transferred')
    assert client.patch(f"/api/notifications/{transferred['id']}/read").status_code == 200
    login(client, 'bobby')
    assert client.patch(f"/api/notifications/{transferred['id']}/read").status_code == 404


def test_employee_release_notifies_admin_once_without_private_notes(client):
    ident,revision=assigned(client)
    save(client,ident,'已联系','release-private-note')
    assert client.delete(f'/api/opportunities/{ident}/claim').status_code==200
    assert client.delete(f'/api/opportunities/{ident}/claim').status_code==404
    assert summary(client)==0
    login(client,'admin')
    response=client.get('/api/notifications');events=[n for n in response.json()['items'] if n['kind']=='released']
    assert len(events)==1 and events[0]['owner_revision']>revision
    assert events[0]['payload']['owner_name']=='Alice 业务' and events[0]['payload']['status']=='待重新指派'
    assert 'release-private-note' not in response.text


def test_other_employee_cannot_release_or_send_release_notification(client):
    ident,_=assigned(client);login(client,'bobby')
    assert client.delete(f'/api/opportunities/{ident}/claim').status_code==403
    with db.connect() as c:assert c.execute("SELECT COUNT(*) FROM notifications WHERE kind='released'").fetchone()[0]==0


def test_obsolete_assignment_can_be_dismissed_without_acknowledging_a_new_revision(client):
    ident,first=assigned(client)
    alice=client.get('/api/me').json()['id']
    old=next(n for n in client.get('/api/notifications').json()['items'] if n['kind']=='assignment')
    assert client.patch(f"/api/notifications/{old['id']}/read").status_code==409
    bobby=login(client,'bobby');login(client,'admin');assign(client,ident,bobby);assign(client,ident,alice)
    login(client,'alice')
    assert client.patch(f"/api/notifications/{old['id']}/read").status_code==200
    assert summary(client)==1
    current=next(n for n in client.get('/api/notifications').json()['items'] if n['kind']=='assignment' and n['owner_revision']>first)
    assert current['read_at'] is None


def test_legacy_migration_is_repeatable_without_inventing_assignment_notifications(client):
    from app import team_followups
    ident = notice('legacy')
    alice = login(client, 'alice')
    with db.connect() as c:
        c.execute('INSERT INTO team_followups(opportunity_id,owner_id,status,claimed_at,updated_at) VALUES(?,?,\'跟进中\',?,?)', (ident, alice, db.now(), db.now()))
    team_followups.initialize()
    team_followups.initialize()
    item = client.get(f'/api/opportunities/{ident}').json()
    assert item['team_followup']['owner_revision'] >= 1
    assert item['assignment_source'] == 'legacy'
    assert summary(client) == 0
