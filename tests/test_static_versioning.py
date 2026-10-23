"""index.html assets are stamped with content hashes so browsers never mix old and new scripts."""
import re

import pytest
from fastapi.testclient import TestClient

from app import db, main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'testing-only-password')
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-32-characters-long')
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')
    with TestClient(main.app) as c:
        yield c


def test_every_asset_has_a_content_version(client):
    response = client.get('/')
    assert response.headers['cache-control'] == 'no-cache'
    assets = re.findall(r'(?:src|href)="(/static/[^"]+)"', response.text)
    assert len(assets) > 10 and all(re.search(r'\?v=[0-9a-f]{12}$', url) for url in assets)
    app_js = next(url for url in assets if url.startswith('/static/app.js'))
    cached = client.get(app_js)
    assert cached.status_code == 200 and 'immutable' in cached.headers['cache-control']
    assert 'immutable' not in client.get('/static/app.js').headers.get('cache-control', '')


def test_version_follows_file_content(client, tmp_path, monkeypatch):
    static = tmp_path / 'static';static.mkdir()
    (static / 'index.html').write_text('<script src="/static/a.js"></script><link href="/static/b.css?v=old">', encoding='utf-8')
    (static / 'a.js').write_text('one', encoding='utf-8');(static / 'b.css').write_text('x', encoding='utf-8')
    monkeypatch.setattr(main, 'STATIC', static);monkeypatch.setattr(main, 'INDEX_CACHE', {})
    first = main.versioned_index()
    assert 'b.css?v=old' not in first
    (static / 'a.js').write_text('two!', encoding='utf-8')
    assert main.versioned_index() != first
