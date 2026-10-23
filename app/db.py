"""Local persistent store. Every caller owns a short transaction."""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from . import clock

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv('DATA_DIR', str(ROOT / 'data')))
TZ = timezone(timedelta(hours=8))

def now() -> str:
    return clock.now().isoformat(timespec='seconds')

def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)

KEEPER: dict = {'path': None, 'connection': None}

def _keep_open(path: Path) -> None:
    # Closing the last connection checkpoints and deletes the WAL file; on Windows that file
    # churn cost several milliseconds per request. One idle connection keeps the WAL in place.
    if KEEPER['path'] == path:return
    if KEEPER['connection'] is not None:
        try:KEEPER['connection'].close()
        except sqlite3.Error:pass
    keeper = sqlite3.connect(path, check_same_thread=False)
    # The connection only joins the WAL once it has read the database.
    keeper.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()
    KEEPER.update(path=path, connection=keeper)

@contextmanager
def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    _keep_open(DATA / 'radar.db')
    # Re-authorize each statement: cached statements otherwise bypass a newly expired clock.
    connection = sqlite3.connect(DATA / 'radar.db', timeout=30, cached_statements=0)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    # WAL with NORMAL sync stays consistent after a crash and avoids one fsync per short transaction.
    connection.execute('PRAGMA synchronous=NORMAL')
    def authorize(action, _arg1, _arg2, _database, _trigger):
        writes = (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                  sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_TABLE,
                  sqlite3.SQLITE_DROP_INDEX, sqlite3.SQLITE_ALTER_TABLE)
        return sqlite3.SQLITE_DENY if action in writes and not clock.writable() else sqlite3.SQLITE_OK
    connection.set_authorizer(authorize)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

DEFAULT_SETTINGS = {
    'focus_keywords': ['智慧焚烧','智能燃烧','智慧管控','AI智慧','智能优化','APC','烟气','超低排放','DCS','控制系统'],
    'exclude_keywords': ['食堂','保洁','绿化','办公家具','食材','保安','劳保','尿素采购','活性炭采购'],
    'preferred_provinces': [], 'min_score': 55, 'interval_minutes': 60,
}

def initialize() -> None:
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, group_name TEXT, province TEXT,
            city TEXT, address TEXT, furnaces TEXT, commissioned_at TEXT,
            contact_name TEXT, contact_phone TEXT, source_row INTEGER UNIQUE,
            source_file TEXT NOT NULL DEFAULT '垃圾焚烧厂名录及区域分布.xlsx');
        CREATE INDEX IF NOT EXISTS company_province ON companies(province);
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
            source_id TEXT NOT NULL, source_name TEXT, source_kind TEXT,
            published_at TEXT, deadline TEXT, province TEXT, category TEXT, stage TEXT,
            score INTEGER NOT NULL, actionable INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT '待研判', note TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL, payload TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_seen_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS opportunity_rank ON opportunities(score DESC, published_at DESC);
        CREATE INDEX IF NOT EXISTS opportunity_filter ON opportunities(province,category,stage,status);
        CREATE TABLE IF NOT EXISTS versions (id INTEGER PRIMARY KEY, opportunity_id INTEGER,
            content_hash TEXT, payload TEXT, captured_at TEXT);
        CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, config TEXT NOT NULL,
            status TEXT DEFAULT '待采集', last_success TEXT, last_error TEXT, last_count INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL,
            finished_at TEXT, status TEXT NOT NULL, found INTEGER DEFAULT 0,
            inserted INTEGER DEFAULT 0, updated INTEGER DEFAULT 0, error TEXT, details TEXT DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, created_at TEXT, action TEXT, target TEXT, detail TEXT);
        ''')
        # Every row change advances revision, so readers can reuse parsed payloads safely.
        if 'revision' not in {r[1] for r in c.execute('PRAGMA table_info(opportunities)')}:
            c.execute('ALTER TABLE opportunities ADD COLUMN revision INTEGER NOT NULL DEFAULT 0')
        c.execute('CREATE INDEX IF NOT EXISTS opportunity_revision_idx ON opportunities(revision)')
        c.execute('''CREATE TRIGGER IF NOT EXISTS opportunity_revision AFTER UPDATE ON opportunities
            WHEN NEW.revision=OLD.revision BEGIN UPDATE opportunities SET revision=OLD.revision+1 WHERE id=NEW.id; END''')
        c.execute('INSERT OR IGNORE INTO settings VALUES (1,?)', (dumps(DEFAULT_SETTINGS),))
        c.execute("UPDATE runs SET status='中断',finished_at=?,error='服务重启，未完成采集将在下一轮重试' WHERE status='运行中'", (now(),))
        seed = ROOT / 'seed' / 'companies.json'
        if seed.exists() and c.execute('SELECT COUNT(*) FROM companies').fetchone()[0] == 0:
            rows = json.loads(seed.read_text(encoding='utf-8'))
            fields = ['name','group_name','province','city','address','furnaces','commissioned_at','contact_name','contact_phone','source_row']
            c.executemany(f"INSERT INTO companies ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", [tuple(row.get(k) for k in fields) for row in rows])
    from . import accounts
    accounts.initialize()
    from . import workbench
    workbench.initialize()
    from . import team_followups
    team_followups.initialize()

def settings() -> dict:
    with connect() as c:
        return json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])

def all_companies() -> list[dict]:
    with connect() as c:
        return [dict(x) for x in c.execute('SELECT * FROM companies')]

def upsert(item: dict, expected_hash: str | None = None) -> str:
    timestamp = now()
    # Listing labels describe the fetch channel, not a change to notice evidence.
    digest = hashlib.sha256(json.dumps({k:v for k,v in item.items() if k not in ('ai_analysis','detail_fetched_at','analysis_date','list_title','listing_source_id')},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute('SELECT * FROM opportunities WHERE url=?', (item['url'],)).fetchone()
        if expected_hash is not None and (not old or old['content_hash']!=expected_hash):
            return 'conflict'
        if old and old['content_hash'] == digest:
            item['ai_analysis']=json.loads(old['payload']).get('ai_analysis')
            c.execute('UPDATE opportunities SET last_seen_at=?,payload=? WHERE id=?', (timestamp,dumps(item),old['id']))
            return 'unchanged'
        fields = ['title','source_id','source_name','source_kind','published_at','deadline','province','category','stage','score','actionable']
        values = [item.get(k) for k in fields]
        if old:
            c.execute('INSERT INTO versions(opportunity_id,content_hash,payload,captured_at) VALUES(?,?,?,?)', (old['id'],old['content_hash'],old['payload'],timestamp))
            old_payload = json.loads(old['payload'])
            if all(old_payload.get(k)==item.get(k) for k in ('title','content','stage','deadline','actionable','category','score','temporal_state','document_deadline','document_state','analysis_version')):
                item['ai_analysis'] = old_payload.get('ai_analysis')
            c.execute(f"UPDATE opportunities SET {','.join(k+'=?' for k in fields)},content_hash=?,payload=?,updated_at=?,last_seen_at=? WHERE id=?", values+[digest,dumps(item),timestamp,timestamp,old['id']])
            return 'updated'
        c.execute(f"INSERT INTO opportunities(url,{','.join(fields)},content_hash,payload,created_at,updated_at,last_seen_at) VALUES({','.join('?' for _ in range(len(fields)+6))})", [item['url']]+values+[digest,dumps(item),timestamp,timestamp,timestamp])
        return 'inserted'

# Read-mostly cache of parsed notice payloads, keyed by database path and row id.
PAYLOADS: dict[tuple[str,int],tuple[int,dict]] = {}
LIGHT_COLUMNS = 'id,status,note,created_at,updated_at,last_seen_at,revision'

def _parsed(row) -> dict:
    keys = row.keys()
    revision = row['revision'] if 'revision' in keys else None
    key = (str(DATA), row['id'])
    hit = PAYLOADS.get(key) if revision is not None else None
    if hit and hit[0] == revision:
        value = hit[1]
    else:
        if 'payload' in keys:
            payload = row['payload']
        else:
            with connect() as c:
                found = c.execute('SELECT revision,payload FROM opportunities WHERE id=?', (row['id'],)).fetchone()
            revision, payload = found['revision'], found['payload']
        value = json.loads(payload)
        if revision is not None:PAYLOADS[key] = (revision, value)
    # Callers mutate the result; copy containers one level deep so the cache stays intact.
    return {k: v.copy() if isinstance(v, (dict, list)) else v for k, v in value.items()}

def unpack_many(connection: sqlite3.Connection, rows: list, full: bool = True) -> list[dict]:
    """Unpack light rows (LIGHT_COLUMNS), loading only payloads missing from the cache."""
    path = str(DATA)
    missing = [r['id'] for r in rows if (hit := PAYLOADS.get((path, r['id']))) is None or hit[0] != r['revision']]
    for start in range(0, len(missing), 500):
        chunk = missing[start:start+500]
        for found in connection.execute(f"SELECT id,revision,payload FROM opportunities WHERE id IN ({','.join('?' for _ in chunk)})", chunk):
            PAYLOADS[(path, found['id'])] = (found['revision'], json.loads(found['payload']))
    return [unpack(r, full) for r in rows]

def unpack(row: sqlite3.Row, full: bool = True) -> dict:
    item = _parsed(row)
    if item.get('ai_analysis') and not item.get('content'):
        from .analysis import title_only_summary
        # Apply to earlier saved analyses too, without discarding useful suggestions.
        item['ai_analysis']={**item['ai_analysis'],'summary':title_only_summary(item['title'])}
    if item.get('temporal_state')!='expired' and item.get('deadline') and item['deadline'] <= now():
        from .analysis import analyze
        item = analyze(item, all_companies(), settings())
    item.update({k:row[k] for k in ('id','status','note','created_at','updated_at','last_seen_at')})
    # Eligibility is time-dependent; query and detail use the same current clock.
    if item.get('deadline') and item['deadline'] <= now():
        item['actionable'] = False
        item['risks'] = list(dict.fromkeys(item.get('risks',[])+['投标截止时间已过；仅作历史情报']))
    item['actionable'] = bool(item.get('actionable'))
    if not full:
        item.pop('content',None)
    return item

def refresh_expired() -> None:
    """Refresh materialized scores before filtering, counting, sorting and export."""
    if not clock.writable():return
    from .analysis import analyze,ANALYSIS_VERSION
    current=now();path=str(DATA)
    signature=_library_signature()
    memo=REFRESHED.get(path)
    # Skip the full payload scan while no row changed, the day is the same and no deadline has passed.
    if signature is not None and memo and memo[0]==signature and memo[1]==current[:10] and (memo[2] is None or current<memo[2]):return
    _refresh_expired(analyze,ANALYSIS_VERSION)
    with connect() as c:
        upcoming=c.execute("""SELECT MIN(value) FROM (SELECT deadline AS value FROM opportunities WHERE deadline>?
            UNION ALL SELECT json_extract(payload,'$.document_deadline') FROM opportunities WHERE json_extract(payload,'$.document_deadline')>?)""",(current,current)).fetchone()[0]
    signature=_library_signature()
    if signature is not None:REFRESHED[path]=(signature,current[:10],upcoming)

REFRESHED: dict[str,tuple] = {}

def _library_signature():
    try:
        with connect() as c:
            return tuple(c.execute('SELECT COUNT(*),COALESCE(SUM(revision),0),COALESCE(MAX(id),0) FROM opportunities').fetchone())
    except sqlite3.OperationalError:
        return None

def _refresh_expired(analyze,ANALYSIS_VERSION) -> None:
    with connect() as c:
        rows=c.execute("SELECT payload,content_hash FROM opportunities WHERE (deadline<=? AND COALESCE(json_extract(payload,'$.temporal_state'),'')!='expired') OR (json_extract(payload,'$.document_deadline')<=? AND COALESCE(json_extract(payload,'$.document_state'),'')!='expired') OR COALESCE(json_extract(payload,'$.analysis_date'),'')!=? OR COALESCE(json_extract(payload,'$.analysis_version'),0)!=?",(now(),now(),now()[:10],ANALYSIS_VERSION)).fetchall()
    if not rows:return
    companies=all_companies();preferences=settings()
    for row in rows:
        item=analyze(json.loads(row['payload']),companies,preferences)
        upsert(item,expected_hash=row['content_hash'])
    reconcile_lifecycle()

def reconcile_lifecycle() -> int:
    """Tie only exact procurement codes together; never merge similar titles."""
    with connect() as c:
        rows=c.execute("SELECT * FROM opportunities WHERE json_extract(payload,'$.project_code') IS NOT NULL ORDER BY published_at DESC,id DESC").fetchall()
    events={};changed=0
    for row in rows:
        item=json.loads(row['payload']);code=item.get('project_code')
        if not code:continue
        key=(item['source_id'],code)
        if key not in events and item['stage'] in ('中标候选人','中标结果','终止','变更'):
            events[key]=item
        later=events.get(key)
        if not later or later['url']==item['url'] or item['stage'] not in ('招标','询价','采购意向'):continue
        if not later.get('published_at') or later['published_at'] < (item.get('published_at') or ''):continue
        reason=f"同标段已发现后续{later['stage']}公告，原公告不可直接作为当前投标依据"
        reference={k:later.get(k) for k in ('title','url','stage','published_at')}
        if item.get('superseded_by')==reference and not item.get('actionable') and item['score']<50:continue
        item.update(superseded_by=reference,actionable=False,score=min(item['score'],49),priority='低',ai_analysis=None)
        item['risks']=list(dict.fromkeys(item['risks']+[reason]))
        item['reasons'].append('同标段后续状态覆盖原公告，最高49分')
        item['summary']=item['title']+'。'+reason+'。'
        item['next_actions']=['先打开同标段最新公告核实状态：'+later['url'],'仅在确认后续公告与新截止时间后，决定是否参与']
        item['evidence']['同标段后续公告']=later['title']+' '+later['url']
        changed+=upsert(item,expected_hash=row['content_hash'])=='updated'
    return changed

def audit(action: str, target: str, detail: str = '') -> None:
    with connect() as c:
        c.execute('INSERT INTO audit(created_at,action,target,detail) VALUES(?,?,?,?)', (now(),action,target,detail))
