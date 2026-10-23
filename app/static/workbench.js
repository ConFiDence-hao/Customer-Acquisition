'use strict';
Object.assign(viewMeta,{
 today:['今日资讯','TODAY / INTELLIGENCE','今日资讯','查看核心商机与公开公告，筛选值得跟进的机会。'],
 subscriptions:['订阅配置','SUBSCRIPTION SETTINGS','配置你的业务关注方向','用行业、关键词和监测网站定义你想获得的资讯。'],
 'subscription-status':['订阅情况','MY SUBSCRIPTIONS','我的订阅情况','查看当前账号的订阅状态与累计站内推送。'],
 pushes:['推送列表','MY INBOX','为你汇集的机会','查看当前账号收到的站内推送，核查原文后安排跟进。'],
 automation:['自动化任务','AUTOMATION','查看自动化任务运行情况','查看个人订阅推送与团队采集任务的状态、执行计划和最近记录。']
});
state.newsTab='opportunities';state.topicQuery='';state.topicQueryDraft='';state.sizes={};state.localPages={};state.pushPage=1;
// Two-column card grids page in even sizes so the last row is never half empty.
const CARD_PAGE_SIZES={sources:[6,12,24]};
function pageSizeOptions(key){return CARD_PAGE_SIZES[key]||[5,10,20];}
function pageSize(key){return state.sizes[key]||(CARD_PAGE_SIZES[key]?.[1]??10);}
const popups=[];
function popup(title,node,restore=false){
 const focus=document.activeElement,placeholder=document.createComment('popup-origin');
 if(restore)node.before(placeholder);
 const dialog=document.createElement('dialog');dialog.className='work-dialog';dialog.setAttribute('aria-label',title);
 const header=document.createElement('div');header.className='dialog-heading';header.innerHTML=`<h2>${esc(title)}</h2><button type="button" class="icon-button" aria-label="关闭弹窗">×</button>`;
 dialog.append(header,node);document.body.append(dialog);popups.push(dialog);
 const close=()=>dialog.close();header.querySelector('button').onclick=close;
 dialog.addEventListener('close',()=>{if(restore&&placeholder.isConnected){placeholder.replaceWith(node);if(node.tagName==='DETAILS')node.open=false;}dialog.remove();const i=popups.indexOf(dialog);if(i>=0)popups.splice(i,1);if(focus?.isConnected)focus.focus({preventScroll:true});document.body.style.overflow=popups.length||!$('#drawer-backdrop').hidden?'hidden':'';});
 dialog.showModal();document.body.style.overflow='hidden';header.querySelector('button').focus({preventScroll:true});return dialog;
}
function mountEditor(form){return popup(form.querySelector('h2')?.textContent||'编辑配置',form);}
function closePopups(){[...popups].reverse().forEach(d=>d.close());}
function openAssist(panel){panel.open=true;popup('智能填写',panel,true);panel.querySelector('.assist-prompt').focus();}
function openText(title,html){const n=document.createElement('div');n.className='dialog-body';n.innerHTML=html;popup(title,n);}
function configurationEditorContext(){const origin=document.activeElement;return {owner:state.user?.id,session:state.sessionGeneration,version:state.renderVersion,view:state.view,tab:state.configTab,origin,selector:origin?.dataset?.editTopic?`[data-edit-topic="${Number(origin.dataset.editTopic)}"]`:origin?.dataset?.taskConfig?`[data-task-config="${origin.dataset.taskConfig}"]`:origin?.dataset?.toggleTopic?`[data-toggle-topic="${Number(origin.dataset.toggleTopic)}"]`:origin?.id==='new-topic'?'#new-topic':null};}
function configurationViewCurrent(context){return context.owner===state.user?.id&&context.session===state.sessionGeneration&&context.version===state.renderVersion&&context.view===state.view&&context.tab===state.configTab;}
function configurationEditorCurrent(context,form,dialog){return configurationViewCurrent(context)&&form.isConnected&&dialog.isConnected&&dialog.open;}
async function refreshConfiguration(context){
 if(!configurationViewCurrent(context)||document.querySelector('dialog[open]'))return;
 const version=state.renderVersion+1,activeBefore=document.activeElement,restoreOrigin=activeBefore===document.body||activeBefore===context.origin||context.dialog?.contains(activeBefore),restoreQuery=activeBefore?.name==='q'&&activeBefore.closest?.('#topic-search');await loadView({automaticRefresh:true});
 if(context.owner!==state.user?.id||context.session!==state.sessionGeneration||context.view!==state.view||context.tab!==state.configTab||version!==state.renderVersion||!$('#account-dropdown').hidden||document.querySelector('dialog[open]'))return;
 if(document.activeElement===document.body||document.activeElement===context.origin||document.activeElement===activeBefore){const target=restoreQuery?$('#topic-search [name=q]'):restoreOrigin?(context.selector?$(context.selector):null)||$('#topic-search [name=q]'):null;target?.focus({preventScroll:true});}
}
function lockConfigurationEditor(form){const controls=[...form.querySelectorAll('input,select,textarea,button')].map(control=>[control,control.disabled]);form.configurationBusy=true;controls.forEach(([control])=>control.disabled=true);return()=>{form.configurationBusy=false;controls.forEach(([control,disabled])=>{if(control.isConnected)control.disabled=disabled;});};}
async function saveConfigurationEditor(form,dialog,context,path,body,message){
 if(form.configurationBusy||!configurationEditorCurrent(context,form,dialog))return;
 const button=form.querySelector('[type=submit]'),text=button.textContent,unlock=lockConfigurationEditor(form);button.textContent='正在保存…';
 try{await api(path,{method:form.id==='topic-form'?(path.endsWith('/subscriptions')?'POST':'PUT'):'PUT',body:JSON.stringify(body)});if(!configurationViewCurrent(context))return;context.dialog=dialog;if(configurationEditorCurrent(context,form,dialog)){toast(message);dialog.close();await refreshConfiguration(context);}else if(!document.querySelector('dialog[open]'))await refreshConfiguration(context);}
 catch(error){if(configurationEditorCurrent(context,form,dialog))toast(error.message,true);}
 finally{unlock();if(button.isConnected)button.textContent=text;}
}
function localPager(container,key,items,render){
 let qpage=state.localPages[key]||1;const size=pageSize(key);qpage=Math.max(1,Math.min(qpage,Math.ceil(items.length/size)||1));state.localPages[key]=qpage;
 container.innerHTML=render(items.slice((qpage-1)*size,qpage*size))+pagination({total:items.length,page:qpage,page_size:size},key);
 container.querySelectorAll('[data-page]').forEach(b=>b.onclick=e=>{e.stopPropagation();state.localPages[key]=Number(b.dataset.page);localPager(container,key,items,render);scrollToTarget(container);});
 container.querySelector('[data-page-size]').onchange=e=>{e.stopPropagation();state.sizes[key]=Number(e.target.value);state.localPages[key]=1;localPager(container,key,items,render);scrollToTarget(container);};
}
function paginateNodes(parent,selector,key){
 const nodes=[...parent.querySelectorAll(selector)];if(!nodes.length)return;
 const footer=document.createElement('div');parent.after(footer);
 const draw=()=>{const size=pageSize(key),page=Math.min(state.localPages[key]||1,Math.ceil(nodes.length/size)||1);nodes.forEach((n,i)=>n.hidden=i<(page-1)*size||i>=page*size);footer.innerHTML=pagination({total:nodes.length,page,page_size:size},key);footer.querySelectorAll('[data-page]').forEach(b=>b.onclick=e=>{e.stopPropagation();state.localPages[key]=Number(b.dataset.page);draw();scrollToTarget(parent);});footer.querySelector('[data-page-size]').onchange=e=>{e.stopPropagation();state.sizes[key]=Number(e.target.value);state.localPages[key]=1;draw();scrollToTarget(parent);};};draw();
}
document.addEventListener('change',e=>{if(!e.target.matches('[data-page-size]'))return;const key=e.target.dataset.pageSize;state.sizes[key]=Number(e.target.value);if(key==='followups')state.followupPage=1;else if(key==='team-dashboard')state.teamPage=1;else if(key==='team-followups')state.teamFollowupPage=1;else state.page=state.companyPage=state.recommendationPage=state.pushPage=1;loadView({anchor:resultsAnchor()});});

async function renderToday(version){
 await renderOpportunities(version);if(version!==state.renderVersion)return;
 if(!isAdmin())return;
 $('#content .signal-strip')?.remove();
 await pollCollection(false);
}
function openCollectionProgress(){
 if(!isAdmin())return;
 if($('#collection-progress'))return;
 const node=document.createElement('div');node.id='collection-progress';node.className='collection-progress';node.setAttribute('role','status');node.textContent='正在读取采集进度…';
 popup('采集进度',node);pollCollection(false);
}
async function pollCollection(notify=false){
 if(!isAdmin())return;const owner=state.user.id;
 try{
 const p=await api('/api/collection-progress');if(!isAdmin()||state.user.id!==owner)return;state.collection=p;if(state.view==='today')updateCollectionStatus(p);const box=$('#collection-progress');
 if(box){const r=p.run;box.innerHTML=r?`<div class="progress-heading"><strong>${p.active?'正在后台处理':'最近采集'} · ${esc(p.active?p.phase:r.status)}</strong><span>${esc(dateText(r.started_at))}</span></div>${p.total?`<progress max="${p.total}" value="${p.completed}" aria-label="已完成采集来源"></progress><p>已完成 ${p.completed} / ${p.total} 个来源${p.current?' · 当前：'+esc(p.current):''}</p>`:''}<p>已发现 ${r.found} 条 · 新增 ${r.inserted} 条 · 更新 ${r.updated} 条${p.active?'。你可以切换页面，任务继续执行。':''}</p>${r.error?`<p class="error-text">${esc(r.error)}</p>`:''}${!p.active?'<button class="text-button" data-view="today">刷新资讯结果 ↗</button>':''}`:'暂无采集任务。可启动一次采集，或在自动化任务配置中设置计划。';}
 $('#collect').disabled=false;$('#collect').textContent=p.active?'查看采集进度':'手动采集';
 if(p.active&&!state.poll)startCollectionPolling();
 if(!p.active&&state.poll){clearInterval(state.poll);state.poll=null;state.dashboardAt=0;if(notify)toast(`采集${p.run?.status||'结束'}，新增 ${p.run?.inserted||0} 条。`);}
 }catch(e){if($('#collection-progress'))$('#collection-progress').textContent='暂时无法获取进度，后台任务可能仍在执行；稍后自动重试。';}
}
async function collect(){if(!isAdmin())return;if(state.collection?.active){openCollectionProgress();return;}const button=$('#collect');button.disabled=true;button.textContent='正在启动…';try{await api('/api/collect',{method:'POST'});toast('采集已在后台启动，可继续使用其他页面。');startCollectionPolling();openCollectionProgress();await pollCollection();}catch(e){toast(e.message,true);button.disabled=false;button.textContent='手动采集';}}
function startCollectionPolling(){if(!isAdmin()||state.poll)return;state.poll=setInterval(()=>pollCollection(true),3000);}

async function topicData(version){const owner=state.user?.id,session=state.sessionGeneration,admin=isAdmin();const [data,sources]=await Promise.all([api('/api/my-subscriptions'),subscriptionSources(admin)]);if(version!==state.renderVersion||owner!==state.user?.id||session!==state.sessionGeneration||admin!==isAdmin())return null;state.topics=data.items;state.availableSources=sources;return data;}
async function renderTopicConfig(version){await renderPersonalTopics(version);}
async function renderSubscriptionStatus(version){await renderPersonalTopics(version);}
async function renderPersonalTopics(version){
 const data=await topicData(version);if(!data)return;
 $('#content').innerHTML=`<section class="metrics compact-metrics"><div class="metric featured"><span class="metric-label">我的订阅</span><div class="metric-value">${data.total}</div></div><div class="metric"><span class="metric-label">正在运行</span><div class="metric-value">${data.items.filter(t=>t.enabled).length}</div></div><div class="metric"><span class="metric-label">当前账号</span><h2>${esc(state.user.display_name)}</h2><p>${esc(state.user.username)}</p></div></section><div class="section-heading"><h2>订阅运行情况</h2><button id="new-topic" class="primary">＋ 新建订阅</button></div><form id="topic-search" class="filters"><div class="filter-row"><input name="q" class="search-field" aria-label="搜索订阅" placeholder="搜索我的专题或关键词" value="${esc(state.topicQueryDraft||'')}"><button class="secondary">搜索</button></div></form><div id="topic-table"></div><div id="topic-editor"></div>`;
 const render=items=>items.length?`<div class="table-wrap"><table class="subscription-table"><thead><tr><th>订阅专题</th><th>行业 / 关键词</th><th>状态</th><th>累计推送</th><th>操作</th></tr></thead><tbody>${items.map(t=>`<tr><td class="wrap-cell"><strong>${esc(t.name)}</strong><span class="cell-sub">${esc(t.owner_name)}</span></td><td class="wrap-cell">${esc(t.industry||'自定义业务')}<div class="keyword-tags">${t.keywords.slice(0,3).map(k=>tag(k)).join('')}${t.keywords.length>3?tag('另'+(t.keywords.length-3)+'个'):''}</div></td><td>${tag(t.enabled?'已启用':'已停用',t.enabled?'dark':'gold')}</td><td>已推送 ${Number(t.push_count||0)} 条</td><td><div class="table-actions"><button class="text-button" data-topic-info="${t.id}">详情</button><button class="text-button" data-topic-results="${t.id}">查看推送</button><button class="text-button" data-edit-topic="${t.id}">编辑</button><button class="text-button" data-toggle-topic="${t.id}">${t.enabled?'停用':'启用'}</button></div></td></tr>`).join('')}</tbody></table></div>`:empty('暂无订阅','在订阅配置中建立自己的业务专题。');
 const draw=q=>localPager($('#topic-table'),'topics',data.items.filter(t=>[t.name,t.industry,...t.keywords].join(' ').toLowerCase().includes(q.toLowerCase())),render);draw(state.topicQuery||'');
 $('#topic-search').oninput=e=>{if(e.target.name==='q')state.topicQueryDraft=e.target.value;};$('#topic-search').onsubmit=e=>{e.preventDefault();state.topicQuery=state.topicQueryDraft=new FormData(e.target).get('q')||'';state.localPages.topics=1;draw(state.topicQuery);};
 if($('#new-topic'))$('#new-topic').onclick=()=>editTopic();
 $('#topic-table').addEventListener('click',e=>{const results=e.target.closest('[data-topic-results]');if(results)openTopicResults(results.dataset.topicResults);const info=e.target.closest('[data-topic-info]'),edit=e.target.closest('[data-edit-topic]'),toggle=e.target.closest('[data-toggle-topic]');if(edit)editTopic(data.items.find(t=>t.id===Number(edit.dataset.editTopic)));if(info){const t=data.items.find(t=>t.id===Number(info.dataset.topicInfo));openText(t.name,`<p>行业：${esc(t.industry||'不限')}</p><p>业务能力：${esc(t.offering||'未填写')}</p><h3>关注关键词</h3><p>${esc(t.keywords.join('、'))}</p><h3>来源</h3><p>${esc(t.source_ids.length?t.source_ids.map(id=>state.availableSources.find(s=>s.id===id)?.name||id).join('、'):'全部已接入来源')}</p><p>地区：${esc(t.provinces.join('、')||'全国')} · 阶段：${esc(t.stages.join('、')||'全部')}</p><p>排除词：${esc(t.exclude_keywords.join('、')||'无')}</p><p>累计站内推送：${t.push_count} 条</p><button type="button" class="secondary" data-push-diagnosis="${t.id}">查看推送诊断</button><p>${t.enabled?'自动任务会按此主题检查并推送符合条件的资讯。':'此主题已停用，历史推送仍保留。'}</p><p class="muted">${esc(subscriptionRecallNotice)}</p>`);}if(toggle){const context=configurationEditorContext();withButton(toggle,async()=>{const t=data.items.find(t=>t.id===Number(toggle.dataset.toggleTopic));try{await api('/api/subscriptions/'+t.id,{method:'PUT',body:JSON.stringify({...t,enabled:!t.enabled})});if(!configurationViewCurrent(context))return;toast('订阅状态已更新');await refreshConfiguration(context);}catch(error){if(configurationViewCurrent(context))toast(error.message,true);}});}});
}
async function renderPushes(version){
 const data=await api('/api/pushes?'+new URLSearchParams({page:state.pushPage,page_size:pageSize('pushes'),q:state.pushQuery||'',read:state.pushRead||''}));if(version!==state.renderVersion)return;
 $('#content').innerHTML=`<div class="signal-strip"><span><strong>${data.total}</strong> 条站内推送</span><span><strong>${data.unread}</strong> 条未读</span><span>仅当前账号 · 相同公告不重复推送</span></div><form id="push-filters" class="filters"><div class="filter-row"><input class="search-field" name="q" aria-label="搜索推送" placeholder="搜索推送标题" value="${esc(state.pushQuery||'')}"><select name="read" aria-label="阅读状态">${[['','全部'],['unread','未读'],['read','已读']].map(([v,l])=>`<option value="${v}"${v===(state.pushRead||'')?' selected':''}>${l}</option>`).join('')}</select><button class="primary">搜索</button></div></form><div class="table-wrap"><table><thead><tr><th>推送资讯</th><th>命中订阅</th><th>推送时间</th><th>阅读状态</th><th>操作</th></tr></thead><tbody>${data.items.map(x=>`<tr><td class="wrap-cell"><button class="card-title" data-push-open="${x.push_id}">${esc(x.title)}</button><span class="cell-sub">${esc(x.source_name)} · ${esc(x.stage)}</span></td><td class="wrap-cell">${esc(x.matched_topics.slice(0,2).map(t=>t.name).join('、'))}${x.matched_topics.length>2?' 等'+x.matched_topics.length+'个专题':''}<span class="cell-sub clipped-text">${esc(x.topic_reasons.join('；'))}</span><button class="text-button" data-push-evidence="${x.push_id}">推送依据</button></td><td>${dateText(x.pushed_at)}</td><td>${tag(x.read_at?'已读':'未读',x.read_at?'':'blue')}</td><td><button class="text-button" data-push-open="${x.push_id}">查看详情</button></td></tr>`).join('')}</tbody></table></div>${!data.items.length?empty('暂时没有推送','先保存订阅并启用自动推送任务；首次匹配可能包含已有公告，推送时间不等于公告发布时间。'):''}${pagination(data,'pushes')}<p class="muted">此处为已送达的历史记录，保留推送时的匹配理由；最新公告状态请打开详情核实。不会向外部联系人发送消息。</p>`;
 $('#push-filters').onsubmit=e=>{e.preventDefault();const f=new FormData(e.target);state.pushQuery=f.get('q');state.pushRead=f.get('read');state.pushPage=1;loadView();};
 document.querySelectorAll('[data-push-evidence]').forEach(b=>b.onclick=()=>{const x=data.items.find(x=>x.push_id===Number(b.dataset.pushEvidence));openText('本次推送依据',`<h3>${esc(x.title)}</h3><p>推送时间：${dateText(x.pushed_at)}</p><p>命中订阅：${esc(x.matched_topics.map(t=>t.name).join('、'))}</p><p>${esc(x.topic_reasons.join('；'))}</p><p>命中词：${esc(x.matched_keywords.join('、'))}</p>`);});
 document.querySelectorAll('[data-push-open]').forEach(b=>b.onclick=async()=>{const x=data.items.find(x=>x.push_id===Number(b.dataset.pushOpen));try{await api('/api/pushes/'+x.push_id+'/read',{method:'PATCH'});openDetail(x.id);b.closest('tr').querySelector('td:nth-child(4)').innerHTML=tag('已读');}catch(e){toast(e.message,true);}});
}
function editAutomation(value,global=false){
 if(global&&!isAdmin())return;
 const context=configurationEditorContext(),prefix=global?'collection':'personal',form=document.createElement('form');form.className='panel compact-form task-plan-form';form.id=`automation-${prefix}-form`;form.dataset.assistContext=prefix;
 form.innerHTML=`<div class="panel-header"><h2>${global?'网站自动采集任务':'我的订阅推送任务'}</h2></div><p class="muted">${global?'为团队采集已启用的网站，更新公共资讯库。':'检查我的启用订阅，把符合条件的资讯送到推送列表。'}</p>${!value.scheduler_enabled?'<p class="runtime-note">当前定时执行已暂停，保存只更新计划。</p>':''}<div class="schedule-row"><label class="check-label"><input name="enabled" type="checkbox"${value.enabled?' checked':''}>启用任务</label><div class="form-field"><label for="${prefix}-interval">执行间隔（分钟）</label><input id="${prefix}-interval" name="interval_minutes" type="number" min="15" max="10080" value="${Number(value.interval_minutes)}" required></div></div><div class="form-footer"><button class="primary" type="submit">保存计划</button></div>`;
 const dialog=mountEditor(form);dialog.classList.add('task-plan-dialog');
 form.onsubmit=event=>{event.preventDefault();const values=new FormData(form);return saveConfigurationEditor(form,dialog,context,global?'/api/collection-schedule':'/api/automation',{enabled:values.has('enabled'),interval_minutes:Number(values.get('interval_minutes'))},'任务计划已保存');};
}
async function renderAutomation(version){
 const owner=state.user?.id;
 const [task,schedule,runs]=await Promise.all([api('/api/automation'),isAdmin()?api('/api/collection-schedule'):Promise.resolve(null),isAdmin()?api('/api/runs'):Promise.resolve(null)]);if(version!==state.renderVersion||owner!==state.user?.id)return;
 const latest=runs?.items[0];
 const nextCollection=isAdmin()&&schedule.scheduler_enabled&&schedule.enabled?(latest?.started_at?dateText(new Date(new Date(latest.started_at).getTime()+schedule.interval_minutes*60000).toISOString()):'等待首轮计划执行'):'已暂停';
 $('#content').innerHTML=`<section class="panel${!isAdmin()?' personal-task-overview':''}" id="personal-automation"><div class="panel-header"><h2>我的订阅推送任务</h2><button class="secondary" data-task-config="personal">配置任务 ↗</button></div><p>${tag(task.enabled?'已启用':'已停用',task.enabled?'dark':'gold')} · 每 ${task.interval_minutes} 分钟检查我的启用订阅</p><p>最近执行：${dateText(task.last_run)} · 上次新增推送 ${task.last_count} 条</p><p>下次计划：${task.scheduler_enabled&&task.enabled?(task.pending_check?'订阅有更新，后台将立即检查':task.next_run?dateText(task.next_run):'等待首轮计划执行'):'已暂停'}</p>${!task.scheduler_enabled?'<p class="runtime-note">定时执行已暂停。</p>':''}<p class="muted">符合订阅条件的资讯送到当前账号的站内列表，同一公告只推送一次。</p><div class="source-actions"><button class="text-button" data-view="pushes">查看推送列表 ↗</button></div></section>${isAdmin()?`<section class="panel" id="collection-automation"><div class="panel-header"><h2>网站自动采集任务</h2><button class="secondary" data-task-config="collection">配置任务 ↗</button></div><p>${tag(schedule.enabled?'已启用':'已停用',schedule.enabled?'dark':'gold')} · 每 ${schedule.interval_minutes} 分钟采集已启用监测网站</p><p>最近执行：${dateText(latest?.started_at)} · 下次计划：${esc(nextCollection)}</p>${!schedule.scheduler_enabled?'<p class="runtime-note">本地定时执行已暂停，可在今日资讯手动采集。</p>':''}<div class="source-actions"><button class="text-button" data-view="sources">管理监测网站 ↗</button><button class="text-button" data-view="today">查看今日资讯 ↗</button></div></section><section class="panel"><h2>采集运行记录</h2><div id="task-runs"></div></section>`:''}`;
 $('#content').querySelectorAll('[data-task-config]').forEach(button=>button.onclick=()=>{if(version!==state.renderVersion||owner!==state.user?.id)return;const global=button.dataset.taskConfig==='collection';if(global&&!isAdmin())return;editAutomation(global?schedule:task,global);});
 if(isAdmin())localPager($('#task-runs'),'runs',runs.items,items=>items.length?`<div class="table-wrap"><table><thead><tr><th>开始时间</th><th>状态</th><th>发现 / 新增 / 更新</th><th>结束时间</th><th>执行详情</th></tr></thead><tbody>${items.map(r=>`<tr><td>${esc(dateText(r.started_at))}</td><td>${tag(runStatus(r.status),['failed','error','失败','中断'].includes(r.status)?'red':['running','运行中'].includes(r.status)?'blue':'dark')}</td><td>${Number(r.found||0)} / ${Number(r.inserted||0)} / ${Number(r.updated||0)}</td><td>${esc(dateText(r.finished_at))}</td><td class="wrap-cell">${r.error?`<p class="error-text">${esc(r.error)}</p>`:''}${r.details?`<details class="run-details"><summary>查看来源执行结果</summary><pre>${esc(asText(r.details))}</pre></details>`:'暂无附加记录'}</td></tr>`).join('')}</tbody></table></div>`:empty('暂无执行记录','采集执行后，运行记录会集中保存在这里。'));
}
// Source paging is owned by the directory so its active type and filters survive.
// Keep long account lists bounded without replacing their bound event handlers.
const usersBeforePaging=renderUsers;
renderUsers=async version=>{await usersBeforePaging(version);if(version===state.renderVersion&&$('#content table'))paginateNodes($('#content table'),'tbody tr','users');};

function closeAccountMenu(returnFocus=false){
 const menu=$('#account-dropdown');menu.hidden=true;$('#account-menu').setAttribute('aria-expanded','false');if(returnFocus)$('#account-menu').focus();
}
$('#account-menu').onclick=()=>{const menu=$('#account-dropdown'),open=menu.hidden;menu.hidden=!open;$('#account-menu').setAttribute('aria-expanded',String(open));if(open)menu.querySelector('button').focus();};
document.addEventListener('click',e=>{if(!e.target.closest('.account-control')||e.target.closest('#account-dropdown button'))closeAccountMenu();});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('#account-dropdown').hidden){e.preventDefault();closeAccountMenu(true);}});
document.addEventListener('focusin',e=>{if(!e.target.closest('.account-control'))closeAccountMenu();});
