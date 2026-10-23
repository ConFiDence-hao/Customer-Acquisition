import httpx
import pytest

from app import collectors


def test_same_origin_robots_redirect_is_verified_and_rules_still_apply():
    requests = []
    def respond(request):
        requests.append(str(request.url))
        if request.url.path == '/robots.txt':
            return httpx.Response(302, headers={'location': '/crawler-rules'})
        return httpx.Response(200, text='User-agent: *\nDisallow: /private/\n')
    client = collectors.PublicClient()
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        assert not client.allowed('https://www.cseg.cn/private/detail')
        assert client.allowed('https://www.cseg.cn/public')
        assert len(requests) == 2  # Cached for subsequent URLs on this origin.
    finally:
        client.close()


@pytest.mark.parametrize('target', ['https://127.0.0.1/rules', 'https://new.ebidding.cecep.cn/rules', '/robots.txt'])
def test_robots_redirect_never_follows_cross_origin_or_loop(target):
    requests = []
    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={'location': target})
    client = collectors.PublicClient()
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(collectors.SourceUnavailable):
            client.allowed('https://www.cseg.cn/public')
        assert requests == ['https://www.cseg.cn/robots.txt']
    finally:
        client.close()


def test_detail_redirect_is_still_rejected():
    client = collectors.PublicClient()
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={'location': '/other'})))
    try:
        with pytest.raises(collectors.SourceUnavailable, match='跳转'):
            client._request('https://www.cseg.cn/public')
    finally:
        client.close()


def test_robots_wildcard_disallow_cannot_be_bypassed_by_browser_fallback():
    client=collectors.PublicClient()
    client.client.close()
    client.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,
        text='User-agent: *\nDisallow: /zbyg/*/\nDisallow: /*.do$\n')))
    try:
        assert client.allowed('https://www.bidizhaobiao.com/zbyg/')
        assert not client.allowed('https://www.bidizhaobiao.com/zbyg/region/')
        assert not client.allowed('https://www.bidizhaobiao.com/private.do')
    finally:client.close()
