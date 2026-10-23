"""Evidence-first analysis. No model output is allowed to set factual fields."""
from __future__ import annotations
from . import clock as business_clock
import re
import unicodedata
from functools import lru_cache
from datetime import datetime, timedelta
from .db import TZ

PROVINCES = '北京 天津 河北 山西 内蒙古 辽宁 吉林 黑龙江 上海 江苏 浙江 安徽 福建 江西 山东 河南 湖北 湖南 广东 广西 海南 重庆 四川 贵州 云南 西藏 陕西 甘肃 青海 宁夏 新疆'.split()
ANALYSIS_VERSION=7
DATE = r'(20\d{2})\s*[-年/.]\s*(\d{1,2})\s*[-月/.]\s*(\d{1,2})\s*日?(?:[T\s]*(\d{1,2})\s*[:：时]\s*(\d{1,2}))?'
DOMAIN = re.compile(r'垃圾|焚烧|环保能源|再生能源|烟气|脱硝|炉排|固废|超低排放')
CATEGORIES = {
    '智慧焚烧': ['智慧焚烧','智能燃烧','智慧管控','AI智慧','人工智能','智能优化','APC','燃烧优化','炉膛火焰'],
    '烟气提标': ['烟气','超低排放','脱硝','SCR','脱酸','NOx','除尘器','脱硫'],
    '控制系统': ['DCS','控制系统','自控','自动化','在线监测','仪控','智能化','CEMS'],
    '设备技改': ['技改','改造','升级','优化','锅炉','设备更新'],
}

def normalized(value: str) -> str:
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', value or '')).lower()

def title_only_summary(title: str) -> str:
    return '公开列表标题：'+title+'。当前仅有标题证据；金额的性质与真实性、采购主体、具体资格及最新期限均须查阅原公告核实，不能据此认定预算或已具备投标条件。'

def parse_date(text: str, end_of_day: bool = False) -> str | None:
    m = re.search(DATE, text)
    if not m:
        return None
    y,mo,d,h,mi = m.groups()
    try:
        dt = datetime(int(y),int(mo),int(d),int(h or (23 if end_of_day else 0)),int(mi or (59 if end_of_day else 0)),tzinfo=TZ)
        return dt.isoformat(timespec='seconds')
    except ValueError:
        return None

def date_precision(text: str | None) -> str:
    """Precision in source text, before date-only values receive default midnight."""
    if not isinstance(text,str) or not parse_date(text):return 'unknown'
    match=re.search(DATE,text)
    return 'minute' if match.group(4) is not None and match.group(5) is not None else 'day'

def _retained_precision(raw: dict, field: str, value: str | None, inferred: str) -> str:
    if not value:return 'unknown'
    previous=raw.get(field+'_precision')
    if previous in ('minute','day','unknown') and parse_date(raw.get(field) or '')==value:return previous
    return inferred

def find_deadline(content: str) -> tuple[str | None, str]:
    return _submission_deadline(content)[:2]

def _submission_deadline(content: str) -> tuple[str | None, str, str]:
    # Match actual submission deadline, never file purchase / publicity end dates.
    prefix = r'(?:投标(?:文件)?(?:递交)?|响应(?:文件)?(?:递交)?|报价|提交(?:投标|响应)文件)[\s（(]*截止(?:时间|日期)?[）)]?'
    for m in re.finditer(prefix, content):
        fragment = content[m.start():m.end()+95]
        d = re.search(DATE, content[m.end():m.end()+95])
        if d:
            gap = content[m.end():m.end()+d.start()]
            if re.search('保证金|文件获取|公示期|招标文件获取',gap):
                continue
            parsed = parse_date(d.group(0))
            # Missing a time cannot establish validity on the last calendar day.
            if parsed:
                return parsed, fragment[:180],date_precision(d.group(0))
    return None, '', 'unknown'

def stage_of(title: str) -> str:
    rules = [
        ('终止',r'终止|流标|废标|异常公告|取消|招标失败|采购失败|暂停|中止'),
        ('中标候选人',r'候选人|预中标|评标结果|预成交'),
        ('中标结果',r'中标|成交|采购结果|结果公示'),
        ('变更',r'变更|更正|澄清|延期'),
        ('采购意向',r'采购意向|招标计划|将招标|将于.{0,12}招标|预公示'),
        ('询价',r'询价|询比|比选|竞价|直接采购|谈判'),
        ('招标',r'招标|采购公告|磋商'),
    ]
    return next((name for name,pattern in rules if re.search(pattern,title)), '资讯')

def find_document_deadline(content: str) -> tuple[str | None, str]:
    return _document_deadline(content)[:2]

def _document_deadline(content: str) -> tuple[str | None, str, str]:
    """Only explicit file/registration endpoints, never a lone start date."""
    label=r'(?:(?:采购|招标|询价|比选)?文件(?:的)?(?:获取|领取|发售)|报名)(?:截止(?:时间|日期)|时间|期限)'
    for m in re.finditer(label,content):
        tail=re.split(r'响应文件|投标文件|保证金|开标时间|获取方式|领取方式',content[m.end():m.end()+180])[0]
        dates=list(re.finditer(DATE,tail))
        endpoint=None
        if len(dates)>=2 and re.fullmatch(r'[\s秒分:：0-9]*(?:至|到|～|~|—|－|–|-)[\s]*',tail[dates[0].end():dates[1].start()]):
            endpoint=dates[1]
        elif dates and ('截止' in m.group() or re.search(r'(?:至|到)\s*$',tail[:dates[0].start()])):
            endpoint=dates[0]
        if endpoint:
            parsed=parse_date(endpoint.group())
            if parsed:return parsed,(m.group()+tail[:endpoint.end()])[:220],date_precision(endpoint.group())
    return None,'','unknown'

@lru_cache(maxsize=65536)
def company_key(name: str) -> str:
    return normalized(name)

def match_companies(title: str, body: str, companies: list[dict]) -> list[dict]:
    # Exact legal entity match with punctuation normalization; no generic group-only guessing.
    text = normalized(title+' '+body[:24000])
    index = _company_index(companies)
    # One pass over 7-character windows finds candidates; full-name containment confirms them.
    found = {position for gram in {text[i:i+7] for i in range(len(text)-6)} for position in index.get(gram, ())}
    result=[]
    for position in sorted(found):
        company = companies[position]
        if company_key(company['name']) in text:
            result.append({k:company.get(k) for k in ('id','name','province','city','group_name','source_row')})
    return result[:8]

_INDEX = {'companies': None, 'value': {}}

def _company_index(companies: list[dict]) -> dict:
    global _INDEX
    cached = _INDEX
    if cached['companies'] is companies:return cached['value']
    value = {}
    for position, company in enumerate(companies):
        name = company_key(company['name'])
        if len(name) >= 7:value.setdefault(name[:7], []).append(position)
    # Retain the input so object-address reuse cannot make a replacement directory hit.
    # Publish one cache snapshot; concurrent readers return their own local index.
    _INDEX = {'companies': companies, 'value': value}
    return value

def analyze(raw: dict, companies: list[dict], preferences: dict, clock: datetime | None = None) -> dict:
    current = clock or business_clock.now()
    title = raw['title'].strip()
    content = raw.get('content','').strip()
    searchable = title+' '+content[:24000]
    # Title is weighted first; an unrelated appendix cannot turn supplies into AI procurement.
    title_hits = {cat:[k for k in words if k.lower() in title.lower()] for cat,words in CATEGORIES.items()}
    category = next((cat for cat,hits in title_hits.items() if hits), '其他')
    # Do not infer buying intent from boilerplate descriptions of the plant.
    # Bus, cleaning and routine inspection tenders often describe furnaces/flue gas.
    stage = stage_of(title)
    deadline,deadline_evidence,deadline_precision = _submission_deadline(content)
    document_deadline,document_evidence,document_deadline_precision=_document_deadline(content)
    deadline_precision=_retained_precision(raw,'deadline',deadline,deadline_precision)
    document_deadline_precision=_retained_precision(raw,'document_deadline',document_deadline,document_deadline_precision)
    document_closed=bool(document_deadline and datetime.fromisoformat(document_deadline)<=current)
    published_at = raw.get('published_at')
    published_at_precision=date_precision(published_at)
    # Previous releases normalized date-only publications to this ISO midnight.
    # Fresh collectors provide an explicit precision for genuine source midnight.
    if isinstance(published_at,str) and re.fullmatch(r'20\d{2}-\d{2}-\d{2}T00:00(?::00)?(?:[+-]\d{2}:\d{2}|Z)?',published_at.strip()):
        published_at_precision='unknown'
    if published_at:
        published_at = parse_date(published_at)
    published_at_precision=_retained_precision(raw,'published_at',published_at,published_at_precision)
    matches = match_companies(title,content,companies)
    buyer_match = re.search(r'(?:采购组织人(?:（招标人）)?|招标人|采购人)\s*[：:]\s*([^\n]{5,95}?(?:有限公司|有限责任公司|股份公司|管理局|人民政府))',content)
    buyer = buyer_match.group(1).strip() if buyer_match else (matches[0]['name'] if matches else '')
    if not buyer:
        m = re.search(r'([\u4e00-\u9fff（）、A-Za-z0-9]{5,60}?有限公司)',title)
        buyer=m.group(1) if m else ''
    # Prefer title locality and exact buyer match, never agent office address.
    province = next((p for p in PROVINCES if p in title), '')
    if not province and matches:
        buyer_matches=[x for x in matches if normalized(x['name'])==normalized(buyer)]
        if buyer_matches:
            province=buyer_matches[0].get('province','')
        elif len({x.get('province') for x in matches})==1:
            province=matches[0].get('province','')
    if not province:
        location = re.search(r'(?:项目地点|建设地点|项目地址)\s*[：:]([^\n]{0,100})',content)
        province=next((p for p in PROVINCES if location and p in location.group(1)), '')
    city = next((x.get('city','') for x in matches if x.get('province')==province),'')
    budget_match=re.search(r'(?:预算(?:金额)?|最高(?:投标)?限价|招标控制价|合同估算价|总投资)\s*[：:为约]?\s*([\d,.]+\s*(?:亿元|万元|元))',content)
    budget_text=budget_match.group(0) if budget_match else ''
    reasons=[];risks=[];score=0
    if raw.get('publication_warning'):risks.append(raw['publication_warning'])
    if raw.get('extraction_warning'):risks.append(raw['extraction_warning'])
    category_score={'智慧焚烧':45,'烟气提标':38,'控制系统':33,'设备技改':22,'其他':8}
    score+=category_score[category]
    reasons.append(f'业务方向：{category}（+{category_score[category]}）')
    focus=[k for k in preferences.get('focus_keywords',[]) if k.lower() in title.lower()]
    if focus:
        score+=10;reasons.append('命中关注词：'+ '、'.join(focus[:5])+'（+10）')
    if raw.get('source_kind')=='集团采购':
        score+=12;reasons.append('采购方公开来源（+12）')
    else:
        score+=5;reasons.append('行业信息线索（+5）');risks.append('转载或聚合来源，需打开采购方公告核实')
    if published_at:
        age=(current-datetime.fromisoformat(published_at)).days
        if age < 0:
            risks.append('发布日期晚于当前时间，需核对');score-=15
        elif age<=14:
            score+=15;reasons.append('两周内发布（+15）')
        elif age<=45:
            score+=7;reasons.append('45天内发布（+7）')
        else:
            score-=15;risks.append('发布时间较早，优先用于历史项目研究')
    else:
        risks.append('发布日期缺失，需人工核实')
    if matches:
        score+=8;reasons.append('与历史企业名录精确匹配（+8）')
    if province in preferences.get('preferred_provinces',[]):
        score+=10;reasons.append('位于优先区域（+10）')
    if stage in ['招标','询价']:
        score+=10;reasons.append('采购进行阶段（+10，仍须核对截止时间）')
    elif stage=='采购意向':
        score+=5;reasons.append('前期布局线索（+5）')
    elif stage in ['中标候选人','中标结果','终止']:
        score-=35;risks.append('已进入结果或终止阶段，不能作为当前投标机会')
    else:
        risks.append('非独立招标公告，需关联原项目核实')
    expired=bool(deadline and datetime.fromisoformat(deadline)<=current)
    if expired:
        score-=30;risks.append('投标截止时间已过；仅作历史情报')
    if not deadline:
        risks.append('未取得明确投标截止时间，不能确认可投标')
    excluded=[k for k in preferences.get('exclude_keywords',[]) if k.lower() in title.lower()]
    if excluded:
        score-=60;risks.append('命中排除词：'+'、'.join(excluded))
    if not content:
        risks.append('仅获得列表标题，正文受限或未获取；金额、资格等需核实')
    if not buyer:
        risks.append('采购主体尚未核实')
    if category=='其他':
        risks.append('与核心解决方案关联较弱')
    if not DOMAIN.search(searchable) and not matches:
        score=min(score,20);risks.append('未确认焚烧行业场景')
    detail_unverified='暂不可读' in raw.get('detail_status','')
    if detail_unverified:
        risks.append('本轮无法复核正文，保存的是历史内容；最新采购状态需人工确认')
    actionable=bool(deadline and not expired and stage in ['招标','询价'] and content and not excluded and not detail_unverified and not document_closed)
    if document_closed and not expired and stage in ('招标','询价'):
        score=min(score,69);reasons.append('文件获取或报名已截止，新进入条件待核实，最高69分')
        risks.append('文件获取或报名已截止；仅已完成相关手续的供应商可能继续投标，不能假定可新报名')
    if stage in ['中标候选人','中标结果','终止'] or expired:
        score=min(score,49)
    if category=='其他':
        score=min(score,44);reasons.append('标题未显示核心技术采购需求，最高44分')
    elif category=='设备技改':
        score=min(score,69);reasons.append('通用设备技改，需确认AI切入点，最高69分')
    if re.search('检修|运维|维护|检测|备件|配件|卡件|耗材|清灰|清焦|比对|保温更换|覆盖封堵|滤袋|袋笼|运营技术服务|药剂|活性炭|尿素',title) and not re.search('智慧|智能|优化|升级|提标|改造|超低',title):
        score=min(score,49);reasons.append('常规维护、耗材或检测采购，最高49分')
        risks.append('常规采购可作客户线索，但未显示智慧焚烧或提标改造需求')
    if re.search('燃煤|煤电|焦化|钢铁|水泥',title) and not re.search('垃圾|固废|环保能源',title):
        score=min(score,44);reasons.append('标题显示非垃圾焚烧行业项目，最高44分')
        risks.append('属于相邻工业场景，需确认是否在本企业获客范围内')
    score=max(0,min(100,score))
    next_actions=['核对采购原文、报名条件和最新变更公告','确认自身资质、同类业绩及交付能力，决定投标或合作方式']
    if category=='智慧焚烧':
        next_actions.append('准备燃烧稳定性、吨垃圾发电量、人工干预次数等指标的现场诊断提纲')
    elif category=='烟气提标':
        next_actions.append('核对排放目标与现有工艺，评估控制优化、药剂优化或总包配套切入点')
    if matches:
        next_actions.append('参考名录找到项目公司；历史信息公开联系人不等同采购负责人，联系前核实')
    if document_closed:
        next_actions=['文件获取或报名已截止，先核实此前是否已按要求取得文件并完成手续','仅已完成手续且满足资质时核对剩余投标时间；未完成者关注合法合作或后续项目，不假定能够补报名']
    if stage in ['中标候选人','中标结果','终止'] or expired:
        next_actions=['作为历史项目、竞争方案和后续升级需求研究；不发起本期投标','核实中标方及业主后续建设计划，评估合作切入点']
    evidence={
        '投标截止时间':deadline_evidence or '未取得明确证据',
        '文件获取或报名截止':document_evidence or '未取得明确证据；新进入条件需核实',
        '采购人':buyer_match.group(0) if buyer_match else ('名称匹配，需原文核实' if buyer else '未取得'),
        '金额':budget_match.group(0) if budget_match else '未取得，不将投标保证金当预算',
        '解析范围':'公开正文' if content else '公开列表标题',
        '来源页面':raw['url'],
    }
    summary=f'{title}。方向为{category}，当前公告阶段为{stage}。'+ ('有明确且未到期的投标截止证据。' if actionable else '有效性或投标资格仍需核实，详见风险提示。')
    if document_closed and not expired and stage in ('招标','询价'):
        summary=title+'。投标期限尚未结束，但文件获取或报名已截止；能否参与取决于是否已完成相关手续。'
    superseded=raw.get('superseded_by')
    if isinstance(superseded,dict) and superseded.get('stage') in ('中标候选人','中标结果','终止','变更') and (superseded.get('published_at') or '') >= (published_at or ''):
        reason=f"同标段已发现后续{superseded['stage']}公告，原公告不可直接作为当前投标依据"
        score=min(score,49);actionable=False
        risks=list(dict.fromkeys(risks+[reason]))
        reasons.append('同标段后续状态覆盖原公告，最高49分')
        summary=title+'。'+reason+'。'
        next_actions=['先核实同标段最新公告：'+superseded.get('url',''),'最新采购状态未确认前，不参与本期投标']
        evidence['同标段后续公告']=superseded.get('title','')+' '+superseded.get('url','')
    return {**raw,'title':title,'content':content,'published_at':published_at,'published_at_precision':published_at_precision,'deadline':deadline,'deadline_precision':deadline_precision,'document_deadline':document_deadline,'document_deadline_precision':document_deadline_precision,'document_state':'expired' if document_closed else 'open' if document_deadline else 'unknown','analysis_version':ANALYSIS_VERSION,'analysis_date':current.date().isoformat(),'temporal_state':'expired' if expired else 'open' if deadline else 'unknown','buyer':buyer,'province':province,'city':city,'category':category,'stage':stage,'budget_text':budget_text,'score':score,'priority':'高' if score>=75 else '中' if score>=50 else '低','actionable':actionable,'summary':summary,'reasons':reasons,'risks':risks,'next_actions':next_actions,'matched_companies':matches,'evidence':evidence,'ai_analysis':None}
