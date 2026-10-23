"""Account identities, revocable sessions and personal follow-up records."""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from fastapi import HTTPException, Request
from . import db, clock

def password_hash(password):
    salt=secrets.token_hex(16)
    digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
    return salt+':'+digest

def verify(password, encoded):
    try:
        salt,digest=encoded.split(':')
        actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
        return hmac.compare_digest(actual,digest)
    except (ValueError,TypeError):return False

DUMMY=password_hash('timing-only-not-a-real-account')

def initialize():
    with db.connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
          display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('admin','user')), enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS followups (
          user_id INTEGER NOT NULL REFERENCES users(id), opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
          status TEXT NOT NULL DEFAULT '待研判', note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
          PRIMARY KEY(user_id,opportunity_id));
        CREATE TABLE IF NOT EXISTS subscriptions (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          config TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS subscription_owner ON subscriptions(user_id,deleted,enabled);
        CREATE TABLE IF NOT EXISTS subscription_defaults (
          user_id INTEGER NOT NULL REFERENCES users(id), preset_id TEXT NOT NULL,
          subscription_id INTEGER REFERENCES subscriptions(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL, PRIMARY KEY(user_id,preset_id));
        CREATE TABLE IF NOT EXISTS subscription_checks (
          user_id INTEGER PRIMARY KEY REFERENCES users(id),
          requested_revision INTEGER NOT NULL DEFAULT 0,
          checked_revision INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS model_settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS topic_analyses (
          user_id INTEGER NOT NULL, subscription_id INTEGER NOT NULL, opportunity_id INTEGER NOT NULL,
          evidence_hash TEXT NOT NULL, topic_hash TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(user_id,subscription_id,opportunity_id));
        ''')
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0:
            password=os.getenv('RADAR_PASSWORD','')
            if len(password)<12:raise RuntimeError('RADAR_PASSWORD must be set for initial admin')
            uid=c.execute("INSERT INTO users(username,display_name,password_hash,role,created_at,updated_at) VALUES(?,?,?,'admin',?,?)",('admin','超级管理员',password_hash(password),db.now(),db.now())).lastrowid
            c.execute("INSERT INTO followups SELECT ?,id,status,note,? FROM opportunities WHERE status!='待研判' OR note!=''",(uid,db.now()))
            if c.execute('SELECT COUNT(*) FROM opportunities').fetchone()[0]:
                import json
                settings=json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
                topic={'name':'焚烧与烟气优化','industry':'垃圾焚烧发电','offering':'智慧焚烧、燃烧优化与烟气提标解决方案','keywords':settings['focus_keywords'],'exclude_keywords':settings['exclude_keywords'],'provinces':settings['preferred_provinces'],'stages':['招标','询价','采购意向','变更'],'source_ids':[],'enabled':True}
                c.execute('INSERT INTO subscriptions(user_id,config,enabled,created_at,updated_at) VALUES(?,?,1,?,?)',(uid,db.dumps(topic),db.now(),db.now()))
        from .default_topics import ensure_for_user
        for row in c.execute('SELECT id FROM users').fetchall():
            ensure_for_user(c, row['id'])
        # Install one check for untracked legacy accounts, including same-second
        # edits. Existing check records make this migration safe to repeat.
        c.execute('''INSERT OR IGNORE INTO subscription_checks(user_id,requested_revision,checked_revision)
            SELECT DISTINCT s.user_id,1,0 FROM subscriptions AS s JOIN users AS u ON u.id=s.user_id
            WHERE s.deleted=0 AND s.enabled=1 AND u.enabled=1''')

def public(user):
    return {k:user[k] for k in ('id','username','display_name','role','enabled','created_at')}

def create_session(user_id):
    token=secrets.token_urlsafe(40)
    with db.connect() as c:
        c.execute('DELETE FROM sessions WHERE expires_at<=?',(db.now(),))
        c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user_id,(clock.now()+timedelta(days=7)).isoformat(),db.now()))
    return token

def revoke(token):
    with db.connect() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))

def require_user(request:Request):
    digest=hashlib.sha256(request.cookies.get('radar_session','').encode()).hexdigest()
    with db.connect() as c:
        row=c.execute('SELECT u.* FROM users u JOIN sessions s ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1',(digest,db.now())).fetchone()
    if not row:raise HTTPException(401,'请先使用账号和密码登录')
    return public(row)

def require_admin(request:Request):
    user=require_user(request)
    if user['role']!='admin':raise HTTPException(403,'此操作仅限超级管理员')
    return user

def overlay(item,user,followups=None,teams=None,assignments=None):
    if followups is None:
        with db.connect() as c:r=c.execute('SELECT status,note,updated_at FROM followups WHERE user_id=? AND opportunity_id=?',(user['id'],item['id'])).fetchone()
    else:r=followups.get(item['id'])
    if teams is None:
        from . import team_followups
        teams=team_followups.public_rows([item['id']])
    team=teams.get(item['id'])
    owner=bool(team and team['owner_id']==user['id'])
    timestamp=r['updated_at'] if r else None
    if owner:timestamp=max(timestamp or '',team['updated_at'])
    item.update(status=team['status'] if owner else r['status'] if r else '待研判',note=r['note'] if r else '',
                has_followup=r is not None,followup_updated_at=timestamp,team_followup=team,is_team_owner=owner)
    if owner:
        if assignments is None:
            from . import notifications
            with db.connect() as c:assignments=notifications.unviewed_assignments(c,user['id'])
        item.update(assignment_source=team['assignment_source'],assignment_unviewed=item['id'] in assignments)
    return item

def items(user,full=False):
    db.refresh_expired()
    with db.connect() as c:
        rows=db.unpack_many(c,c.execute('SELECT '+db.LIGHT_COLUMNS+' FROM opportunities ORDER BY score DESC,published_at DESC').fetchall(),full)
        follows={r['opportunity_id']:dict(r) for r in c.execute('SELECT * FROM followups WHERE user_id=?',(user['id'],))}
        from . import notifications
        assignments=notifications.unviewed_assignments(c,user['id'])
    from . import team_followups
    teams=team_followups.public_rows()
    return [overlay(r,user,follows,teams,assignments) for r in rows]

def saved_followups(user,q='',status='',page=1,page_size=10,sort='followup_updated_at',query_spec=None):
    """Personal saved records or assigned leads, with effective state before paging."""
    db.refresh_expired()
    clause='(f.user_id IS NOT NULL OR t.owner_id=:user_id)';params={'user_id':user['id'],'q':q,'status':status}
    if q:
        clause+=" AND (instr(lower(o.title),lower(:q))>0 OR instr(lower(COALESCE(json_extract(o.payload,'$.buyer'),'')),lower(:q))>0 OR instr(lower(COALESCE(f.note,'')),lower(:q))>0)"
    effective_status="CASE WHEN t.owner_id=:user_id THEN t.status ELSE COALESCE(f.status,'待研判') END"
    effective_time="CASE WHEN t.owner_id=:user_id THEN MAX(COALESCE(f.updated_at,''),t.updated_at) ELSE f.updated_at END"
    if status:clause+=' AND ('+effective_status+')=:status'
    query=' FROM opportunities o LEFT JOIN followups f ON f.opportunity_id=o.id AND f.user_id=:user_id LEFT JOIN team_followups t ON t.opportunity_id=o.id WHERE '+clause
    published="COALESCE(o.published_at,'') DESC"
    order={'followup_updated_at':f'({effective_time}) DESC,{published},o.id DESC','published_at':f'{published},({effective_time}) DESC,o.id DESC'}[sort]
    with db.connect() as c:
        total=c.execute('SELECT COUNT(*)'+query,params).fetchone()[0]
        extended=bool(query_spec and query_spec.extended())
        rows=c.execute('SELECT o.*,f.user_id AS personal_user_id,f.status AS followup_status,f.note AS followup_note,f.updated_at AS followup_updated_at'+query+' ORDER BY '+order+('' if extended else ' LIMIT :limit OFFSET :offset'),{**params,'limit':page_size,'offset':(page-1)*page_size}).fetchall()
        from . import notifications
        assignments=notifications.unviewed_assignments(c,user['id'])
    follows={r['id']:{'status':r['followup_status'],'note':r['followup_note'],'updated_at':r['followup_updated_at']} for r in rows if r['personal_user_id'] is not None}
    from . import team_followups
    teams=team_followups.public_rows([r['id'] for r in rows])
    items=[overlay(db.unpack(r,extended),user,follows,teams,assignments) for r in rows]
    if extended:
        from . import querying
        items=[item for item in items if querying.matches_notice(item,query_spec,clock.now())]
        total=len(items);items=items[(page-1)*page_size:page*page_size]
        for item in items:item.pop('content',None)
    return {'items':items,'total':total}
