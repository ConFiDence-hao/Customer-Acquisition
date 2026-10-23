"""Page-scoped query facets; dates use Beijing day boundaries and one keyword facet."""
import json,re
from datetime import date,datetime,timedelta,timezone
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel,Field,ValidationError
from .analysis import PROVINCES

ZONE=timezone(timedelta(hours=8))
STAGES=['招标','询价','采购意向','变更','中标候选人','中标结果','终止','资讯']
STATUSES=['待研判','跟进中','已联系','已排除','已归档']
CATEGORIES=['智慧焚烧','烟气提标','控制系统','设备技改','其他']
ALIASES={'opportunity-filters':'opportunities','push-history':'pushes','push-matches':'recommendations',
         'followup-filters':'followups','company-filters':'companies','topic-search':'subscriptions','recommendation-filters':'recommendations'}
WORDS={'q','terms','term_mode'}
DATES={'published_from','published_to','deadline_state'}
SUPPORT={
 'opportunities':WORDS|DATES|{'province','stage','category','status','min_score','actionable','sort'},
 'pushes':WORDS|DATES|{'pushed_from','pushed_to','subscription_id','read','sort'},
 'followups':WORDS|DATES|{'status','sort'},'companies':{'q','province'},
 'subscriptions':{'q'},'recommendations':WORDS|{'status','sort','subscription_id'},
}
SORTS={'opportunities':{'score','published_at','deadline'},'pushes':{'pushed_at','published_at','score'},
       'followups':{'followup_updated_at','published_at'},'recommendations':{'score','published_at'}}


class NoticeParams(BaseModel):
    terms:str=Field(default='',max_length=3000)
    term_mode:Literal['any','all']='any'
    published_from:str=Field(default='',max_length=20)
    published_to:str=Field(default='',max_length=20)
    deadline_state:Literal['','open','expired','unknown','eligible']=''


class PushParams(NoticeParams):
    q:str=Field(default='',max_length=150)
    pushed_from:str=Field(default='',max_length=20)
    pushed_to:str=Field(default='',max_length=20)
    subscription_id:int|None=Field(default=None,ge=1)
    read:Literal['','unread','read']=''
    sort:Literal['pushed_at','published_at','score']='pushed_at'
    page:int=Field(default=1,ge=1,le=10000)
    page_size:int=Field(default=10,ge=5,le=20)


class FollowupParams(NoticeParams):
    q:str=Field(default='',max_length=150)
    status:str=Field(default='',max_length=30)
    sort:Literal['followup_updated_at','published_at']='followup_updated_at'
    page:int=Field(default=1,ge=1,le=10000)
    page_size:int=Field(default=10,ge=5,le=20)


class QuerySpec(BaseModel):
    page:str
    q:str=Field(default='',max_length=150)
    terms:list[str]=Field(default_factory=list,max_length=12)
    term_mode:Literal['any','all']='any'
    published_from:str=''
    published_to:str=''
    pushed_from:str=''
    pushed_to:str=''
    deadline_state:Literal['','open','expired','unknown','eligible']=''
    province:str=''
    category:str=''
    stage:str=''
    status:str=''
    min_score:int=Field(default=0,ge=0,le=100)
    actionable:bool=False
    sort:str=''
    subscription_id:int|None=None
    read:Literal['','unread','read']=''

    def to_params(self):
        values={key:value for key,value in self.model_dump(exclude={'page','terms','q'}).items() if key in SUPPORT[self.page] and value not in ('',None,False,0)}
        if self.terms:values['terms']=json.dumps(self.terms,ensure_ascii=False)
        elif self.q:values['q']=self.q
        if not self.terms:values.pop('term_mode',None)
        return values

    def extended(self):return bool(self.terms or self.published_from or self.published_to or self.pushed_from or self.pushed_to or self.deadline_state)


def canonical_day(value,reference):
    aliases={'今天':reference.date(),'today':reference.date(),'昨天':reference.date()-timedelta(days=1),
             'yesterday':reference.date()-timedelta(days=1),'近7天':reference.date()-timedelta(days=6)}
    if value in aliases:return aliases[value].isoformat()
    try:return date.fromisoformat(value).isoformat()
    except (TypeError,ValueError):raise HTTPException(422,'日期须使用 YYYY-MM-DD，或明确的今天／昨天') from None


def normalize(page,fields,reference):
    page=ALIASES.get(page,page)
    if page not in SUPPORT:raise HTTPException(422,'此页面不支持智能查询')
    values={key:value for key,value in fields.items() if value not in ('',None,[]) and key not in ('page','page_size')}
    unknown=set(values)-SUPPORT[page]
    if unknown:raise HTTPException(422,'当前页面不支持这些查询条件：'+'、'.join(sorted(unknown)))
    terms=values.get('terms',[])
    if isinstance(terms,str):
        try:terms=json.loads(terms) if terms.lstrip().startswith('[') else re.split(r'[,，;；\n]+',terms)
        except ValueError:raise HTTPException(422,'关键词列表格式无效') from None
    if not isinstance(terms,list) or any(not isinstance(term,str) or len(term)>60 for term in terms):raise HTTPException(422,'关键词须为最多12个简短词语')
    terms=list(dict.fromkeys(term.strip() for term in terms if term.strip()))
    if terms:values.update(terms=terms,q='')
    for key in ('published_from','published_to','pushed_from','pushed_to'):
        if values.get(key):values[key]=canonical_day(values[key],reference.astimezone(ZONE))
    for prefix in ('published','pushed'):
        if values.get(prefix+'_from') and values.get(prefix+'_to') and values[prefix+'_from']>values[prefix+'_to']:
            raise HTTPException(422,'日期起点不能晚于结束日')
    province=values.get('province','')
    if province:
        aliases={p+'省':p for p in PROVINCES};aliases.update({p+'市':p for p in ['北京','上海','天津','重庆']})
        aliases.update({'广西壮族自治区':'广西','宁夏回族自治区':'宁夏','新疆维吾尔自治区':'新疆','内蒙古自治区':'内蒙古','西藏自治区':'西藏'})
        values['province']=aliases.get(province,province)
        if values['province'] not in PROVINCES:raise HTTPException(422,'请使用系统可查询的省份，城市或区县条件暂不支持')
    for key,choices in [('stage',STAGES),('status',STATUSES),('category',CATEGORIES)]:
        if values.get(key) and values[key] not in choices:raise HTTPException(422,'当前系统不支持该'+key+'值')
    if values.get('sort') and values['sort'] not in SORTS.get(page,set()):raise HTTPException(422,'当前页面不支持该排序方式')
    try:return QuerySpec(page=page,**values)
    except ValidationError:raise HTTPException(422,'查询条件的类型或范围不符合本页面规则') from None


def validate_params(page,keys):
    allowed=SUPPORT[ALIASES.get(page,page)]|{'page','page_size'}
    unknown=set(keys)-allowed
    if unknown:raise HTTPException(422,'当前页面不支持这些查询参数：'+'、'.join(sorted(unknown)))


def parse_time(value):
    if not value:return None
    try:
        result=datetime.fromisoformat(value)
        return result.replace(tzinfo=ZONE) if result.tzinfo is None else result.astimezone(ZONE)
    except (TypeError,ValueError):return None


def in_range(value,begin,end):
    if not begin and not end:return True
    actual=parse_time(value)
    if actual is None:return False
    if begin and actual<parse_time(begin):return False
    if end and actual>=parse_time(end)+timedelta(days=1):return False
    return True


def matches_notice(item,spec,now):
    title=item.get('title') or ''
    if spec.terms:
        text=(title+'\n'+(item.get('content') or '')).casefold()
        hits=[term.casefold() in text for term in spec.terms]
        if not (all(hits) if spec.term_mode=='all' else any(hits)):return False
    elif spec.q:
        text=title
        if spec.page in ('opportunities','followups'):text+=' '+(item.get('buyer') or '')
        if spec.page=='followups':text+=' '+(item.get('note') or '')
        if spec.q.casefold() not in text.casefold():return False
    if any(getattr(spec,key) and getattr(spec,key)!=item.get(key) for key in ('province','category','stage','status')):return False
    if (item.get('score') or 0)<spec.min_score:return False
    if not in_range(item.get('published_at'),spec.published_from,spec.published_to):return False
    if not in_range(item.get('pushed_at'),spec.pushed_from,spec.pushed_to):return False
    if spec.read and (bool(item.get('read_at'))!=(spec.read=='read')):return False
    deadline=parse_time(item.get('deadline'));document=parse_time(item.get('document_deadline'))
    closed=bool(item.get('superseded_by')) or item.get('stage') in ('终止','中标候选人','中标结果')
    unexpired=bool(deadline and deadline>now and not closed)
    eligible=unexpired and bool(item.get('content')) and bool(item.get('actionable')) and bool(document and document>now) and item.get('document_state')!='expired'
    if spec.deadline_state=='open' and not unexpired:return False
    if spec.deadline_state=='eligible' and not eligible:return False
    if spec.deadline_state=='expired' and not (closed or deadline and deadline<=now):return False
    if spec.deadline_state=='unknown' and deadline is not None:return False
    if spec.actionable and (not item.get('actionable') or item.get('status') in ('已排除','已归档')):return False
    return True
