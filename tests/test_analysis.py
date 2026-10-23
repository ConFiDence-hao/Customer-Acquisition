from datetime import datetime
import pytest
from app.analysis import analyze,find_deadline,find_document_deadline,parse_date,stage_of
from app.db import DEFAULT_SETTINGS,TZ

NOW=datetime(2026,10,11,12,tzinfo=TZ)
def raw(**kwargs):
    return {'title':'中节能（合肥）可再生能源有限公司垃圾焚烧AI智慧管控采购招标公告','url':'https://example.org/1','source_id':'cecep','source_name':'慧采云','source_kind':'集团采购','published_at':'2026-10-10','content':'采购组织人：中节能（合肥）可再生能源有限公司\n投标文件递交截止时间：2026-10-30 10:00\n投标保证金金额：20000元',**kwargs}

def test_active_submission_deadline_is_distinct_from_document_purchase():
    d,e=find_deadline('招标文件获取时间：2026-10-10～2026-10-16\n投标文件递交截止时间：2026-10-30 10:00')
    assert d=='2026-10-30T10:00:00+08:00'

def test_missing_deadline_is_never_actionable():
    x=analyze(raw(content='招标文件获取截止时间：2026-10-16 23:59'),[],DEFAULT_SETTINGS,NOW)
    assert not x['actionable'] and not x['deadline']

def test_awards_and_terminated_are_not_current_opportunities():
    for suffix in ['中标候选人公示','中标结果公告','流标公告','采购结果公示','招标失败公告','暂停招标公告','中止公告']:
        x=analyze(raw(title='垃圾焚烧AI智慧管控'+suffix),[],DEFAULT_SETTINGS,NOW)
        assert not x['actionable'] and x['score']<50

def test_expired_is_capped_and_not_actionable():
    x=analyze(raw(content='投标截止时间：2026年10月10日10时00分'),[],DEFAULT_SETTINGS,NOW)
    assert not x['actionable'] and x['score']<50

def test_budget_does_not_use_deposit():
    x=analyze(raw(),[],DEFAULT_SETTINGS,NOW)
    assert x['budget_text']=='' and x['actionable']

def test_exact_entity_matches_despite_parentheses():
    companies=[{'id':1,'name':'中节能(合肥)可再生能源有限公司','province':'安徽','city':'合肥市'}, {'id':2,'name':'中节能（北京）节能环保工程有限公司','province':'北京'}]
    x=analyze(raw(),companies,DEFAULT_SETTINGS,NOW)
    assert x['province']=='安徽' and len(x['matched_companies'])==1

def test_replaced_company_directory_matches_when_object_ids_collide(monkeypatch):
    from app import analysis
    # Simulate address reuse without depending on the allocator or garbage collection.
    monkeypatch.setattr(analysis,'id',lambda value:1,raising=False)
    original=[{'id':1,'name':'原名录环保能源有限公司','province':'安徽'}]
    replacement=[{'id':1,'name':'替换名录环保能源有限公司','province':'山东'}]
    first=analyze(raw(title='原名录环保能源有限公司智慧焚烧招标公告',content=''),original,DEFAULT_SETTINGS,NOW)
    assert [company['name'] for company in first['matched_companies']]==['原名录环保能源有限公司']

    updated=analyze(raw(title='替换名录环保能源有限公司智慧焚烧招标公告',content=''),replacement,DEFAULT_SETTINGS,NOW)
    assert [company['name'] for company in updated['matched_companies']]==['替换名录环保能源有限公司']
    assert updated['province']=='山东'

def test_exclusion_prevents_supplies_becoming_ai_recommendation():
    x=analyze(raw(title='垃圾焚烧厂食堂保洁服务招标公告'),[],DEFAULT_SETTINGS,NOW)
    assert x['score']<50 and not x['actionable']

def test_intent_change_and_results_order():
    assert stage_of('烟气项目将于11月招标')=='采购意向'
    assert stage_of('公开招标中标候选人公示')=='中标候选人'
    assert stage_of('烟气改造招标变更公告')=='变更'

def test_invalid_date_and_title_only_are_safe():
    assert parse_date('2026年13月31日') is None
    assert not analyze(raw(content=''),[],DEFAULT_SETTINGS,NOW)['actionable']
    assert parse_date('2026-10-11T15:30:00+08:00')=='2026-10-11T15:30:00+08:00'

def test_unknown_locality_does_not_use_agent_city():
    x=analyze(raw(title='垃圾焚烧烟气提标招标',content='代理机构地址：北京市西城区\n投标截止时间：2026-11-01 09:00'),[],DEFAULT_SETTINGS,NOW)
    assert x['province']==''

def test_plant_boilerplate_does_not_promote_bus_tender():
    x=analyze(raw(title='中节能垃圾焚烧发电厂通勤车采购招标',content='项目简介：烟气超低排放智慧管控。投标截止时间：2026-11-01 10:00'),[],DEFAULT_SETTINGS,NOW)
    assert x['category']=='其他' and x['score']<=44

def test_ordinary_monitoring_is_lower_than_upgrade():
    x=analyze(raw(title='垃圾焚烧厂烟气在线监测系统运维服务招标'),[],DEFAULT_SETTINGS,NOW)
    assert x['score']<=59

def test_routine_real_world_procurement_does_not_dominate_recommendations():
    for phrase in ['烟气系统清灰清焦服务','烟气及水质在线监测比对服务','DCS卡件配件','烟气CEMS运营技术服务','烟气脱硝尿素']:
        x=analyze(raw(title='垃圾焚烧厂'+phrase+'询比采购公告'),[],DEFAULT_SETTINGS,NOW)
        assert x['score']<=49
    upgraded=analyze(raw(title='垃圾焚烧厂烟气自动监测设备升级改造询比采购公告'),[],DEFAULT_SETTINGS,NOW)
    assert upgraded['score']>=75

def test_explicit_coal_sector_is_not_a_top_waste_incineration_lead():
    x=analyze(raw(title='燃煤锅炉智能燃烧与NOx超低排放研究测试服务询比采购公告'),[],DEFAULT_SETTINGS,NOW)
    assert x['score']<=44 and any('相邻工业' in r for r in x['risks'])

def test_closed_file_window_is_not_new_entry_even_before_bid_deadline():
    text='采购文件获取时间：2026-09-29 00:00:00\n～\n2026-10-10 23:59:59\n采购文件获取方式：平台下载\n响应文件递交截止时间：2026-10-12 09:00'
    x=analyze(raw(content=text),[],DEFAULT_SETTINGS,NOW)
    assert x['document_deadline']=='2026-10-10T23:59:00+08:00'
    assert x['document_state']=='expired' and not x['actionable'] and x['score']<=69
    assert x['temporal_state']=='open' and '手续' in x['summary']

def test_document_start_date_does_not_steal_bid_deadline():
    text='招标文件获取时间：2026-10-01\n响应文件递交截止时间：2026-10-30 10:00'
    assert find_document_deadline(text)[0] is None
    assert find_document_deadline('报名截止时间：2026年10月20日10时00分')[0]=='2026-10-20T10:00:00+08:00'
    assert find_document_deadline('招标文件获取时间：2026-10-01 至 2026-10-20 10:00')[0]=='2026-10-20T10:00:00+08:00'

def test_supplies_and_insulation_repairs_are_not_technical_upgrade():
    for text in ['CEMS烟气监测设备耗材','除尘器保温更换服务','除尘器玻璃钢覆盖封堵服务','除尘器滤袋及袋笼']:
        assert analyze(raw(title='垃圾焚烧厂'+text+'询比采购公告'),[],DEFAULT_SETTINGS,NOW)['score']<=49


@pytest.mark.parametrize('published,precision',[
    ('2026-10-10','day'),
    ('2026-10-10 15:43','minute'),
    ('2026年10月10日00时00分','minute'),
    ('2026-10-10T15:43:00+08:00','minute'),
    ('2026-10-10T00:00:00+08:00','unknown'),
    ('invalid date','unknown'),
    (None,'unknown'),
])
def test_publication_precision_distinguishes_source_dates_from_legacy_midnight(published,precision):
    item=analyze(raw(published_at=published),[],DEFAULT_SETTINGS,NOW)
    assert item['published_at_precision']==precision


@pytest.mark.parametrize('precision',['day','minute','unknown'])
def test_publication_precision_survives_reanalysis_of_normalized_midnight(precision):
    item=analyze(raw(published_at='2026-10-10T00:00:00+08:00',published_at_precision=precision),[],DEFAULT_SETTINGS,NOW)
    assert item['published_at_precision']==precision
    assert analyze(item,[],DEFAULT_SETTINGS,NOW)==item


@pytest.mark.parametrize('content,deadline_precision,document_precision',[
    ('投标截止时间：2026-10-30','day','unknown'),
    ('投标截止时间：2026-10-30 00:00','minute','unknown'),
    ('文件获取时间：2026-10-10 10:00 至 2026-10-20\n投标截止时间：2026-10-30 10:00','minute','day'),
    ('文件获取时间：2026-10-10 至 2026-10-20 00:00\n投标截止时间：2026-10-30','day','minute'),
    ('尚未披露截止时间','unknown','unknown'),
])
def test_deadline_precision_uses_selected_endpoint_not_other_dates(content,deadline_precision,document_precision):
    item=analyze(raw(content=content),[],DEFAULT_SETTINGS,NOW)
    assert item['deadline_precision']==deadline_precision
    assert item['document_deadline_precision']==document_precision
    assert analyze(item,[],DEFAULT_SETTINGS,NOW)==item


def test_date_only_cutoff_keeps_existing_expiration_rule():
    item=analyze(raw(content='投标截止时间：2026-10-11'),[],DEFAULT_SETTINGS,NOW)
    assert item['deadline']=='2026-10-11T00:00:00+08:00'
    assert item['deadline_precision']=='day'
    assert item['temporal_state']=='expired' and not item['actionable']
