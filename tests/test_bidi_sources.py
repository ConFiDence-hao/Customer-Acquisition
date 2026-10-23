"""Visible public Bidi DOM contracts, based on its observed public row markup.

The live anonymous browser currently ends at an IP security page. The normal
examples below are explicit offline contracts, not claims of successful access.
"""
from importlib import import_module

import pytest

from app.collectors import SourceUnavailable

BASE='https://www.bidizhaobiao.com'
ROOTS=[BASE+'/zbyg/',BASE+'/zbgg',BASE+'/cgyx']
SOURCE={'id':'bidi','url':ROOTS[0]}
SETTINGS={'focus_keywords':['烟气'],'exclude_keywords':['食堂']}

def adapter():return import_module('app.bidi_sources')

def listing(number=1,title='垃圾焚烧烟气治理设备采购公告',next_url=None,hidden=False):
    style=' style="display: none"' if hidden else ''
    text=f'''<div class="ul"><div class="li clearfix"><div class="list-top"><div class="li-title fl"><p class="cp-title"><a class="noAfreePublic" href="{BASE}/info-{number}.html"{style}>{title}</a></p></div><div class="li-time fl"><p class="year_date">2026-10-15</p></div></div><div class="list-bottom">招标单位：公开采购单位</div></div></div>'''
    return text+(f'<a class="next" href="{next_url}">下一页</a>' if next_url else '')

class Client:
    def __init__(self,pages):self.pages=pages;self.calls=[]
    def get_rendered(self,url):
        self.calls.append(url)
        value=self.pages.get(url,SourceUnavailable('此补充栏目暂不可用'))
        if isinstance(value,Exception):raise value
        return value
    def get(self,url):raise AssertionError('Bidi must use rendered HTML, not its static script shell')

def defaults(first):
    return {ROOTS[0]:first,ROOTS[1]:listing(2),ROOTS[2]:listing(3)}

def detail(body=None):
    body=body if body is not None else '采购范围为垃圾焚烧厂烟气净化控制系统升级，包含设备供货、安装调试和技术培训。'*6
    return '<header>登录 网站导航</header><h1>垃圾焚烧烟气控制系统升级采购公告</h1><div class="article-meta">发布时间：2026-10-15 09:30</div><article>'+body+'</article><footer>会员推广、400热线及相关文章</footer>'

def test_three_public_root_columns_and_disclosed_allowed_next_page_are_deduplicated():
    pages=defaults(listing(1,next_url=ROOTS[0]+'?page=2'))
    pages[ROOTS[0]+'?page=2']=listing(4)
    pages[ROOTS[1]]=listing(1)+listing(2)
    client=Client(pages)
    rows=adapter().get_candidates(client,SOURCE,SETTINGS)
    assert {r['url'] for r in rows}=={BASE+f'/info-{i}.html' for i in (1,2,3,4)}
    assert rows[0]['published_at']=='2026-10-15' and rows[0]['published_at_precision']=='day'
    assert client.listing_pages==4

def test_title_filter_remains_global_and_personal_recall_is_supported():
    settings={**SETTINGS,'collection_topics':[{'keywords':['专属芯片'],'exclude_keywords':[]}]}
    client=Client(defaults(listing(1,'专属芯片设备采购预告')+listing(4,'垃圾焚烧厂食堂烟气设备采购')))
    rows=adapter().get_candidates(client,SOURCE,settings)
    assert BASE+'/info-1.html' in {r['url'] for r in rows}
    assert BASE+'/info-4.html' not in {r['url'] for r in rows}

def test_actual_ip_security_page_is_not_accepted_as_an_empty_public_list():
    html='<title>安全认证</title><div class="container_right">IP存在访问异常, 请进行安全认证。</div><form class="verify-form"></form>'
    with pytest.raises(SourceUnavailable,match='安全认证|访问异常'):
        adapter().get_candidates(Client({ROOTS[0]:html}),SOURCE,SETTINGS)

def test_hidden_intermediate_template_is_not_accepted_as_public_visible_results():
    with pytest.raises(SourceUnavailable,match='可见|结构'):
        adapter().get_candidates(Client({ROOTS[0]:listing(hidden=True)}),SOURCE,SETTINGS)

def test_later_security_page_retains_candidates_and_stops_domain_requests():
    client=Client({ROOTS[0]:listing(1,next_url=ROOTS[0]+'?page=2'),ROOTS[0]+'?page=2':'<title>安全认证</title>IP存在访问异常，请进行安全认证'})
    assert len(adapter().get_candidates(client,SOURCE,SETTINGS))==1
    assert client.listing_warnings and ROOTS[1] not in client.calls

def test_wildcard_robots_disallowed_nested_list_path_is_not_followed():
    client=Client(defaults(listing(1,next_url=ROOTS[0]+'2/')))
    assert adapter().get_candidates(client,SOURCE,SETTINGS)
    assert ROOTS[0]+'2/' not in client.calls
    assert client.listing_warnings

def test_page_limit_is_three_per_column_and_repeated_notices_stop_early():
    pages={}
    for root_index,root in enumerate(ROOTS):
        for n in range(1,6):
            url=root if n==1 else root+'?page='+str(n)
            pages[url]=listing(root_index*100+n,next_url=root+'?page='+str(n+1))
    client=Client(pages)
    assert len(adapter().get_candidates(client,SOURCE,SETTINGS))==9
    assert len(client.calls)==9 and not any('page=4' in u for u in client.calls)
    repeat=Client(defaults(listing(1,next_url=ROOTS[0]+'?page=2'))|{ROOTS[0]+'?page=2':listing(1,next_url=ROOTS[0]+'?page=3')})
    assert adapter().get_candidates(repeat,SOURCE,SETTINGS)
    assert ROOTS[0]+'?page=3' not in repeat.calls

def test_unrelated_visible_list_is_valid_empty_and_later_failure_is_reported():
    client=Client({ROOTS[0]:listing(1,'普通办公用品项目采购预告'),ROOTS[1]:SourceUnavailable('网络超时'),ROOTS[2]:listing(3,'普通办公用品项目采购意向')})
    assert adapter().get_candidates(client,SOURCE,SETTINGS)==[]
    assert client.listing_warnings

def test_candidate_limit_is_bounded_across_columns():
    client=Client(defaults(''.join(listing(i) for i in range(1,280))))
    assert len(adapter().get_candidates(client,SOURCE,SETTINGS))==250

@pytest.mark.parametrize('url',[BASE+'/client/details_login/123.html',BASE+'/info-1.html?type=free',BASE+'/info-1.html#x',BASE+':444/info-1.html','https://evil.example/info-1.html','http://www.bidizhaobiao.com/info-1.html','https://user:password@www.bidizhaobiao.com/info-1.html'])
def test_nonpublic_or_noncanonical_details_are_rejected_before_browser_read(url):
    client=Client({})
    with pytest.raises(SourceUnavailable):adapter().read_detail(client,{'url':url},SOURCE)
    assert not client.calls

def test_visible_semantic_article_extracts_only_public_evidence_and_metadata():
    url=BASE+'/info-838800663.html'
    body,title,published=adapter().read_detail(Client({url:detail()}),{'url':url},SOURCE)
    assert '烟气净化控制系统升级' in body and '会员推广' not in body and '网站导航' not in body
    assert title=='垃圾焚烧烟气控制系统升级采购公告' and published=='2026-10-15 09:30'

def test_hidden_member_fields_are_never_read():
    url=BASE+'/info-838800663.html'
    html=detail(('公开烟气治理控制系统采购技术要求。'*10)+'<div style="display:none">仅会员可见私密联系人及金额999999</div>')
    body,_,_=adapter().read_detail(Client({url:html}),{'url':url},SOURCE)
    assert '999999' not in body and '私密联系人' not in body

@pytest.mark.parametrize('html',[detail('公开项目简介。登录后查看完整公告'),'<title>会员登录</title><form>请登录后查看</form>',detail('图片正文<img src="/scan.jpg">'),'<h1>公告</h1><nav>'+('导航及相关推荐。'*40)+'</nav>'])
def test_login_gated_short_or_missing_article_is_not_a_verified_body(html):
    url=BASE+'/info-838800663.html'
    with pytest.raises(SourceUnavailable):adapter().read_detail(Client({url:html}),{'url':url},SOURCE)

def test_submitted_dates_inside_body_do_not_become_publication_metadata():
    url=BASE+'/info-838788156.html'
    html=detail().replace('<div class="article-meta">发布时间：2026-10-15 09:30</div>','')
    html=html.replace('</article>','投标截止时间：2026-11-20 10:00</article>')
    assert adapter().read_detail(Client({url:html}),{'url':url},SOURCE)[2] is None
