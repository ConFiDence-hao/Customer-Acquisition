"""Personal topics widen bounded public recall without changing source authority."""
import json

import pytest

from app import accounts, collectors, db, monitoring


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(db, 'ROOT', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'testing-only-password')
    db.initialize()
    with db.connect() as connection:
        # These tests control a small keyword set to verify source scope and batches.
        # The default-topic integration case explicitly reenables the installed presets.
        connection.execute('UPDATE subscriptions SET enabled=0 WHERE id IN (SELECT subscription_id FROM subscription_defaults)')
        uid = connection.execute(
            'INSERT INTO users(username,display_name,password_hash,role,created_at,updated_at) VALUES(?,?,?,?,?,?)',
            ('alice', 'Alice', accounts.DUMMY, 'user', db.now(), db.now()),
        ).lastrowid
    return uid


def test_default_topics_and_personal_keyword_are_recalled_across_actual_rounds(library, monkeypatch):
    add_topic(library, ['专属芯片'], source_ids=['cecep'])
    settings = {**db.DEFAULT_SETTINGS, 'focus_keywords': ['全局水泵']}
    source = next(x for x in collectors.SOURCES if x['id'] == 'cecep')
    with db.connect() as connection:
        connection.execute('UPDATE subscriptions SET enabled=1 WHERE id IN (SELECT subscription_id FROM subscription_defaults)')
        connection.execute('UPDATE settings SET value=? WHERE id=1', (db.dumps(settings),))
        connection.execute('INSERT INTO sources(id,config) VALUES(?,?)', ('cecep', db.dumps(source)))
    class MixedSource(SearchSource):
        def post(self, url, payload):
            result = super().post(url, payload)
            if payload['condition'][0]['equal'] == 'SCR脱硝':
                result['content']['result']['records'] = [dict(title='垃圾焚烧烟气SCR脱硝系统改造采购公告', webdate='2099-10-10', linkurl='/cgxx/default-scr.html')]
            return result
        def get(self, url):
            if url.endswith('/default-scr.html'):
                return '<div class="article-title">垃圾焚烧烟气SCR脱硝系统改造采购公告</div><div class="article-info">'+('SCR脱硝系统改造采购技术要求。'*25)+'</div>'
            return super().get(url)
    monkeypatch.setattr(collectors.time, 'time', lambda: 0)
    seen = set()
    for _ in range(3):
        public = MixedSource()
        monkeypatch.setattr(collectors, 'PublicClient', lambda public=public: public)
        with db.connect() as connection:
            run_id = connection.execute('INSERT INTO runs(started_at,status) VALUES(?,?)', (db.now(), '运行中')).lastrowid
        collectors.run_collection(run_id)
        assert len(public.queries) <= 18
        assert len(public.queries) == len({word.casefold() for word in public.queries})
        seen.update(public.queries)
    assert {
        '智慧焚烧', 'AI智能燃烧', '智能燃烧控制', '智能优化控制', 'APC', '锅炉优化', '炉膛火焰图像',
        '烟气超低排放', '烟气提标改造', '烟气净化', 'SCR脱硝', 'NOx提标', 'DCS组态', '智慧管控', '飞灰处置',
        '专属芯片', '全局水泵', '垃圾焚烧',
    }.issubset(seen)
    with db.connect() as connection:
        assert {row[0] for row in connection.execute('SELECT title FROM opportunities')} == {
            '专属芯片设备采购招标公告', '垃圾焚烧烟气SCR脱硝系统改造采购公告',
        }
        assert connection.execute('SELECT COUNT(*) FROM pushes').fetchone()[0] == 0


def add_topic(uid, keywords, *, source_ids=None, excludes=None, enabled=True, deleted=False):
    topic = dict(name='订阅召回验证', industry='工业', offering='设备采购', keywords=keywords,
                 exclude_keywords=excludes or [], provinces=[], stages=[], source_ids=source_ids or [], enabled=enabled)
    with db.connect() as connection:
        return connection.execute(
            'INSERT INTO subscriptions(user_id,config,enabled,deleted,created_at,updated_at) VALUES(?,?,?,?,?,?)',
            (uid, db.dumps(topic), int(enabled), int(deleted), db.now(), db.now()),
        ).lastrowid


class SearchSource:
    def __init__(self):
        self.queries = []

    def post(self, url, payload):
        word = payload['condition'][0]['equal']
        self.queries.append(word)
        rows = []
        if word == '专属芯片':
            rows = [dict(title='专属芯片设备采购招标公告', webdate='2099-10-10', linkurl='/cgxx/recall.html')] * 2
        return {'content': {'result': {'records': rows}}}

    def get(self, url):
        return '<div class="article-title">专属芯片设备采购招标公告</div><div class="article-info">'+('专属芯片设备采购技术要求。'*25)+'</div>'

    def close(self):
        pass


@pytest.mark.parametrize('owner', ['user', 'admin'])
def test_active_personal_subscription_collects_new_topic_into_shared_library(library, monkeypatch, owner):
    uid = library if owner == 'user' else 1
    add_topic(uid, ['专属芯片'], source_ids=['cecep'], excludes=['全局水泵'])
    settings = {**db.DEFAULT_SETTINGS, 'focus_keywords': ['全局水泵']}
    source = next(x for x in collectors.SOURCES if x['id'] == 'cecep')
    with db.connect() as connection:
        connection.execute('UPDATE settings SET value=? WHERE id=1', (db.dumps(settings),))
        connection.execute('INSERT INTO sources(id,config) VALUES(?,?)', ('cecep', db.dumps(source)))
        run_id = connection.execute('INSERT INTO runs(started_at,status) VALUES(?,?)', (db.now(), '运行中')).lastrowid
    public = SearchSource()
    monkeypatch.setattr(collectors, 'PublicClient', lambda: public)
    collectors.run_collection(run_id)
    assert '专属芯片' in public.queries
    assert '全局水泵' in public.queries and '垃圾焚烧' in public.queries
    assert len(public.queries) == len(set(public.queries))
    with db.connect() as connection:
        rows = connection.execute('SELECT title,payload FROM opportunities').fetchall()
        assert [row['title'] for row in rows] == ['专属芯片设备采购招标公告']
        assert connection.execute('SELECT COUNT(*) FROM pushes').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM followups').fetchone()[0] == 0
    item = json.loads(rows[0]['payload'])
    assert not any('命中关注词：专属芯片' in reason for reason in item['reasons'])
    assert 'collection_topics' not in item and 'search_keywords' not in item
    assert db.settings()['focus_keywords'] == ['全局水泵']


def test_collection_recall_is_source_scoped_and_only_uses_active_accounts_topics(library):
    add_topic(library, ['专属芯片'], source_ids=['cecep'])
    add_topic(library, ['全来源传感器'])
    add_topic(library, ['停用主题'], enabled=False)
    add_topic(library, ['已删除主题'], deleted=True)
    with db.connect() as connection:
        disabled = connection.execute(
            'INSERT INTO users(username,display_name,password_hash,role,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
            ('disabled', 'Disabled', accounts.DUMMY, 'user', 0, db.now(), db.now()),
        ).lastrowid
    add_topic(disabled, ['停用账号主题'])
    settings = {**db.DEFAULT_SETTINGS, 'focus_keywords': ['全局水泵']}
    for source_id, included in [('cecep', True), ('bjx', False)]:
        prefs = collectors.collection_preferences(settings, source_id)
        public = SearchSource()
        collectors.cecep_candidates(public, prefs)
        assert ('专属芯片' in public.queries) is included
        assert '全来源传感器' in public.queries
        assert not set(public.queries).intersection({'停用主题', '已删除主题', '停用账号主题'})
        assert collectors.relevant('专属芯片设备采购招标公告', prefs) is included
        assert collectors.relevant('全局水泵设备采购招标公告', prefs)
        assert prefs['focus_keywords'] == ['全局水泵']


@pytest.mark.parametrize('change', ['topic_disabled', 'topic_deleted', 'user_disabled'])
def test_next_collection_stops_using_deactivated_subscription(library, change):
    sid = add_topic(library, ['专属芯片'])
    assert collectors.relevant('专属芯片设备采购招标公告', collectors.collection_preferences(db.DEFAULT_SETTINGS, 'cecep'))
    with db.connect() as connection:
        if change == 'user_disabled':
            connection.execute('UPDATE users SET enabled=0 WHERE id=?', (library,))
        else:
            column = 'enabled' if change == 'topic_disabled' else 'deleted'
            value = 0 if change == 'topic_disabled' else 1
            connection.execute(f'UPDATE subscriptions SET {column}=? WHERE id=?', (value, sid))
    prefs = collectors.collection_preferences(db.DEFAULT_SETTINGS, 'cecep')
    public = SearchSource()
    assert collectors.cecep_candidates(public, prefs) == []
    assert '专属芯片' not in public.queries


def test_personal_exclusion_is_local_to_its_topic_and_global_exclusion_still_wins(library):
    add_topic(library, ['专属芯片'], excludes=['旧设备'])
    settings = {**db.DEFAULT_SETTINGS, 'focus_keywords': ['全局水泵'], 'exclude_keywords': ['食堂']}
    first = collectors.collection_preferences(settings, 'cecep')
    assert not collectors.relevant('专属芯片旧设备采购招标公告', first)
    add_topic(1, ['专属芯片'], excludes=[])
    combined = collectors.collection_preferences(settings, 'cecep')
    assert collectors.relevant('专属芯片旧设备采购招标公告', combined)
    assert not collectors.relevant('专属芯片食堂设备采购招标公告', combined)
    assert collectors.relevant('全局水泵旧设备采购招标公告', combined)


def test_builtin_html_listing_recall_uses_personal_topic_and_deduplicates_urls(library):
    add_topic(library, ['专属芯片'], source_ids=['bjx'])
    class ListSource:
        def get(self, url):
            return '<li><a href="/news/20991010/1234.shtml">专属芯片设备采购招标公告</a><time>2099-10-10</time></li>'
    prefs = collectors.collection_preferences(db.DEFAULT_SETTINGS, 'bjx')
    rows = collectors.html_candidates(ListSource(), {'id': 'bjx', 'url': 'https://ljfd.bjx.com.cn/zb/'}, prefs)
    assert [row['title'] for row in rows] == ['专属芯片设备采购招标公告']
    assert len(rows) == 1


def test_bounded_search_rotates_all_topic_words_without_case_duplicate_queries(library, monkeypatch):
    words = [f'专属芯片{i:02d}' for i in range(50)]
    add_topic(library, words[:25]+['APC', 'apc'])
    add_topic(library, words[25:]+['APC'])
    prefs = collectors.collection_preferences({**db.DEFAULT_SETTINGS, 'focus_keywords': ['全局水泵', 'APC']}, 'cecep')
    seen = set()
    monkeypatch.setattr(collectors.time, 'time', lambda: 0)
    for cycle in range(5):
        public = SearchSource()
        collectors.cecep_candidates(public, {**prefs, 'collection_cycle': cycle})
        assert len(public.queries) <= 18
        assert len(public.queries) == len({word.casefold() for word in public.queries})
        seen.update(public.queries)
    assert set(words).issubset(seen)
    assert '全局水泵' in seen and '垃圾焚烧' in seen and 'APC' in seen


@pytest.mark.parametrize('hours', [[0, 2, 4], [0, 0, 0]], ids=['two-hour-schedule', 'same-hour-manual-runs'])
def test_actual_collection_rounds_rotate_without_wall_clock_aliasing(library, monkeypatch, hours):
    words = [f'专属芯片{i:02d}' for i in range(28)]
    add_topic(library, words)
    settings = {**db.DEFAULT_SETTINGS, 'focus_keywords': ['全局水泵']}
    source = next(x for x in collectors.SOURCES if x['id'] == 'cecep')
    with db.connect() as connection:
        connection.execute('UPDATE settings SET value=? WHERE id=1', (db.dumps(settings),))
        connection.execute('INSERT INTO sources(id,config) VALUES(?,?)', ('cecep', db.dumps(source)))
    batches = []
    for hour in hours:
        monkeypatch.setattr(collectors.time, 'time', lambda hour=hour: hour*3600)
        public = SearchSource()
        monkeypatch.setattr(collectors, 'PublicClient', lambda public=public: public)
        with db.connect() as connection:
            run_id = connection.execute('INSERT INTO runs(started_at,status) VALUES(?,?)', (db.now(), '运行中')).lastrowid
        collectors.run_collection(run_id)
        batches.append(set(public.queries))
        assert len(public.queries) == 18
    expected = set(words) | {'全局水泵', '垃圾焚烧', '烟气', '环保能源', '智慧', '智能燃烧', 'APC', 'DCS'}
    assert len(expected) == 36
    assert batches[0] != batches[1]
    assert set.union(*batches) == expected


def test_api_candidate_cap_remains_bounded_after_subscription_expansion(library):
    add_topic(library, ['专属芯片'])
    class ManyNotices:
        def post(self, url, payload):
            return {'content': {'result': {'records': [
                dict(title=f'专属芯片设备采购招标公告 {i}', webdate='2099-10-10', linkurl=f'/cgxx/{i}.html')
                for i in range(360)
            ]}}}
    rows = collectors.cecep_candidates(ManyNotices(), collectors.collection_preferences(db.DEFAULT_SETTINGS, 'cecep'))
    assert len(rows) == 300
    assert len({row['url'] for row in rows}) == 300


def test_personal_topic_cannot_enable_an_admin_disabled_source(library, monkeypatch):
    add_topic(library, ['专属芯片'])
    source = {**next(x for x in collectors.SOURCES if x['id'] == 'cecep'), 'enabled': False}
    with db.connect() as connection:
        connection.execute('INSERT INTO sources(id,config) VALUES(?,?)', ('cecep', db.dumps(source)))
        run_id = connection.execute('INSERT INTO runs(started_at,status) VALUES(?,?)', (db.now(), '运行中')).lastrowid
    public = SearchSource()
    monkeypatch.setattr(collectors, 'PublicClient', lambda: public)
    collectors.run_collection(run_id)
    assert public.queries == []
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM opportunities').fetchone()[0] == 0


def test_custom_source_rules_are_not_bypassed_by_personal_topic(library, monkeypatch):
    add_topic(library, ['专属芯片'])
    source = dict(id='custom-test', name='受管理员规则约束的网站', kind='公开采购', enabled=True, custom=True,
                  url='https://example.com/list', adapter='html', item_selector='li', link_selector='a[href]',
                  title_selector='', date_selector='time', content_selector='article', include_keywords=['全局水泵'],
                  exclude_keywords=['食堂'], max_items=20)
    with db.connect() as connection:
        connection.execute('INSERT INTO sources(id,config) VALUES(?,?)', (source['id'], db.dumps(source)))
    class CustomSource:
        def get(self, url):
            if url == source['url']:
                return '<li><a href="/a">专属芯片设备采购公告</a></li><li><a href="/b">全局水泵设备采购公告</a></li><li><a href="/c">全局水泵食堂设备采购公告</a></li>'
            return '<article>'+('采购设备技术要求。'*30)+'</article>'
        def close(self):
            pass
    monkeypatch.setattr(monitoring, 'MonitorClient', CustomSource)
    result = collectors.collect_source(source, [], collectors.collection_preferences(db.DEFAULT_SETTINGS, source['id']))
    assert result['found'] == 1 and result['inserted'] == 1
    with db.connect() as connection:
        assert [row[0] for row in connection.execute('SELECT title FROM opportunities')] == ['全局水泵设备采购公告']
