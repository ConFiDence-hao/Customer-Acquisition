import json
import pytest
from fastapi.testclient import TestClient
from app import db, main, monitoring, collectors

HTML='<ul><li><a href="/notice/1">工业智能控制采购公告</a><time>2026-10-12</time></li><li><a href="http://127.0.0.1/admin">跨域非法采购公告</a></li></ul>'
BODY='<article>'+('采购工业智能控制设备。'*30)+'</article>'

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD','testing-only-password')
    monkeypatch.setenv('SESSION_SECRET','test-session-secret-32-characters-long')
    monkeypatch.setenv('SCHEDULER_ENABLED','false')
    main.FAILURES.clear()
    with TestClient(main.app) as c:
        c.post('/api/login',json={'username':'admin','password':'testing-only-password'})
        yield c

def cfg(**kw):
    return dict(name='工业采购监测',url='https://example.com/list',adapter='html',item_selector='li',link_selector='a[href]',date_selector='time',content_selector='article',**kw)

@pytest.mark.parametrize('url',['http://127.0.0.1/','http://169.254.169.254/','https://localhost/','file:///x','https://user:pass@example.com/','https://example.com:999/x','http://[::1]/'])
def test_unsafe_url_rejected(url):
    with pytest.raises(ValueError):monitoring.validate_public_url(url)

def test_mixed_dns_private_rejected(monkeypatch):
    monkeypatch.setattr(monitoring.socket,'getaddrinfo',lambda *a,**k:[(0,0,0,'',('8.8.8.8',443)),(0,0,0,'',('192.168.0.1',443))])
    with pytest.raises(ValueError):monitoring.validate_public_url('https://example.com',resolve=True)

def test_stream_deadline_and_pinned_transport(monkeypatch):
    from contextlib import contextmanager
    clock=[0.0];seen={}
    monkeypatch.setattr(monitoring.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(monitoring.time,'sleep',lambda n:None)
    monkeypatch.setattr(monitoring,'validate_public_url',lambda *a,**k:'8.8.8.8')
    class Response:
        is_redirect=False;status_code=200;encoding='utf-8'
        def raise_for_status(self):pass
        def iter_bytes(self):
            clock[0]=101
            yield b'data'
    @contextmanager
    def stream(method,url,**kwargs):
        seen.update(url=str(url),**kwargs);yield Response()
    client=monitoring.MonitorClient()
    monkeypatch.setattr(client.client,'stream',stream)
    try:
        with pytest.raises(ValueError,match='时长'):client.request('https://example.com/list')
        assert seen['url']=='https://8.8.8.8/list' and seen['headers']['Host']=='example.com'
        assert seen['extensions']['sni_hostname']=='example.com'
    finally:client.close()

def test_extract_html_rss_json():
    c=monitoring.SourceConfig(**cfg()).model_dump()
    rows=monitoring.parse_candidates(HTML,c,c['url'])
    assert len(rows)==1 and rows[0]['published_at']=='2026-10-12'
    assert rows[0]['url']=='https://example.com/notice/1'
    c.update(adapter='rss')
    rows=monitoring.parse_candidates('<rss><channel><item><title>控制系统采购</title><link>https://example.com/2</link><pubDate>Mon, 12 Oct 2026 08:00:00 +0800</pubDate></item></channel></rss>',c,c['url'])
    assert len(rows)==1 and rows[0]['published_at'].startswith('2026-10-12')
    c.update(adapter='json',items_path='data.items',title_field='name',url_field='link')
    assert monitoring.parse_candidates(json.dumps({'data':{'items':[{'name':'智能设备采购公告','link':'/3'}]}}),c,c['url'])[0]['url'].endswith('/3')


@pytest.mark.parametrize('adapter,date,precision',[
    ('html','2026-10-12T00:00:00+08:00','minute'),
    ('html','2026-10-12','day'),
    ('rss','Mon, 12 Oct 2026 00:00:00 +0800','minute'),
    ('rss','2026-10-12','day'),
    ('json','2026-10-12T00:00:00+08:00','minute'),
    ('json','2026-10-12','day'),
])
def test_custom_source_precision_survives_normalization_of_real_midnight(adapter,date,precision):
    from app.analysis import analyze
    config=monitoring.SourceConfig(**cfg()).model_dump()
    config['adapter']=adapter
    if adapter=='html':
        text='<li><a href="/notice/1">工业智能控制采购公告</a><time datetime="'+date+'">发布日期</time></li>'
    elif adapter=='rss':
        text='<rss><channel><item><title>工业智能控制采购公告</title><link>https://example.com/notice/1</link><pubDate>'+date+'</pubDate></item></channel></rss>'
    else:
        text=json.dumps([{'title':'工业智能控制采购公告','url':'/notice/1','published_at':date}])
    row=monitoring.parse_candidates(text,config,config['url'])[0]
    assert row['published_at_precision']==precision
    item=analyze({**row,'source_id':'custom','source_name':'工业采购监测','source_kind':'自定义监测'},[],db.DEFAULT_SETTINGS)
    assert item['published_at']=='2026-10-12T00:00:00+08:00'
    assert item['published_at_precision']==precision
    assert analyze(item,[],db.DEFAULT_SETTINGS)['published_at_precision']==precision

def test_enable_requires_matching_preview_and_survives_restart(client,monkeypatch):
    monkeypatch.setattr(monitoring.MonitorClient,'get',lambda self,url:HTML if url.endswith('/list') else BODY)
    body=cfg(enabled=True)
    assert client.post('/api/source-configs',json=body).status_code==409
    preview=client.post('/api/source-preview',json=body)
    assert preview.status_code==200,preview.text
    assert preview.json()['passed'] and preview.json()['items'][0]['content']
    with db.connect() as c:assert c.execute('select count(*) from opportunities').fetchone()[0]==0
    changed={**body,'include_keywords':['没有的关键词'],'test_token':preview.json()['test_token']}
    assert client.post('/api/source-configs',json=changed).status_code==409
    r=client.post('/api/source-configs',json={**body,'test_token':preview.json()['test_token']})
    assert r.status_code==200,r.text
    sid=r.json()['id'];collectors.register_sources()
    assert any(s['id']==sid and s['enabled'] for s in collectors.source_rows())
    topic={'name':'工业','keywords':['智能控制'],'source_ids':[sid]}
    assert client.post('/api/subscriptions',json=topic).status_code==200
    settings=db.settings();source=next(s for s in collectors.source_rows() if s['id']==sid)
    first=collectors.collect_source(source,[],settings); second=collectors.collect_source(source,[],settings)
    assert first['inserted']==1 and second['inserted']==0
    assert client.put('/api/source-configs/'+sid,json={**body,'enabled':False}).status_code==200
    assert not next(s for s in collectors.source_rows() if s['id']==sid)['enabled']

def test_builtin_toggle_persists_and_reference_cannot_enable(client):
    assert client.patch('/api/source-configs/cecep/state',json={'enabled':False}).status_code==200
    collectors.register_sources()
    assert not next(s for s in collectors.source_rows() if s['id']=='cecep')['enabled']
    assert client.patch('/api/source-configs/jilin/state',json={'enabled':True}).status_code==409

def test_ordinary_user_cannot_configure_or_probe(client):
    client.post('/api/users',json={'username':'alice','display_name':'业务','password':'testing-user-password'})
    client.post('/api/login',json={'username':'alice','password':'testing-user-password'})
    assert client.post('/api/source-preview',json=cfg()).status_code==403
    assert client.post('/api/source-configs',json=cfg()).status_code==403

def test_same_notice_observed_by_two_monitors_matches_both(client,monkeypatch):
    from app import subscriptions
    monkeypatch.setattr(monitoring.MonitorClient,'get',lambda self,url:HTML if url.endswith('/list') else BODY)
    first={**monitoring.SourceConfig(**cfg()).model_dump(),'id':'custom_first','custom':True}
    second={**first,'id':'custom_second','name':'第二个来源'}
    assert collectors.collect_source(first,[],db.DEFAULT_SETTINGS)['inserted']==1
    assert collectors.collect_source(second,[],db.DEFAULT_SETTINGS)['inserted']==0
    with db.connect() as c:row=db.unpack(c.execute('select * from opportunities').fetchone())
    topic={'source_ids':['custom_second'],'provinces':[],'stages':[],'keywords':['智能控制'],'exclude_keywords':[],'name':'工业','industry':'','offering':''}
    assert subscriptions.match(row,topic) is not None
    assert row['source_id']=='custom_first'

def test_scheduler_reads_saved_enabled_sources(client,monkeypatch):
    config={**monitoring.SourceConfig(**cfg()).model_dump(),'id':'custom_scheduled','custom':True,'enabled':True}
    with db.connect() as c:
        c.execute('INSERT INTO sources(id,config) VALUES(?,?)',(config['id'],db.dumps(config)))
        c.execute("UPDATE sources SET config=json_set(config,'$.enabled',json('false')) WHERE id='ccgp'")
        run_id=c.execute("INSERT INTO runs(started_at,status) VALUES(?,'运行中')",(db.now(),)).lastrowid
    seen=[]
    def collect(source,*args):
        seen.append(source['id']);return {'source':source['name'],'found':1,'inserted':0,'updated':0,'errors':[]}
    monkeypatch.setattr(collectors,'collect_source',collect)
    collectors.run_collection(run_id)
    assert 'custom_scheduled' in seen and 'ccgp' not in seen

def test_redirect_rejected_and_robots_denial(monkeypatch):
    from contextlib import contextmanager
    monkeypatch.setattr(monitoring,'validate_public_url',lambda *a,**k:'8.8.8.8')
    class Response:is_redirect=True
    @contextmanager
    def stream(*a,**k):yield Response()
    client=monitoring.MonitorClient();monkeypatch.setattr(client.client,'stream',stream)
    try:
        with pytest.raises(ValueError,match='跳转'):client.request('https://example.com/list')
        calls=[]
        def robots(url):calls.append(url);return 'User-agent: *\nDisallow: /'
        monkeypatch.setattr(client,'request',robots)
        with pytest.raises(ValueError,match='robots'):client.get('https://example.com/list')
        assert calls==['https://example.com/robots.txt']
    finally:client.close()
