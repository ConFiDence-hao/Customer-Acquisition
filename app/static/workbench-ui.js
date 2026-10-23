'use strict';

function noticeActiveFilters(fields){
 const active=[];
 for(const [key,label] of [['category','方向'],['province','区域'],['stage','阶段'],['status','跟进']])if(fields[key])active.push([key,`${label}：${fields[key]}`]);
 if(Number(fields.min_score)>0)active.push(['min_score',`${Number(fields.min_score)} 分以上`]);
 if(fields.actionable)active.push(['actionable','仅有效商机']);
 let terms=fields.terms;
 if(typeof terms==='string'){try{terms=JSON.parse(terms);}catch{terms=[];}}
 if(Array.isArray(terms)&&terms.length)active.push(['terms',`${fields.term_mode==='all'?'全部词':'任意词'}：${terms.join('、')}`]);
 for(const [key,label] of [['published_from','发布起日'],['published_to','发布止日']])if(fields[key])active.push([key,`${label}：${fields[key]}`]);
 const deadlines={open:'明确未截止',eligible:'还能报名',expired:'已截止或已结束',unknown:'截止时间待核实'};
 if(deadlines[fields.deadline_state])active.push(['deadline_state',deadlines[fields.deadline_state]]);
 return active;
}
function noticeFilterChips(fields){
 const active=noticeActiveFilters(fields);
 return active.length?`<div class="active-filter-chips" aria-label="已选筛选条件">${active.map(([key,label])=>`<button type="button" data-clear-filter="${key}" aria-label="移除${esc(label)}">${esc(label)}<span aria-hidden="true">×</span></button>`).join('')}<button type="button" class="clear-all-filters">清空筛选</button></div>`:'';
}
function noticeFilterMarkup(f){
 const count=noticeActiveFilters(f).length;
 const select=(name,label,values,empty)=>`<label>${label}<select name="${name}" aria-label="${label}">${options(values,f[name],empty)}</select></label>`;
 const scores=[...new Set([0,40,60,80,state.defaultMinScore,Number(f.min_score||0)])].sort((a,b)=>a-b);
 return `<form id="opportunity-filters" class="filters compact-notice-filters"><div class="filter-toolbar"><div class="search-control"><input class="search-field" name="q" aria-label="搜索项目、采购方或关键词" placeholder="搜索项目、采购方或关键词" value="${esc(f.q||'')}"><button type="submit" class="primary">搜索</button></div><div class="filter-tools"><label class="sort-control"><span class="sr-only">排序方式</span><select name="sort" aria-label="排序方式">${[['published_at','最新发布'],['score','匹配度优先'],['deadline','截止时间优先']].map(([value,label])=>`<option value="${value}"${value===f.sort?' selected':''}>${label}</option>`).join('')}</select></label><details class="filter-popover"><summary>筛选${count?`<span class="filter-count">${count}</span>`:''}<span aria-hidden="true">⌄</span></summary><div class="filter-popover-body"><div class="filter-popover-heading"><strong>筛选资讯</strong><button type="button" class="text-button" id="reset-filters">恢复策略</button></div><div class="filter-field-grid">${select('category','业务方向',categories,'全部方向')}${select('province','区域',provinces,'全国区域')}${select('stage','公告阶段',stages,'全部阶段')}${select('status','跟进状态',statuses,'全部状态')}<label>最低匹配分<select name="min_score" aria-label="最低匹配分">${scores.map(n=>`<option value="${n}"${n===Number(f.min_score||0)?' selected':''}>${n?`${n} 分以上${n===state.defaultMinScore?' · 策略':''}`:'不限评分'}</option>`).join('')}</select></label><label class="check-label"><input type="checkbox" name="actionable"${f.actionable?' checked':''}>仅有效商机</label></div>${queryFacetFields(f,false,{inline:true,showTags:false})}<div class="filter-popover-footer"><button type="button" class="text-button" data-close-filters>取消</button><button type="submit" class="primary">应用筛选</button></div></div></details></div></div>${noticeFilterChips(f)}</form>`;
}
function bindCompactNoticeFilters(form){
 const disclosure=form.querySelector('.filter-popover');
 form.querySelector('[data-close-filters]')?.addEventListener('click',()=>{form.reset();disclosure.open=false;});
 const clear=key=>{
  if(!key){for(const field of ['category','province','stage','status','terms','published_from','published_to','deadline_state'])state.filters[field]='';state.filters.min_score='0';state.filters.actionable=false;state.filters.term_mode='any';}
  else if(key==='terms'){state.filters.terms='';state.filters.q='';state.filters.term_mode='any';}
  else state.filters[key]=key==='min_score'?'0':key==='actionable'?false:'';
  persistFilters();state.page=1;loadView();
 };
 form.querySelectorAll('[data-clear-filter]').forEach(button=>button.onclick=()=>clear(button.dataset.clearFilter));
 form.querySelector('.clear-all-filters')?.addEventListener('click',()=>clear());
}
function pushTopicTabs(topics,selected){
 return `<button type="button" class="topic-tab${!selected?' active':''}" data-push-topic="" aria-pressed="${!selected}">全部主题</button>${topics.map(topic=>`<button type="button" class="topic-tab${String(topic.id)===String(selected)?' active':''}" data-push-topic="${Number(topic.id)}" aria-pressed="${String(topic.id)===String(selected)}"><span>${esc(topic.name)}</span><small>${Number(topic.push_count||0)}</small></button>`).join('')}`;
}
function pushTopicChoices(topics,selected,search=''){
 const filtered=topics.filter(topic=>topic.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
 return filtered.length?filtered.map(topic=>`<button type="button" class="topic-choice${String(topic.id)===String(selected)?' active':''}" data-push-topic="${Number(topic.id)}" aria-pressed="${String(topic.id)===String(selected)}"><span>${esc(topic.name)}${topic.enabled?'':' <small>已停用</small>'}</span><small>${Number(topic.push_count||0)}</small></button>`).join(''):'<p class="muted">未找到主题</p>';
}
function collectionStatusMarkup(schedule,progress){
 const run=progress.run,enabled=schedule.scheduler_enabled&&schedule.enabled;
 const label=progress.active?'正在采集':enabled?'自动采集':'自动采集已暂停';
 const next=enabled?(run?.started_at?dateText(new Date(new Date(run.started_at).getTime()+schedule.interval_minutes*60000).toISOString()):'等待首轮执行'):'已暂停';
 const tone=progress.active?'active':run&&['部分完成','失败','中断'].includes(run.status)?'warning':enabled?'enabled':'paused';
 return `<details class="collection-heading-status ${tone}"><summary><span class="status-dot" aria-hidden="true"></span>${label}${enabled?` · ${Number(schedule.interval_minutes)} 分钟`:''}<span aria-hidden="true">⌄</span></summary><div class="collection-status-popover"><strong>采集计划</strong><dl><div><dt>最近采集</dt><dd>${esc(run?dateText(run.started_at):'尚未采集')}</dd></div><div><dt>执行状态</dt><dd>${esc(progress.active?progress.phase||'正在采集':run?.status||'尚未执行')}</dd></div><div><dt>下次计划</dt><dd>${esc(next)}</dd></div></dl><button type="button" class="text-button" data-view="configuration" data-config-target="automation" data-nav-anchor="#collection-automation">任务配置 ↗</button></div></details>`;
}

if(typeof document!=='undefined'&&document.addEventListener){
 document.addEventListener('click',event=>document.querySelectorAll('.filter-popover[open],.theme-picker[open],.collection-heading-status[open]').forEach(details=>{if(!details.contains(event.target))details.open=false;}));
 document.addEventListener('keydown',event=>{if(event.key!=='Escape')return;document.querySelectorAll('.filter-popover[open],.theme-picker[open],.collection-heading-status[open]').forEach(details=>{details.open=false;details.querySelector('summary')?.focus({preventScroll:true});});});
}
