import pytest

from app import browser_fetch


def test_url_scope_is_fixed_to_supported_public_pages():
    assert browser_fetch.permitted_page('https://www.bidizhaobiao.com/zbyg/')
    assert browser_fetch.permitted_page('https://www.bidizhaobiao.com/info-123.html')
    assert browser_fetch.permitted_page('https://huanbao.bjx.com.cn/news/20261014/1516189.shtml')
    for url in ('http://127.0.0.1/', 'https://www.bidizhaobiao.com.evil.org/zbyg/',
                'https://user:password@www.bidizhaobiao.com/zbyg/',
                'https://www.bidizhaobiao.com:8443/zbyg/',
                'https://www.bidizhaobiao.com/login', 'https://huanbao.bjx.com.cn/member/'):
        assert not browser_fetch.permitted_page(url)


def test_visible_verification_stops_collection_even_when_article_exists():
    with pytest.raises(browser_fetch.BrowserReadError, match='访问验证'):
        browser_fetch.validate_snapshot({'visible_text': '请按住滑块，拖动到最右边', 'challenge': True,
                                         'login': False, 'html': '<h1>公开公告</h1><article>正文</article>'})


def test_visible_login_overlay_has_a_separate_reason():
    with pytest.raises(browser_fetch.BrowserReadError, match='登录'):
        browser_fetch.validate_snapshot({'visible_text': '请登录后阅读', 'challenge': False,
                                         'login': True, 'html': '<article>摘要</article>'})


def test_ip_security_challenge_reports_the_specific_reason():
    with pytest.raises(browser_fetch.BrowserReadError,match='IP安全认证'):
        browser_fetch.validate_snapshot({'visible_text':'IP存在访问异常，请进行安全认证',
            'challenge':True,'login':False,'html':'<title>安全认证</title>'})


def test_browser_network_budget_stops_before_another_page_launch():
    reader=browser_fetch.BrowserReader()
    reader.network_requests=500
    with pytest.raises(browser_fetch.BrowserReadError,match='浏览器请求数量上限'):
        reader.get('https://www.bidizhaobiao.com/zbyg/',lambda _:True)
    reader.close()


def test_hidden_captcha_templates_do_not_reject_public_article():
    html='<article>公开正文</article><div hidden>aliyunCaptcha 人机验证 登录</div>'
    assert browser_fetch.validate_snapshot({'visible_text':'公开正文', 'challenge':False, 'login':False, 'html':html}) == html


def test_browser_page_budget_is_bounded_before_launch():
    reader=browser_fetch.BrowserReader(max_pages=0)
    with pytest.raises(browser_fetch.BrowserReadError, match='渲染页数上限'):
        reader.get('https://www.bidizhaobiao.com/zbyg/', lambda _:True)
    reader.close()


def test_forbidden_page_never_launches_browser():
    reader=browser_fetch.BrowserReader()
    with pytest.raises(browser_fetch.BrowserReadError, match='范围'):
        reader.get('https://127.0.0.1/', lambda _:True)
    reader.close()


def test_robots_denied_never_launches_browser():
    reader=browser_fetch.BrowserReader()
    with pytest.raises(browser_fetch.BrowserReadError, match='robots'):
        reader.get('https://www.bidizhaobiao.com/zbyg/', lambda _:False)
    reader.close()


def test_data_requests_obey_robots_and_unknown_subdomains_are_blocked():
    root='huanbao.bjx.com.cn'
    assert not browser_fetch.resource_allowed('https://huanbao.bjx.com.cn/private/api',root,'xhr',lambda _:False)
    assert not browser_fetch.resource_allowed('https://unverified-subdomain.bjx.com.cn/data',root,'xhr',lambda _:True)
    assert browser_fetch.resource_allowed('https://huanbao.bjx.com.cn/public/api',root,'xhr',lambda _:True)


def test_assets_do_not_include_local_or_unverified_hosts():
    for url in ('http://127.0.0.1/icon','https://huanbao.bjx.com.cn:8443/x','https://unknown.bjx.com.cn/a.js'):
        assert not browser_fetch.resource_allowed(url,'huanbao.bjx.com.cn','script',lambda _:True)


def test_bjx_rendering_does_not_download_article_images():
    assert not browser_fetch.resource_allowed('https://img01.mybjx.net/news/UploadFile/202610/test.png',
        'huanbao.bjx.com.cn','image',lambda _:True)
