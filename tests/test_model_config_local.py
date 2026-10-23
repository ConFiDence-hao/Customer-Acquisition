"""Administrators can point local mode at an intranet model server from the page."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, main, model_config


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'testing-only-password')
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-32-characters-long')
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')
    for name in ('LLM_BASE_URL', 'LLM_MODEL', 'LLM_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    main.FAILURES.clear()
    with TestClient(main.app) as c:
        assert c.post('/api/login', json={'username': 'admin', 'password': 'testing-only-password'}).status_code == 200
        yield c


def save(client, **body):
    return client.put('/api/model-config', json={'mode': 'local', 'auto_analyze': False, 'max_concurrency': 1,
                                                 'response_format': 'json_object', **body})


def test_page_local_endpoint_overrides_env_and_keeps_key_private(client, monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'http://10.0.0.9:9000/v1');monkeypatch.setenv('LLM_MODEL', 'env-model')
    assert client.get('/api/model-config').json()['local_source'] == 'env'
    response = save(client, base_url='http://10.0.0.20:8000/v1/', model='qwen3-32b', api_key='intranet-key')
    assert response.status_code == 200 and 'intranet-key' not in response.text
    public = client.get('/api/model-config').json()
    assert (public['local_source'], public['base_url'], public['model'], public['has_key']) == ('page', 'http://10.0.0.20:8000/v1', 'qwen3-32b', True)
    config = model_config.runtime()
    assert (config['base_url'], config['model'], config['api_key'], config['response_format']) == (
        'http://10.0.0.20:8000/v1', 'qwen3-32b', 'intranet-key', 'json_object')
    save(client, base_url='', model='')
    config = model_config.runtime()
    assert (config['base_url'], config['model'], config['response_format']) == ('http://10.0.0.9:9000/v1', 'env-model', 'json_schema')


@pytest.mark.parametrize('url', ['http://localhost:8000/v1', 'http://127.0.0.1:8000/v1', 'http://169.254.169.254/v1',
                                 'ftp://192.168.1.2/v1', 'http://user:pw@192.168.1.2/v1', 'http://192.168.1.2:8000/v1/chat/completions'])
def test_local_endpoint_rejects_container_self_and_metadata(client, url):
    assert save(client, base_url=url, model='m').status_code == 422


def test_local_endpoint_requires_model_and_external_still_rejects_intranet(client):
    assert save(client, base_url='http://192.168.1.2:8000/v1').status_code == 422
    assert client.put('/api/model-config', json={'mode': 'external', 'base_url': 'http://192.168.1.2:8000/v1', 'model': 'm',
                                                 'api_key': 'k', 'response_format': 'json_object'}).status_code == 422


def test_requests_go_to_page_local_endpoint_without_public_dns_check(client, monkeypatch):
    from app import ai
    assert save(client, base_url='http://model-server.lan:8000/v1', model='local-test', api_key='k').status_code == 200
    monkeypatch.setattr(model_config, 'validate_endpoint', lambda *a, **kw: pytest.fail('external validation must not run'))
    recorded = {}
    def handle(request):
        recorded.update(url=str(request.url), json=json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(
            {'summary': '测试', 'fit': '控制', 'questions': [], 'suggested_actions': []})}}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(ai.ai_provider.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    result = ai.explain({'title': '控制系统改造', 'content': '公开公告', 'business_context': {'industry': '工业', 'offering': '控制'}})
    assert recorded['url'] == 'http://model-server.lan:8000/v1/chat/completions'
    assert recorded['json']['model'] == 'local-test' and result['model'] == 'local-test'
