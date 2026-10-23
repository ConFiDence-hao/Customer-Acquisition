import json
from app import db
from app.analysis import analyze


def test_list_cache_metadata_does_not_change_evidence_or_create_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(db, 'ROOT', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'testing-only-password')
    db.initialize()
    item = analyze({'url': 'https://example.com/notice', 'title': '垃圾焚烧烟气提标改造采购公告',
                    'source_id': 'cecep', 'source_name': '公开来源', 'source_kind': '集团采购',
                    'published_at': '2026-10-14', 'content': '烟气提标改造公开采购内容。' * 20}, [], db.DEFAULT_SETTINGS)
    assert db.upsert(item) == 'inserted'
    with db.connect() as c:
        original = dict(c.execute('SELECT * FROM opportunities').fetchone())
    assert db.upsert({**item, 'list_title': '垃圾焚烧烟气提标改造…', 'listing_source_id': 'cecep'}) == 'unchanged'
    assert db.upsert({**item, 'list_title': '烟气提标改造…', 'listing_source_id': 'other_channel'}) == 'unchanged'
    with db.connect() as c:
        current = dict(c.execute('SELECT * FROM opportunities').fetchone())
        assert c.execute('SELECT COUNT(*) FROM versions').fetchone()[0] == 0
    assert current['content_hash'] == original['content_hash']
    assert current['created_at'] == original['created_at']
    assert current['updated_at'] == original['updated_at']
    assert json.loads(current['payload'])['listing_source_id'] == 'other_channel'
