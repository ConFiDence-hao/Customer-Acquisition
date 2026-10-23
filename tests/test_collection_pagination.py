"""Pagination contracts using public pages captured on 2026-10-14; no live I/O."""
import json
from pathlib import Path
from datetime import datetime

import pytest

from app import collectors, industry_sources, procurement_sources, public_resource_sources, clock
from app.db import DEFAULT_SETTINGS, TZ

FIXTURES = Path(__file__).parent/'fixtures'/'pagination'

def page(name): return (FIXTURES/name).read_text(encoding='utf-8')

class Pages:
    def __init__(self, pages): self.pages=pages;self.calls=[]
    def get(self,url):
        self.calls.append(url)
        value=self.pages.get(url, collectors.SourceUnavailable('next page unavailable'))
        if isinstance(value,Exception):raise value
        return value

def source(sid): return next(dict(s) for s in collectors.SOURCES if s['id']==sid)

def test_cecep_offset_page_adds_records_after_first_forty(monkeypatch):
    monkeypatch.setattr(clock,'now',lambda:datetime(2026,10,15,tzinfo=TZ))
    class Api:
        def post(self,url,payload):
            if payload['pn'] in (0,40):return json.loads(page(f"cecep-pn{payload['pn']}.json"))
            return {'content':{'result':{'records':[]}}}
    rows=collectors.cecep_candidates(Api(),DEFAULT_SETTINGS)
    assert 'https://new.ebidding.cecep.cn/cgxx/013002/013002003/20261009/aba15b16-34c3-4298-ba87-a3f826f673c5.html' in {r['url'] for r in rows}
    assert len(rows)>35

def test_cecep_keeps_first_page_if_next_page_is_restricted(monkeypatch):
    monkeypatch.setattr(clock,'now',lambda:datetime(2026,10,15,tzinfo=TZ))
    class Api:
        def post(self,url,payload):
            if payload['pn']==0:return json.loads(page('cecep-pn0.json'))
            raise collectors.SourceUnavailable('站点限制访问（HTTP 403）')
    client=Api()
    rows=collectors.cecep_candidates(client,DEFAULT_SETTINGS)
    assert len(rows)==35 and client.listing_warnings

def test_cseg_follows_actual_next_link_and_keeps_failed_later_page():
    first='https://www.cseg.cn/index.php?a=lists&catid=15'
    second=first+'&page=2'
    client=Pages({first:page('cseg-list.html'),second:page('cseg-page2.html')})
    rows=collectors.html_candidates(client,source('cseg'),DEFAULT_SETTINGS)
    assert 'https://www.cseg.cn/index.php?a=shows&catid=15&id=1346' in {r['url'] for r in rows}
    assert len({r['url'] for r in rows})==len(rows)
    assert client.listing_warnings

def test_real_hbzhan_next_page_widens_topic_column():
    first='https://www.hbzhan.com/news/topic-99.html'
    second='https://www.hbzhan.com/news/topic-99_p2.html'
    rows=industry_sources.get_candidates(Pages({first:page('hbzhan-list.html'),second:page('hbzhan-page2.html')}),source('hbzhan'),DEFAULT_SETTINGS)
    assert 'https://www.hbzhan.com/news/detail/202192.html' in {r['url'] for r in rows}

def test_solidwaste_project_column_and_next_page_are_read():
    first='https://www.solidwaste.com.cn/news/'
    column='https://www.solidwaste.com.cn/news/list?cid=246'
    rows=industry_sources.get_candidates(Pages({first:page('solidwaste-list.html'),column:page('solidwaste-column.html'),column+'&page=2':page('solidwaste-column-page2.html')}),source('solidwaste'),DEFAULT_SETTINGS)
    assert 'https://www.solidwaste.com.cn/news/365057.html' in {r['url'] for r in rows}

def test_bece_public_turnpage_parameter_widens_procurement_column():
    first='https://ecp.capitaleco-pro.com/'
    column='https://ecp.capitaleco-pro.com/sccms/category/list.html?dates=300&categoryId=2&tabName=招标公告&page=1'
    client=Pages({first:page('bece-list.html'),column:page('bece-expanded-page1.html'),column.replace('page=1','page=2'):page('bece-expanded-page2.html')})
    rows=procurement_sources.get_candidates(client,source('bece'),DEFAULT_SETTINGS)
    assert 'https://ecp.capitaleco-pro.com/sdny_bulletin/2026-10-13/158693.html' in {r['url'] for r in rows}

def test_everbright_reads_disclosed_wrapper_iframe_and_second_page():
    first='https://zcpt.cebenvironment.com.cn/'
    wrapper='https://zcpt.cebenvironment.com.cn/cms/category/bulletinNewList3.html?dates=300&categoryId=2&tabName=招标公告&page=1'
    iframe=wrapper.replace('bulletinNewList3.html','iframe.html')
    settings={**DEFAULT_SETTINGS,'focus_keywords':DEFAULT_SETTINGS['focus_keywords']+['涟水']}
    client=Pages({first:page('everbright-list.html'),wrapper:page('everbright-column.html'),iframe:page('everbright-expanded-page1.html'),iframe.replace('page=1','page=2'):page('everbright-expanded-page2.html')})
    rows=collectors.html_candidates(client,source('everbright'),settings)
    assert iframe.replace('page=1','page=2') in client.calls
    assert 'https://zcpt.cebenvironment.com.cn/ebi_bulletin/2026-10-14/561695.html' in {r['url'] for r in rows}

def test_ccgp_disclosed_pager_keeps_local_second_page_after_empty_central_column():
    first='https://www.ccgp.gov.cn/cggg/dfgg/'
    settings={**DEFAULT_SETTINGS,'focus_keywords':['采购']}
    client=Pages({'https://www.ccgp.gov.cn/cggg/zygg/':'<div class="vF_detail_relcontent_lst"><ul></ul></div>',first:page('ccgp-list.html'),first+'index_1.htm':page('ccgp-column.html')})
    rows=public_resource_sources.get_candidates(client,source('ccgp'),settings)
    assert 'https://www.ccgp.gov.cn/cggg/dfgg/zbgg/202610/t20261014_27494122.htm' in {r['url'] for r in rows}

def test_verified_datong_https_column_canonicalizes_relative_notice_links():
    root='http://ggzyjy.dt.gov.cn/'
    column='https://ggzyjy.dt.gov.cn:443/jyxxgc/index.jhtml'
    settings={**DEFAULT_SETTINGS,'focus_keywords':['供热']}
    client=Pages({root:page('datong-list.html'),column:page('datong-expanded-page1.html'),column.replace('index.jhtml','index_2.jhtml'):page('datong-column-page2.html'),column.replace('index.jhtml','index_3.jhtml'):page('datong-column-page3.html')})
    rows=public_resource_sources.get_candidates(client,source('datong'),settings)
    assert column.replace('index.jhtml','index_3.jhtml') in client.calls
    assert 'http://ggzyjy.dt.gov.cn/jyxxgchxr/382769.jhtml' in {r['url'] for r in rows}

def test_page_budget_and_cycle_stop_do_not_follow_offsite_or_navigation():
    root='https://www.cseg.cn/index.php?a=lists&catid=15'
    html='<a href="?a=shows&catid=15&id=10">垃圾焚烧烟气设备采购公告</a><a href="?a=lists&catid=15&page=2">下一页</a><a href="https://evil.example/?page=2">下一页</a>'
    second=html.replace('id=10','id=11').replace('page=2','page=3')
    third=html.replace('id=10','id=12').replace('page=2','page=4')
    fourth=html.replace('id=10','id=13').replace('page=2','page=5')
    client=Pages({root:html,root+'&page=2':second,root+'&page=3':third,root+'&page=4':fourth,root+'&page=5':html})
    rows=collectors.html_candidates(client,source('cseg'),DEFAULT_SETTINGS)
    assert len(rows)==4
    assert len(client.calls)<=5 and not any('evil.example' in u for u in client.calls)

def test_cecep_candidate_cap_allows_more_than_old_180_but_bounds_large_search(monkeypatch):
    monkeypatch.setattr(clock,'now',lambda:datetime(2026,10,15,tzinfo=TZ))
    class Api:
        def post(self,url,payload):
            word=payload['condition'][0]['equal'];offset=payload['pn']
            records=[{'title':'垃圾焚烧烟气设备采购公告','linkurl':f'/cgxx/{word}-{offset+i}.html','webdate':'2026-10-14'} for i in range(40)]
            return {'content':{'result':{'records':records}}}
    assert len(collectors.cecep_candidates(Api(),DEFAULT_SETTINGS))==300

@pytest.mark.parametrize('sid,expected',[('cseg',250),('everbright',230)])
def test_html_cap_reserves_pdf_request_budget(sid,expected):
    s=source(sid)
    links=''.join('<a href="'+(f'/index.php?a=shows&catid=15&id={i}' if sid=='cseg' else f'/ebi_bulletin/2026-10-14/{i}.html')+'">垃圾焚烧烟气设备采购公告</a>' for i in range(270))
    assert len(collectors.html_candidates(Pages({s['url']:links}),s,DEFAULT_SETTINGS))==expected

def test_bece_candidate_cap_preserves_detail_pdf_budget():
    s=source('bece')
    html=''.join(f'<a href="/sdny_bulletin/2026-10-14/{i}.html">垃圾焚烧烟气设备采购公告</a>' for i in range(240))
    assert len(procurement_sources.get_candidates(Pages({s['url']:html}),s,DEFAULT_SETTINGS))==200

def test_partial_listing_failure_is_visible_in_collection_result(tmp_path,monkeypatch):
    from app import db
    monkeypatch.setattr(db,'DATA',tmp_path);monkeypatch.setattr(db,'ROOT',tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD','testing-only-password');db.initialize()
    s=source('cseg')
    with db.connect() as c:c.execute('INSERT INTO sources(id,config) VALUES(?,?)',(s['id'],db.dumps(s)))
    class Partial(Pages):
        def close(self):pass
    url='https://www.cseg.cn/index.php?a=shows&catid=15&id=10'
    html='<a href="'+url+'">垃圾焚烧烟气设备采购公告</a><a href="?a=lists&catid=15&page=2">下一页</a>'
    client=Partial({s['url']:html,url:'<div class="content">'+('烟气设备采购需求。'*30)+'</div>'})
    monkeypatch.setattr(collectors,'PublicClient',lambda:client)
    result=collectors.collect_source(s,[],DEFAULT_SETTINGS)
    assert result['found']==1 and result['inserted']==1
    assert result['listing_pages']==1 and result['listing_warnings']
    with db.connect() as c:assert c.execute('SELECT status FROM sources WHERE id=?',(s['id'],)).fetchone()[0]=='部分可用'

