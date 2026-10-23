"""Explicit shared lead responsibility; detailed personal notes stay account-scoped."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import accounts, db, notifications

router = APIRouter(prefix='/api')
STATUSES = ('待研判', '跟进中', '已联系', '已排除', '已归档')


def initialize():
    with db.connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS team_followups (
            opportunity_id INTEGER PRIMARY KEY REFERENCES opportunities(id),
            owner_id INTEGER NOT NULL REFERENCES users(id),
            status TEXT NOT NULL CHECK(status IN ('待研判','跟进中','已联系','已排除','已归档')),
            claimed_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS team_followup_owner ON team_followups(owner_id,updated_at DESC);
        CREATE TABLE IF NOT EXISTS team_followup_revisions (
            opportunity_id INTEGER PRIMARY KEY REFERENCES opportunities(id), owner_revision INTEGER NOT NULL);
        ''')
        columns = {row['name'] for row in c.execute('PRAGMA table_info(team_followups)')}
        for name, definition in [('owner_revision', 'INTEGER NOT NULL DEFAULT 0'),
                                 ('assignment_source', "TEXT NOT NULL DEFAULT 'legacy'"),
                                 ('assigned_by_user_id', 'INTEGER REFERENCES users(id)')]:
            if name not in columns:
                c.execute(f'ALTER TABLE team_followups ADD COLUMN {name} {definition}')
        c.execute('''INSERT OR IGNORE INTO team_followup_revisions
            SELECT opportunity_id,MAX(owner_revision,1) FROM team_followups''')
        c.execute('''UPDATE team_followups SET owner_revision=(SELECT owner_revision FROM
            team_followup_revisions r WHERE r.opportunity_id=team_followups.opportunity_id)
            WHERE owner_revision=0''')
    notifications.initialize()


def advance_owner_revision(c, opportunity_id):
    c.execute('''INSERT INTO team_followup_revisions VALUES(?,1) ON CONFLICT(opportunity_id)
        DO UPDATE SET owner_revision=owner_revision+1''', (opportunity_id,))
    return c.execute('SELECT owner_revision FROM team_followup_revisions WHERE opportunity_id=?',
                     (opportunity_id,)).fetchone()[0]


def public(row):
    return {'owner_id': row['owner_id'], 'owner_name': row['owner_name'],
            'owner_enabled': bool(row['owner_enabled']), 'status': row['status'],
            'claimed_at': row['claimed_at'], 'updated_at': row['updated_at'],
            'owner_revision': row['owner_revision'], 'assignment_source': row['assignment_source']}


def public_rows(ids=None):
    if ids is not None and not ids:
        return {}
    clause = '' if ids is None else ' WHERE t.opportunity_id IN (' + ','.join('?' for _ in ids) + ')'
    with db.connect() as c:
        rows = c.execute('''SELECT t.opportunity_id,t.owner_id,t.status,t.claimed_at,t.updated_at,t.owner_revision,t.assignment_source,
            u.display_name AS owner_name,u.enabled AS owner_enabled
            FROM team_followups t JOIN users u ON u.id=t.owner_id''' + clause,
                         [] if ids is None else ids).fetchall()
    return {row['opportunity_id']: public(row) for row in rows}


def require_notice(c, ident):
    if not c.execute('SELECT 1 FROM opportunities WHERE id=?', (ident,)).fetchone():
        raise HTTPException(404, '未找到该公告')


def audit(c, action, ident, user, detail=''):
    return c.execute('INSERT INTO audit(created_at,action,target,detail) VALUES(?,?,?,?)',
              (db.now(), action, str(ident), detail + '；操作人：' + user['username'])).lastrowid


def metadata(ident, user):
    return accounts.overlay({'id': ident}, user)


@router.post('/opportunities/{ident}/claim')
def claim(ident: int, user=Depends(accounts.require_user)):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        require_notice(c, ident)
        existing = c.execute('SELECT owner_id FROM team_followups WHERE opportunity_id=?', (ident,)).fetchone()
        if existing and existing['owner_id'] != user['id']:
            raise HTTPException(409, '该线索已有负责人，请联系管理员转交')
        if not existing:
            if not c.execute('SELECT 1 FROM users WHERE id=? AND enabled=1', (user['id'],)).fetchone():
                raise HTTPException(403, '账号已停用')
            timestamp = db.now()
            revision = advance_owner_revision(c, ident)
            c.execute('''INSERT INTO team_followups
                (opportunity_id,owner_id,status,claimed_at,updated_at,owner_revision,assignment_source)
                VALUES(?,?,?,?,?,?,'claim')''',
                      (ident, user['id'], '跟进中', timestamp, timestamp, revision))
            c.execute('''INSERT INTO followups(user_id,opportunity_id,status,note,updated_at)
                VALUES(?,?,'跟进中','',?) ON CONFLICT(user_id,opportunity_id)
                DO UPDATE SET status='跟进中',updated_at=excluded.updated_at''', (user['id'], ident, timestamp))
            audit(c, '领取线索', ident, user)
            if user['role']=='user':
                notifications.record_event(c, notifications.administrators(c), 'claimed', ident, revision,
                    f'claimed:{ident}:{revision}', {'owner_id': user['id'], 'owner_name': user['display_name'], 'status': '跟进中'})
            from . import project_analysis
            project_analysis.enqueue_if_active(c,ident,user['id'])
    return metadata(ident, user)


@router.delete('/opportunities/{ident}/claim')
def release(ident: int, user=Depends(accounts.require_user)):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        require_notice(c, ident)
        existing = c.execute('SELECT owner_id FROM team_followups WHERE opportunity_id=?', (ident,)).fetchone()
        if not existing:
            raise HTTPException(404, '该线索尚未领取')
        if existing['owner_id'] != user['id'] and user['role'] != 'admin':
            raise HTTPException(403, '仅负责人或管理员可以放弃线索')
        c.execute('DELETE FROM team_followups WHERE opportunity_id=?', (ident,))
        revision=advance_owner_revision(c, ident)
        audit(c, '放弃线索', ident, user, '原负责人：' + str(existing['owner_id']))
        owner=c.execute('SELECT display_name FROM users WHERE id=?',(existing['owner_id'],)).fetchone()
        if user['role']=='user':
            notifications.record_event(c,notifications.administrators(c),'released',ident,revision,f'released:{ident}:{revision}',
                {'owner_id':existing['owner_id'],'owner_name':owner['display_name'] if owner else '',
                 'actor_name':user['display_name'],'status':'待重新指派'})
    return metadata(ident, user)


class Assignee(BaseModel):
    user_id: int = Field(ge=1)


@router.put('/opportunities/{ident}/assignee')
def assign(ident: int, body: Assignee, user=Depends(accounts.require_admin)):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        require_notice(c, ident)
        target = c.execute('SELECT enabled FROM users WHERE id=?', (body.user_id,)).fetchone()
        if not target:
            raise HTTPException(404, '账号不存在')
        if not target['enabled']:
            raise HTTPException(409, '停用账号不能接手线索')
        existing = c.execute('SELECT owner_id,status FROM team_followups WHERE opportunity_id=?', (ident,)).fetchone()
        if not existing or existing['owner_id'] != body.user_id:
            timestamp = db.now()
            stage = existing['status'] if existing else '跟进中'
            revision = advance_owner_revision(c, ident)
            c.execute('''INSERT INTO team_followups
                (opportunity_id,owner_id,status,claimed_at,updated_at,owner_revision,assignment_source,assigned_by_user_id)
                VALUES(?,?,?,?,?,?,'admin',?) ON CONFLICT(opportunity_id)
                DO UPDATE SET owner_id=excluded.owner_id,status=excluded.status,
                claimed_at=excluded.claimed_at,updated_at=excluded.updated_at,
                owner_revision=excluded.owner_revision,assignment_source=excluded.assignment_source,
                assigned_by_user_id=excluded.assigned_by_user_id''',
                      (ident, body.user_id, stage, timestamp, timestamp, revision, user['id']))
            notifications.record_assignment(c, ident, body.user_id, user['id'], revision)
            if existing:
                notifications.record_event(c, [existing['owner_id']], 'transferred', ident, revision,
                    f'transferred:{ident}:{revision}', {'actor_name': user['display_name']})
            audit(c, '转交线索' if existing else '指派线索', ident, user,
                  '负责人：' + str(body.user_id) + ('；原负责人：' + str(existing['owner_id']) if existing else ''))
            from . import project_analysis
            project_analysis.enqueue_if_active(c,ident,user['id'])
    return metadata(ident, user)


def save_personal(ident, user, status, note):
    """Private save and current-owner public stage change share one transaction."""
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        require_notice(c, ident)
        timestamp = db.now()
        team = c.execute('SELECT owner_id,status,owner_revision FROM team_followups WHERE opportunity_id=?', (ident,)).fetchone()
        c.execute('''INSERT INTO followups VALUES(?,?,?,?,?) ON CONFLICT(user_id,opportunity_id)
            DO UPDATE SET status=excluded.status,note=excluded.note,updated_at=excluded.updated_at''',
                  (user['id'], ident, status, note, timestamp))
        c.execute('UPDATE team_followups SET status=?,updated_at=? WHERE opportunity_id=? AND owner_id=?',
                  (status, timestamp, ident, user['id']))
        audit(c, '跟进更新', ident, user, status)
        from . import project_analysis
        project_analysis.enqueue_if_active(c,ident,user['id'])


@router.get('/team-followups')
def listing(q: str = Query('', max_length=150), owner_id: int | None = Query(None, ge=1),
            status: Literal['', '待研判', '跟进中', '已联系', '已排除', '已归档'] = '',
            page: int = Query(1, ge=1, le=10000), page_size: int = Query(10, ge=5, le=20),
            user=Depends(accounts.require_admin)):
    if page_size not in (5, 10, 20):
        raise HTTPException(422, '每页仅支持5、10或20条')
    clause = '1=1'
    params = {'q': q.strip(), 'owner_id': owner_id, 'status': status,
              'limit': page_size, 'offset': (page-1)*page_size}
    if params['q']:
        clause += ''' AND (instr(lower(o.title),lower(:q))>0 OR instr(lower(COALESCE(o.source_name,'')),lower(:q))>0
            OR instr(lower(u.display_name),lower(:q))>0 OR instr(lower(u.username),lower(:q))>0)'''
    if owner_id is not None:
        clause += ' AND t.owner_id=:owner_id'
    if status:
        clause += ' AND t.status=:status'
    joins = ' FROM team_followups t JOIN opportunities o ON o.id=t.opportunity_id JOIN users u ON u.id=t.owner_id'
    with db.connect() as c:
        summary = dict(c.execute('''SELECT COUNT(*) AS total,
            COALESCE(SUM(status='跟进中'),0) AS following,COALESCE(SUM(status='已联系'),0) AS contacted,
            COALESCE(SUM(status IN ('已排除','已归档')),0) AS closed FROM team_followups''').fetchone())
        users = [{'id': row['id'], 'display_name': row['display_name'], 'enabled': bool(row['enabled'])}
                 for row in c.execute('SELECT id,display_name,enabled FROM users ORDER BY id')]
        total = c.execute('SELECT COUNT(*)' + joins + ' WHERE ' + clause, params).fetchone()[0]
        rows = c.execute('''SELECT o.id,o.title,o.source_name,o.published_at,
            COALESCE(json_extract(o.payload,'$.published_at_precision'),'unknown') AS published_at_precision,
            t.owner_id,t.status,t.claimed_at,t.updated_at,t.owner_revision,t.assignment_source,u.display_name AS owner_name,u.enabled AS owner_enabled'''
                         + joins + ' WHERE ' + clause + ' ORDER BY t.updated_at DESC,o.id DESC LIMIT :limit OFFSET :offset', params).fetchall()
    items = [{**{key: row[key] for key in ('id', 'title', 'source_name', 'published_at', 'published_at_precision')},
              'team_followup': public(row)} for row in rows]
    from . import project_analysis
    project_analysis.decorate(items,user)
    return {'summary': summary, 'users': users, 'items': items, 'total': total, 'page': page, 'page_size': page_size}
