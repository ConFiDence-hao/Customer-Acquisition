import pytest
from app import commercial_sources

ENTRY = 'https://www.bidcenter.com.cn/yqzb/index.html'
SOURCE = {'id':'bidcenter','url':ENTRY}
SETTINGS = {'focus_keywords':['烟气'],'exclude_keywords':[]}

def listing(number=1, next_page=True):
    return f'''<div class="main-item"><div class="main-item1"><h3><a href="/yqzb/detailyqzb-{number}.html">垃圾焚烧烟气治理系统改造采购公告</a></h3><div class="item-bott">发布时间：2026-10-14</div></div></div>''' + ('<a href="/yqzb/index-2.html">下一页</a>' if next_page else '')

class Client:
    def __init__(self,pages): self.pages=pages; self.urls=[]
    def get(self,url):
        self.urls.append(url)
        value=self.pages[url]
        if isinstance(value,Exception): raise value
        return value

def test_verified_public_channel_follows_next_page_and_deduplicates():
    client=Client({ENTRY:listing(1),ENTRY.replace('index.html','index-2.html'):listing(2,False)})
    rows=commercial_sources.get_candidates(client,SOURCE,SETTINGS)
    assert [r['url'].rsplit('-',1)[-1] for r in rows]==['1.html','2.html']
    assert rows[0]['published_at']=='2026-10-14'
    assert rows[0]['public_scope']=='公开邀请招标项目概况和报名须知'

def test_second_page_failure_preserves_first_page():
    from app.collectors import SourceUnavailable
    client=Client({ENTRY:listing(1),ENTRY.replace('index.html','index-2.html'):SourceUnavailable('访问限制')})
    rows=commercial_sources.get_candidates(client,SOURCE,SETTINGS)
    assert len(rows)==1
    assert client.listing_pages==1
    assert client.listing_warnings

def test_repeated_page_stops_and_external_next_page_not_followed():
    client=Client({ENTRY:listing(1),ENTRY.replace('index.html','index-2.html'):listing(1)})
    assert len(commercial_sources.get_candidates(client,SOURCE,SETTINGS))==1
    assert len(client.urls)==2

def test_detail_extracts_public_scope_excludes_forms_cases_and_member_contact():
    text='为垃圾焚烧项目采购烟气净化控制系统，包含设备安装调试和技术服务。'*6
    html=f'<div class="sin-left-top"><h1>垃圾焚烧烟气治理系统改造采购公告</h1>发布时间：2026-10-14</div><div class="baoming-form">申请电话400</div><div class="xiangmu-sin"><div class="project-gk">{text}</div><div class="contact">采购业主【仅向已成功推荐会员显示】</div></div><div class="success-case">别的项目中标案例</div>'
    raw={'url':'https://www.bidcenter.com.cn/yqzb/detailyqzb-1.html','title':'原列表标题'}
    body,title,published=commercial_sources.read_detail(Client({raw['url']:html}),raw,SOURCE)
    assert '烟气净化' in body
    assert '400' not in body and '别的项目' not in body and '会员显示' not in body
    assert title=='垃圾焚烧烟气治理系统改造采购公告'
    assert published=='2026-10-14'

def test_verification_and_wrong_origins_are_rejected():
    from app.collectors import SourceUnavailable
    with pytest.raises(SourceUnavailable):
        commercial_sources.get_candidates(Client({ENTRY:'<title>人机验证</title>'}),SOURCE,SETTINGS)
    with pytest.raises(SourceUnavailable):
        commercial_sources.read_detail(Client({}),{'url':'https://evil.example/yqzb/detailyqzb-1.html'},SOURCE)

def test_external_next_page_not_requested_and_page_budget_is_bounded():
    html=listing(1,False)+'<a href="https://evil.example/yqzb/index-2.html">下一页</a>'
    client=Client({ENTRY:html})
    assert len(commercial_sources.get_candidates(client,SOURCE,SETTINGS))==1
    assert client.urls==[ENTRY]
    pages={}
    for index in range(1,12):
        url=ENTRY if index==1 else ENTRY.replace('index.html',f'index-{index}.html')
        pages[url]=listing(index,False)+f'<a href="/yqzb/index-{index+1}.html">下一页</a>'
    client=Client(pages)
    assert len(commercial_sources.get_candidates(client,SOURCE,SETTINGS))==8
    assert len(client.urls)==8

def test_multiple_member_fields_in_one_row_do_not_break_public_body():
    text='项目采购烟气治理设备并提供安装调试服务。'*10
    html=f'<div class="xiangmu-sin">{text}<table><tr><td>采购业主仅向会员显示</td><td>联系人仅向会员显示</td></tr></table></div>'
    raw={'url':'https://www.bidcenter.com.cn/yqzb/detailyqzb-1.html','title':'烟气设备采购'}
    body,_,_=commercial_sources.read_detail(Client({raw['url']:html}),raw,SOURCE)
    assert text in body and '会员显示' not in body

def test_empty_title_match_then_later_failure_is_a_valid_empty_result():
    from app.collectors import SourceUnavailable
    html=listing(1).replace('垃圾焚烧烟气治理系统改造采购公告','文具和纸张采购公告')
    client=Client({ENTRY:html,ENTRY.replace('index.html','index-2.html'):SourceUnavailable('网络超时')})
    assert commercial_sources.get_candidates(client,SOURCE,SETTINGS)==[]

def test_member_contact_mask_split_across_tags_is_removed():
    text='本项目建设垃圾焚烧烟气治理控制系统，提供工程技术服务。'*8
    html=f'<div class="xiangmu-sin"><div>{text}</div><table><tr><td>采购业主</td><td><span>仅向会员</span><span>显示</span></td></tr></table></div>'
    raw={'url':'https://www.bidcenter.com.cn/yqzb/detailyqzb-1.html','title':'烟气治理控制采购'}
    body,_,_=commercial_sources.read_detail(Client({raw['url']:html}),raw,SOURCE)
    assert '采购业主' not in body and '仅向会员' not in body
    assert text in body
