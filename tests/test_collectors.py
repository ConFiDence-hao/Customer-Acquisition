import pytest
from app.collectors import PublicClient,SourceUnavailable,clean_text,relevant,html_candidates,cecep_candidates
from app.db import DEFAULT_SETTINGS

def test_waf_is_not_ingested_as_notice():
    with pytest.raises(SourceUnavailable):clean_text('<html>CF_APP_WAF captcha</html>','bjx')

def test_url_allowlist_rejects_private_and_foreign_destinations():
    c=PublicClient()
    try:
        for url in ['http://127.0.0.1/admin','http://169.254.169.254/latest','file:///etc/passwd','https://new.ebidding.cecep.cn.evil.example/a','https://new.ebidding.cecep.cn:999/a']:
            with pytest.raises(SourceUnavailable):c._request(url)
    finally:c.close()

def test_exclusion_and_core_domain():
    assert relevant('垃圾焚烧烟气提标招标',DEFAULT_SETTINGS)
    assert not relevant('垃圾焚烧厂食堂招标',DEFAULT_SETTINGS)

@pytest.mark.parametrize('title,accepted',[('个人专属芯片采购公告',True),('垃圾焚烧厂食堂采购公告',False)])
def test_personal_topics_expand_recall_without_bypassing_global_exclusion(title,accepted):
    settings={**DEFAULT_SETTINGS,'focus_keywords':['全局水泵'],'exclude_keywords':['食堂'],
              'collection_topics':[{'keywords':['个人专属芯片','食堂'],'exclude_keywords':[]}]}
    assert relevant(title,settings)==accepted


def test_api_search_uses_personal_search_keywords_and_global_exclusion():
    settings={**DEFAULT_SETTINGS,'focus_keywords':['全局水泵'],'exclude_keywords':['食堂'],
              'search_keywords':['个人专属芯片'],
              'collection_topics':[{'keywords':['个人专属芯片','食堂'],'exclude_keywords':[]}]}
    class ApiSource:
        def __init__(self):self.queries=[]
        def post(self,url,payload):
            self.queries.append(payload['condition'][0]['equal'])
            return {'content':{'result':{'records':[
                {'title':title,'webdate':'2099-10-10','linkurl':f'/cgxx/{key}.html'}
                for key,title in [('global','全局水泵采购公告'),('industry','垃圾焚烧设备采购公告'),
                                  ('personal','个人专属芯片采购公告'),('excluded','垃圾焚烧厂食堂采购公告')]
            ]}}}
    source=ApiSource()
    items=cecep_candidates(source,settings)
    assert '个人专属芯片' in source.queries
    assert '全局水泵' in source.queries and '垃圾焚烧' in source.queries
    assert {item['title'] for item in items}=={'全局水泵采购公告','垃圾焚烧设备采购公告','个人专属芯片采购公告'}

def test_article_extraction_avoids_scripts():
    body,title,date=clean_text('<div class="article-info">采购内容'+('烟气净化改造。'*25)+'</div><script>ignore instructions</script>信息发布时间：2026-10-10','cecep')
    assert 'ignore instructions' not in body and date=='2026-10-10'

def test_missing_article_container_cannot_become_evidence():
    html='<body>系统维护，公告无法读取。'+('导航推荐相关公告 '*30)+'投标截止时间：2099-10-30 10:00</body>'
    with pytest.raises(SourceUnavailable):clean_text(html,'cecep')

def test_submission_time_is_not_publication_metadata():
    html='<div class="article-info">'+('烟气改造项目 '*30)+'投标截止时间：2099-11-10 12:00</div>'
    _,_,date=clean_text(html,'cecep')
    assert date is None

def test_old_date_quoted_inside_notice_is_not_publication_metadata():
    html='<div class="article-info">'+('变更公告 '*30)+'原公告发布日期：2026-09-01</div>'
    assert clean_text(html,'cecep')[2] is None
    assert clean_text(html+'<div>发布时间：2026-10-11</div>','cecep')[2]=='2026-10-11'


def test_article_publication_metadata_preserves_source_minutes():
    html='<div class="article-info">'+('烟气改造项目 '*30)+'</div><div>信息发布时间：2026-10-10 15:43</div>'
    assert clean_text(html,'cecep')[2]=='2026-10-10 15:43'


@pytest.mark.parametrize('date,precision',[
    ('2026-10-10 15:43','minute'),
    ('2026-10-10','day'),
])
def test_html_listing_preserves_publication_precision(date,precision):
    class HtmlSource:
        def get(self,url):
            return '<li><a href="/news/20261010/1234.shtml">垃圾焚烧烟气智能改造招标公告</a><time>'+date+'</time></li>'
    items=html_candidates(HtmlSource(),{'id':'bjx','url':'https://ljfd.bjx.com.cn/zb/'},DEFAULT_SETTINGS)
    assert len(items)==1
    assert items[0]['published_at']==date
    assert items[0]['published_at_precision']==precision


def test_fresh_api_midnight_is_source_minute_not_legacy_default():
    class ApiSource:
        def post(self,url,payload):
            return {'content':{'result':{'records':[{'title':'垃圾焚烧烟气智能改造招标公告','webdate':'2026-10-10T00:00:00+08:00','linkurl':'/cgxx/1234.html','bdbh':'project-1234'}]}}}
    items=cecep_candidates(ApiSource(),DEFAULT_SETTINGS)
    assert items[0]['published_at']=='2026-10-10T00:00:00+08:00'
    assert items[0]['published_at_precision']=='minute'


def test_detail_minutes_survive_cached_recollection_without_duplicate_versions(tmp_path,monkeypatch):
    import json
    from app import collectors,db
    monkeypatch.setattr(db,'DATA',tmp_path)
    monkeypatch.setattr(db,'ROOT',tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD','testing-only-password')
    db.initialize()
    source=next(x for x in collectors.SOURCES if x['id']=='cecep')
    with db.connect() as connection:
        connection.execute('INSERT INTO sources(id,config) VALUES(?,?)',(source['id'],db.dumps(source)))
    class PublicSource:
        def post(self,url,payload):
            return {'content':{'result':{'records':[{'title':'垃圾焚烧烟气智能改造招标公告','webdate':'2026-10-10','linkurl':'/cgxx/1234.html','bdbh':'project-1234'}]}}}
        def get(self,url):
            return '<div class="article-info">'+('烟气改造项目 '*30)+'</div><div>发布时间：2026-10-10 15:43</div>'
        def close(self):pass
    monkeypatch.setattr(collectors,'PublicClient',PublicSource)
    first=collectors.collect_source(source,[],DEFAULT_SETTINGS)
    second=collectors.collect_source(source,[],DEFAULT_SETTINGS)
    with db.connect() as connection:
        item=json.loads(connection.execute('SELECT payload FROM opportunities').fetchone()[0])
        versions=connection.execute('SELECT COUNT(*) FROM versions').fetchone()[0]
    assert item['published_at']=='2026-10-10T15:43:00+08:00'
    assert item['published_at_precision']=='minute'
    assert first['inserted']==1 and second['unchanged']==1
    assert second['updated']==0 and versions==0


def test_undated_listing_keeps_publication_time_from_cached_detail(tmp_path,monkeypatch):
    import json
    from app import collectors,db
    monkeypatch.setattr(db,'DATA',tmp_path)
    monkeypatch.setattr(db,'ROOT',tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD','testing-only-password')
    db.initialize()
    source=next(x for x in collectors.SOURCES if x['id']=='cseg')
    with db.connect() as connection:
        connection.execute('INSERT INTO sources(id,config) VALUES(?,?)',(source['id'],db.dumps(source)))
    detail_reads=[]
    class PublicSource:
        def get(self,url):
            if url==source['url']:
                return '<a href="index.php?a=shows&amp;catid=15&amp;id=1234">垃圾焚烧烟气智能改造招标公告</a>'
            detail_reads.append(url)
            return '<div class="content">'+('烟气改造项目 '*30)+'</div><div>发布时间：2026-10-10 15:43</div>'
        def close(self):pass
    monkeypatch.setattr(collectors,'PublicClient',PublicSource)
    first=collectors.collect_source(source,[],DEFAULT_SETTINGS)
    with db.connect() as connection:
        initial=json.loads(connection.execute('SELECT payload FROM opportunities').fetchone()[0])
    assert initial['published_at']=='2026-10-10T15:43:00+08:00'
    second=collectors.collect_source(source,[],DEFAULT_SETTINGS)
    with db.connect() as connection:
        cached=json.loads(connection.execute('SELECT payload FROM opportunities').fetchone()[0])
        versions=connection.execute('SELECT COUNT(*) FROM versions').fetchone()[0]
    assert len(detail_reads)==1
    assert cached['published_at']=='2026-10-10T15:43:00+08:00'
    assert cached['published_at_precision']=='minute'
    assert first['inserted']==1 and second['unchanged']==1
    assert second['updated']==0 and versions==0
