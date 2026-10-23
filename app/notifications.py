"""Account-scoped events; assignment acknowledgement is bound to current ownership."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import accounts, db

router = APIRouter(prefix='/api')
SAFE_FIELDS = {'title', 'owner_id', 'owner_name', 'status', 'actor_name', 'job_status'}
ADMIN_KINDS = ('claimed','released')


def initialize():
    with db.connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            kind TEXT NOT NULL, opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
            owner_revision INTEGER NOT NULL, event_key TEXT NOT NULL,
            payload TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT,
            UNIQUE(user_id,event_key));
        CREATE INDEX IF NOT EXISTS notification_inbox ON notifications(user_id,read_at,id DESC);
        ''')


def administrators(c):
    return [row['id'] for row in c.execute("SELECT id FROM users WHERE enabled=1 AND role='admin'")]


def record_event(c, recipients, kind, opportunity_id, owner_revision, event_key, payload):
    # Assignments remain durable for the separate viewed-revision acknowledgement.
    recipients={uid for uid in recipients if (person:=c.execute('SELECT role FROM users WHERE id=?',(uid,)).fetchone())
                and (person['role']!='admin' or kind in (*ADMIN_KINDS,'assignment'))}
    if not recipients:return
    safe = {key: value for key, value in payload.items() if key in SAFE_FIELDS}
    title = c.execute('SELECT title FROM opportunities WHERE id=?', (opportunity_id,)).fetchone()
    if title:
        safe['title'] = title['title']
    c.executemany('''INSERT OR IGNORE INTO notifications
        (user_id,kind,opportunity_id,owner_revision,event_key,payload,created_at)
        VALUES(?,?,?,?,?,?,?)''',
        [(uid, kind, opportunity_id, owner_revision, event_key, db.dumps(safe), db.now())
         for uid in set(recipients)])


def record_assignment(c, opportunity_id, recipient_id, actor_id, revision):
    actor = c.execute('SELECT display_name FROM users WHERE id=?', (actor_id,)).fetchone()
    record_event(c, [recipient_id], 'assignment', opportunity_id, revision,
                 f'assignment:{opportunity_id}:{revision}',
                 {'actor_name': actor['display_name'] if actor else ''})


def unviewed_assignments(c, user_id):
    return {row['opportunity_id'] for row in c.execute('''
        SELECT n.opportunity_id FROM notifications n JOIN team_followups t
        ON t.opportunity_id=n.opportunity_id AND t.owner_revision=n.owner_revision
        WHERE n.user_id=? AND t.owner_id=? AND t.assignment_source='admin'
        AND n.kind='assignment' AND n.read_at IS NULL''', (user_id, user_id))}


def unviewed_assignment_count(c, user_id):
    return len(unviewed_assignments(c, user_id))


def mark_assignment_viewed(c, user_id, opportunity_id, expected_revision):
    row = c.execute('SELECT owner_id,owner_revision FROM team_followups WHERE opportunity_id=?',
                    (opportunity_id,)).fetchone()
    if not row or row['owner_id'] != user_id:
        raise HTTPException(403, '仅当前负责人可确认自己的指派查看状态')
    if row['owner_revision'] != expected_revision:
        raise HTTPException(409, '负责人已变更，请重新打开当前项目')
    return c.execute('''UPDATE notifications SET read_at=? WHERE user_id=? AND opportunity_id=?
        AND owner_revision=? AND kind='assignment' AND read_at IS NULL''',
        (db.now(), user_id, opportunity_id, expected_revision)).rowcount


@router.get('/my-assignments/summary')
def assignment_summary(user=Depends(accounts.require_user)):
    with db.connect() as c:
        return {'unviewed_count': unviewed_assignment_count(c, user['id'])}


class AssignmentViewed(BaseModel):
    owner_revision: int = Field(ge=1)


@router.patch('/opportunities/{ident}/assignment-viewed')
def assignment_viewed(ident: int, body: AssignmentViewed, user=Depends(accounts.require_user)):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        marked = mark_assignment_viewed(c, user['id'], ident, body.owner_revision)
        return {'marked': marked, 'unviewed_count': unviewed_assignment_count(c, user['id'])}


@router.get('/notifications')
def listing(unread_only: bool = False, limit: int = Query(30, ge=1, le=100),
            user=Depends(accounts.require_user)):
    with db.connect() as c:
        policy=" AND kind IN ('claimed','released') AND EXISTS (SELECT 1 FROM users actor WHERE actor.id=json_extract(notifications.payload,'$.owner_id') AND actor.role='user')" if user['role']=='admin' else ''
        rows = c.execute('''SELECT * FROM notifications WHERE user_id=?'''
                         + policy
                         + (' AND read_at IS NULL' if unread_only else '')
                         + ' ORDER BY id DESC LIMIT ?', (user['id'], limit)).fetchall()
        unread = c.execute('SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL'+policy,
                           (user['id'],)).fetchone()[0]
        current={r['opportunity_id']:r['owner_revision'] for r in c.execute('SELECT opportunity_id,owner_revision FROM team_followups WHERE owner_id=?',(user['id'],))}
    return {'items': [{**{key: row[key] for key in ('id', 'kind', 'opportunity_id', 'owner_revision', 'created_at', 'read_at')},
                       'payload': json.loads(row['payload']),'superseded':row['kind']=='assignment' and current.get(row['opportunity_id'])!=row['owner_revision']} for row in rows], 'unread_count': unread}


@router.patch('/notifications/{ident}/read')
def read(ident: int, user=Depends(accounts.require_user)):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM notifications WHERE id=? AND user_id=?', (ident, user['id'])).fetchone()
        if not row:
            raise HTTPException(404, '未找到该通知')
        if row['kind'] == 'assignment':
            # Reading an inbox event must not acknowledge a project that was never displayed.
            current=c.execute('SELECT owner_id,owner_revision FROM team_followups WHERE opportunity_id=?',(row['opportunity_id'],)).fetchone()
            if current and current['owner_id']==user['id'] and current['owner_revision']==row['owner_revision']:
                raise HTTPException(409, '请打开项目详情查看此指派')
        c.execute('UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE id=?', (db.now(), ident))
    return {'status': '已读'}
