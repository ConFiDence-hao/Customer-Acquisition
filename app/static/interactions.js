'use strict';

Object.assign(viewMeta, {
 configuration:['系统配置','SYSTEM SETTINGS','管理订阅与自动任务','设置关注主题，分别管理个人推送和团队采集计划。'],
 pushes:['推送列表','MY INBOX','查看订阅推送的资讯','自动化任务将符合订阅条件的资讯送到这里，可按主题查看和跟进。'],
 followups:['我的跟进','MY FOLLOW-UPS','查看我的跟进记录','查看本人负责的公告与个人研判记录，备注仅自己可见。']
});
state.configTab='subscriptions';
state.pushTopicId='';
state.pushSort='pushed_at';
state.topicExpanded=false;
state.topicSearch='';

// This footer is also used by the existing client-side management tables.
pagination=function(data,type='opportunities') {
 const total=Number(data.total||0),size=Number(data.page_size||pageSize(type));
 const pages=Math.max(1,Math.ceil(total/size)),page=Math.max(1,Number(data.page||1));
 const numbers=[...new Set([1,pages,page-1,page,page+1].filter(n=>n>=1&&n<=pages))].sort((a,b)=>a-b);
 const button=(n,label,current=false,disabled=false)=>`<button type="button" class="secondary page-number${current?' active':''}" data-page="${n}" data-page-type="${esc(type)}"${current?' aria-current="page"':''}${disabled?' disabled':''}>${label}</button>`;
 let previous=0;
 const numbered=numbers.map(n=>{const gap=previous&&n-previous>1?'<span class="page-gap" aria-hidden="true">…</span>':'';previous=n;return gap+button(n,n,n===page);}).join('');
 return `<div class="pagination"><span>共 ${total} 条 · ${pages} 页</span><label>每页 <select data-page-size="${esc(type)}" aria-label="每页显示数量">${pageSizeOptions(type).map(n=>`<option value="${n}"${n===size?' selected':''}>${n} 条</option>`).join('')}</select></label><div class="page-buttons">${button(page-1,'上一页',false,page<=1)}${numbered}${button(page+1,'下一页',false,page>=pages)}</div><div class="page-jump"><label>跳至 <input type="number" min="1" max="${pages}" step="1" value="${page}" aria-label="跳转页码" required> 页</label><button type="button" class="secondary" data-page="${page}" data-page-type="${esc(type)}" data-page-jump>跳转</button></div></div>`;
};
// Validate before both delegated server paging and local table handlers see it.
document.addEventListener('click',event=>{
 const button=event.target.closest('[data-page-jump]');if(!button)return;
 const input=button.closest('.page-jump').querySelector('input');
 if(!input.checkValidity()){event.preventDefault();event.stopImmediatePropagation();input.reportValidity();return;}
 button.dataset.page=String(Number(input.value));
},true);
document.addEventListener('keydown',event=>{
 if(event.key==='Enter'&&event.target.matches('.page-jump input')){event.preventDefault();event.target.closest('.page-jump').querySelector('button').click();}
});
$('#refresh-view').onclick=()=>loadView();

async function renderConfiguration(version) {
 if(state.configTab==='automation')await renderAutomation(version);else await renderTopicConfig(version);
 if(version!==state.renderVersion)return;
 $('#content').insertAdjacentHTML('afterbegin',`<nav class="news-tabs config-tabs" aria-label="系统配置分类"><button type="button" data-config-tab="subscriptions" class="${state.configTab==='subscriptions'?'active':''}" aria-pressed="${state.configTab==='subscriptions'}">订阅情况</button><button type="button" data-config-tab="automation" class="${state.configTab==='automation'?'active':''}" aria-pressed="${state.configTab==='automation'}">自动化任务</button></nav>`);
 $('#content').querySelectorAll('[data-config-tab]').forEach(button=>button.onclick=()=>{state.configTab=button.dataset.configTab;history.replaceState(null,'',`#configuration?tab=${state.configTab}`);loadView({anchor:'.config-tabs'});});
}

function updateCollectionStatus(progress) {
 if(!isAdmin()||state.view!=='today')return;
 const schedule=state.collectionSchedule,heading=$('#today-collection-status');
 if(!schedule||!heading)return;
 const wasOpen=heading.querySelector('details')?.open;
 heading.hidden=false;heading.innerHTML=collectionStatusMarkup(schedule,progress);
 if(wasOpen)heading.querySelector('details').open=true;
}

renderToday=async function(version) {
 await renderOpportunities(version);if(version!==state.renderVersion)return;
 const heading=$('#today-collection-status');
 if(!isAdmin()){
  const updated=state.dashboard?.stats?.last_update;
  if(heading){heading.hidden=!updated;heading.innerHTML=updated?`<span class="shared-heading-status">数据更新 ${esc(dateText(updated))}</span>`:'';}
  return;
 }
 const [schedule,progress]=await Promise.all([api('/api/collection-schedule'),api('/api/collection-progress')]);
 if(version!==state.renderVersion)return;
 state.collectionSchedule=schedule;updateCollectionStatus(progress);
 await pollCollection(false);
};

function pushRoute() {
 const params=queryToParams({...state.pushFacets,q:state.pushQuery,read:state.pushRead,sort:state.pushSort,page:state.pushPage});if(state.pushTopicId)params.set('topic',state.pushTopicId);
 history.replaceState(null,'',`#pushes${params.size?'?'+params:''}`);
}
function openTopicResults(id) {
 state.view='pushes';state.pushTopicId=String(id);state.pushQuery='';state.pushRead='';state.pushFacets={};state.pushPage=1;state.topicSearch='';
 pushRoute();loadView({anchor:'.count-line',navigation:true});
}
function topicTags(topics) {return `<div class="keyword-tags topic-tags">${(topics||[]).map(t=>tag(t.name)).join('')}</div>`;}
function followupTag(item){return personalFollowupBadge(item);}
function followupRowContent(item){
 const note=String(item.note||''),excerpt=note.length>140?note.slice(0,140)+'…':note;
 return `<td class="wrap-cell"><button type="button" class="card-title" data-detail="${Number(item.id)}">${esc(item.title||'标题待核实')}</button><span class="cell-sub">${esc(item.source_name||'来源待核实')}</span><div data-assignment-markers>${assignmentMarkers(item)}</div></td><td class="time-cell">${esc(sourceTime(item.published_at,item.published_at_precision))}</td><td>${followupTag(item)}</td><td class="team-followup-cell">${teamFollowupBadge(item)}</td><td class="followup-note-cell"><span class="followup-note">${esc(excerpt||'暂无备注')}</span></td><td class="time-cell">${esc(dateText(item.followup_updated_at))}</td><td><button type="button" class="text-button" data-detail="${Number(item.id)}">查看详情</button></td>`;
}
function followupRow(item){return `<tr data-followup-id="${Number(item.id)}">${followupRowContent(item)}</tr>`;}
function followupResults(items){
 return items.length?`<div class="table-wrap"><table class="followups-table"><thead><tr><th>公告</th><th>发布时间</th><th>我的状态</th><th>团队负责人 / 阶段</th><th>我的备注摘要</th><th>最近跟进时间</th><th>操作</th></tr></thead><tbody>${items.map(followupRow).join('')}</tbody></table></div>`:empty('没有符合条件的跟进记录','主动开始跟进、被管理员指派，或保存个人研判后，记录会出现在这里。');
}
async function renderFollowups(version){
 const filters=state.followupFilters,size=pageSize('followups');
 const params=queryToParams({...filters,page:state.followupPage,page_size:size});queryRoute('followups',filters,state.followupPage);
 const data=await api(`/api/followups?${params}`);if(version!==state.renderVersion)return;
 const lastPage=Math.max(1,Math.ceil(data.total/size));if(state.followupPage>lastPage){state.followupPage=lastPage;return renderFollowups(version);}

 $('#content').innerHTML=`<form id="followup-filters" class="filters"><div class="filter-row"><input class="search-field" name="q" maxlength="150" aria-label="搜索公告、采购方或我的备注" placeholder="搜索公告、采购方或我的备注" value="${esc(filters.q)}"><select name="status" aria-label="跟进状态">${options(statuses,filters.status,'全部跟进状态')}</select><select name="sort" aria-label="排序方式"><option value="followup_updated_at"${filters.sort==='followup_updated_at'?' selected':''}>最近跟进</option><option value="published_at"${filters.sort==='published_at'?' selected':''}>最新发布</option></select><button type="submit" class="primary">搜索</button></div>${queryFacetFields(filters)}</form><div class="count-line"><span>本人负责 / 个人研判 · <strong id="followup-total">${data.total}</strong> 条</span><span>负责人公开阶段团队可见，备注仅自己可见</span></div><div id="followup-live-results">${followupResults(data.items)}</div>${!data.items.length&&!filters.q&&!filters.status?'<div class="source-actions"><button type="button" class="text-button" data-view="today">去今日资讯查看公告 ↗</button></div>':''}<div id="followup-live-pagination">${pagination(data,'followups')}</div><p class="muted result-note">这里汇总本人负责的公告与自己保存的个人研判，包含尚未推送的公告；同一公告只显示一次。同名公告可能来自不同日期或网址，请核对发布时间与来源原文。</p>`;
 const form=$('#followup-filters');bindQueryFacets(form);form.onsubmit=e=>{e.preventDefault();const value=new FormData(form);state.followupFilters={...readQueryFacets(form),status:value.get('status')||'',sort:value.get('sort')||'followup_updated_at'};state.followupPage=1;loadView({anchor:resultsAnchor()});};form.querySelectorAll('select').forEach(select=>select.onchange=()=>form.requestSubmit());
}

renderPushes=async function(version) {
 const topicData=await api('/api/my-subscriptions');if(version!==state.renderVersion)return;
 state.topics=topicData.items;
 if(state.pushTopicId&&!state.topics.some(t=>String(t.id)===String(state.pushTopicId)))throw new Error('该主题不可访问或已归档，请重新选择主题。');
 const params=queryToParams({...state.pushFacets,page:state.pushPage,page_size:pageSize('pushes'),q:state.pushQuery||'',sort:state.pushSort,read:state.pushRead||''});pushRoute();
 if(state.pushTopicId)params.set('subscription_id',state.pushTopicId);
 const data=await api(`/api/pushes?${params}`);if(version!==state.renderVersion)return;
 // Clamp a page when filters or another session changed the record count.
 const lastPage=Math.max(1,Math.ceil(data.total/pageSize('pushes')));
 if(state.pushPage>lastPage){state.pushPage=lastPage;return renderPushes(version);}
 const selected=state.topics.find(t=>String(t.id)===String(state.pushTopicId));
 const sortOptions=[['pushed_at','最新推送'],['published_at','最新发布'],['score','匹配度优先']];
 const rows=data.items.map(x=>{
  const title=`<button type="button" class="card-title" data-push-open="${x.push_id}">${esc(x.title)}</button><span class="cell-sub">${esc(x.source_name||'来源待核实')} · ${esc(x.stage||'阶段待核实')}</span>`;
  return `<tr><td class="wrap-cell">${title}</td><td class="wrap-cell">${topicTags(x.associated_topics)}<button type="button" class="text-button" data-push-evidence="${x.push_id}">首次推送依据</button></td><td class="time-cell">${esc(sourceTime(x.published_at,x.published_at_precision))}</td><td class="time-cell">${esc(dateText(x.pushed_at))}</td><td class="push-followup">${followupTag(x)}</td><td class="team-followup-cell">${teamFollowupBadge(x)}</td><td class="push-read">${tag(x.read_at?'已读':'未读',x.read_at?'':'blue')}</td><td><button type="button" class="text-button" data-push-open="${x.push_id}">查看详情</button></td></tr>`;
 }).join('');
 $('#content').innerHTML=`<section class="topic-navigation" aria-label="主题分类"><nav id="theme-options" class="topic-tab-strip" aria-label="推送主题"></nav><details class="theme-picker"><summary aria-label="搜索切换主题">切换主题 <span aria-hidden="true">⌄</span></summary><div class="theme-picker-popover"><input id="theme-search" type="search" aria-label="查找主题" placeholder="搜索主题" value="${esc(state.topicSearch)}"><div id="theme-picker-options" class="theme-picker-options"></div></div></details></section><div class="count-line"><span><strong>${esc(selected?.name||'全部主题')}</strong> · ${data.total} 条站内推送</span><span>账号全部未读 ${data.unread} 条</span>${data.unread?`<button type="button" class="text-button" data-push-read-all>${selected?'本主题全部标为已读':'全部标为已读'}</button>`:''}</div><form id="push-filters" data-assist-context="history" class="filters"><div class="filter-row"><input class="search-field" name="q" aria-label="搜索推送标题" placeholder="在当前主题范围内搜索推送标题" value="${esc(state.pushQuery||'')}"><select name="sort" aria-label="排序方式">${sortOptions.map(([value,label])=>`<option value="${value}"${value===state.pushSort?' selected':''}>${label}</option>`).join('')}</select><select name="read" aria-label="阅读状态">${[['','全部阅读状态'],['unread','未读'],['read','已读']].map(([value,label])=>`<option value="${value}"${value===(state.pushRead||'')?' selected':''}>${label}</option>`).join('')}</select><button type="submit" class="primary">搜索</button></div><input type="hidden" name="subscription_id" value="${esc(state.pushTopicId||'')}">${queryFacetFields(state.pushFacets||{},true)}</form>${data.items.length?`<div class="table-wrap"><table class="push-table"><thead><tr><th>推送资讯</th><th>关联主题</th><th>发布时间</th><th>推送时间</th><th>我的状态</th><th>团队负责人 / 阶段</th><th>阅读状态</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div>`:empty('暂无推送',state.pushQuery||state.pushRead?'没有符合筛选条件的推送，可调整关键词或阅读状态。':state.topics.length?'启用订阅与自动推送任务后，符合主题条件的资讯会送到这里。请到自动化任务确认运行状态和最近执行时间。':'先到系统配置创建订阅并启用自动推送任务。')}${!data.items.length&&!state.pushQuery&&!state.pushRead?`<div class="source-actions"><button type="button" class="text-button" data-view="configuration" data-config-target="${state.topics.length?'automation':'subscriptions'}">${state.topics.length?'查看推送任务':'配置订阅'} ↗</button></div>`:''}${!data.items.length?selected?`<div class="source-actions"><button type="button" class="secondary" data-push-diagnosis="${selected.id}">查看推送诊断</button></div>`:'<p class="muted">先在上方选择一个主题，即可查看该主题的推送诊断。</p>':''}${pagination(data,'pushes')}<p class="muted result-note">默认按最新推送排序，同轮推送按匹配度排列。同一资讯只推送一次，可关联多个主题；首次推送依据保留原始记录。${selected&&!selected.enabled?' 此主题已停用，历史推送仍保留，不再产生新的推送。':''}</p>`;
 const bindTopics=parent=>parent.querySelectorAll('[data-push-topic]').forEach(button=>button.onclick=()=>{state.pushTopicId=button.dataset.pushTopic;state.pushPage=1;state.pushRead='';state.topicSearch='';pushRoute();loadView();});
 $('#theme-options').innerHTML=pushTopicTabs(state.topics,state.pushTopicId);bindTopics($('#theme-options'));
 const drawChoices=()=>{$('#theme-picker-options').innerHTML=pushTopicChoices(state.topics,state.pushTopicId,state.topicSearch);bindTopics($('#theme-picker-options'));};
 drawChoices();$('#theme-search').oninput=e=>{state.topicSearch=e.target.value;drawChoices();};
 $('#content .theme-picker').addEventListener('toggle',event=>{if(event.currentTarget.open)$('#theme-search').focus();});
 $('#theme-options [aria-pressed="true"]')?.scrollIntoView({block:'nearest',inline:'nearest'});
 bindQueryFacets($('#push-filters'));$('#push-filters').onsubmit=e=>{e.preventDefault();const form=new FormData(e.target),facets=readQueryFacets(e.target);state.pushQuery=facets.q;delete facets.q;state.pushFacets=facets;state.pushTopicId=form.get('subscription_id')||'';state.pushSort=form.get('sort');state.pushRead=form.get('read')||'';state.pushPage=1;pushRoute();loadView({anchor:resultsAnchor()});};
 $('#push-filters').querySelectorAll('select').forEach(input=>input.onchange=()=>$('#push-filters').requestSubmit());
 $('#content').querySelectorAll('[data-push-evidence]').forEach(button=>button.onclick=()=>{
  const x=data.items.find(x=>x.push_id===Number(button.dataset.pushEvidence));
  openText('首次推送依据',`<h3>${esc(x.title)}</h3><p>首次推送：${esc(dateText(x.pushed_at))}</p><p>当时命中主题：${esc((x.matched_topics||[]).map(t=>t.name).join('、'))}</p><p>当时匹配分：${Number(x.topic_score||0)}</p><p>${esc((x.topic_reasons||[]).join('；'))}</p><p>当前关联主题：${esc((x.associated_topics||[]).map(t=>t.name).join('、'))}</p>`);
 });
 $('#content').querySelectorAll('[data-push-open]').forEach(button=>button.onclick=async()=>{
  const x=data.items.find(x=>x.push_id===Number(button.dataset.pushOpen));
  try {await api(`/api/pushes/${x.push_id}/read`,{method:'PATCH'});if(version!==state.renderVersion)return;
   const topic=state.topics.find(t=>String(t.id)===String(state.pushTopicId||x.subscription_id))?.id||'';
   openDetail(x.id,topic);button.closest('tr').querySelector('.push-read').innerHTML=tag('已读');
   if(!x.read_at){x.read_at=true;data.unread=Math.max(0,data.unread-1);$('#content .count-line>span:last-child').textContent=`账号全部未读 ${data.unread} 条`;}
  }catch(error){toast(error.message,true);}
 });
 const readAll=$('#content [data-push-read-all]');
 if(readAll)readAll.onclick=async()=>{
  if(!confirm(selected?`将「${selected.name}」的未读推送全部标为已读？`:'将全部未读推送标为已读？'))return;
  readAll.disabled=true;
  try {const result=await api('/api/pushes/read-all'+(selected?`?subscription_id=${encodeURIComponent(selected.id)}`:''),{method:'PATCH'});if(version!==state.renderVersion)return;toast(`已标记 ${result.updated} 条为已读`);loadView();}
  catch(error){readAll.disabled=false;toast(error.message,true);}
 };
 pushRoute();
};
