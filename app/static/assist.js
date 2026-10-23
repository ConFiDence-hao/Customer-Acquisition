'use strict';
const assistForms={
 'automation-form':['automation',['interval_minutes']],
 'automation-personal-form':['automation',['interval_minutes']],
 'automation-collection-form':['automation',['interval_minutes']],
 'push-filters':['search_recommendations',['q','terms','term_mode','published_from','published_to','pushed_from','pushed_to','deadline_state','read','sort','subscription_id']],
 'topic-form':['subscription',['name','industry','offering','keywords','exclude_keywords','provinces','stages','source_ids']],
 'source-form':['source',['name','url','kind','adapter','extra_urls','item_selector','link_selector','title_selector','date_selector','content_selector','items_path','title_field','url_field','date_field','content_field','include_keywords','exclude_keywords','max_items','note']],
 'settings-form':['settings',['focus_keywords','exclude_keywords','preferred_provinces','min_score']],
 'model-form':['model',['base_url','model','response_format']],
 'user-form':['user',['username','display_name']],
 'followup-form':['followup',['note']],
 'opportunity-filters':['search_opportunities',['q','terms','term_mode','published_from','published_to','deadline_state','province','stage','category','status','min_score','sort']],
 'followup-filters':['search_followups',['q','terms','term_mode','published_from','published_to','deadline_state','status','sort']],
 'company-filters':['search_companies',['q','province']],
 'recommendation-filters':['search_recommendations',['q','status']],
 'topic-search':['search_subscriptions',['q']],
};
const assistPages={
 'followup-filters':{title:'智能查询 · 我的跟进',description:'按需求查询本人跟进和负责的项目，支持关键词、公告发布日期、截止情况和个人阶段。',placeholder:'例如：查我跟进中且未截止的烟气项目'},
 'opportunity-filters':{
  title:'智能查询 · 今日资讯',description:'直接按你的需求查询公共公告库，可查关键词、地区、日期、截止情况及个人状态。理解后直接展示查询结果。',
  placeholder:'例如：查找江苏的控制系统招标公告，最低60分',
 },
 'company-filters':{
  title:'智能查询 · 企业名录',description:'把企业名称或业务关键词、地区整理为名录查询条件，查询范围是系统已收录的企业。',
  placeholder:'例如：查找江苏与垃圾发电相关的企业',
 },
 'recommendation-filters':{
  title:'智能查询 · 推荐项目',description:'整理推荐项目的关键词和跟进状态，保留你当前选择的订阅专题。',
  placeholder:'例如：查找控制系统相关、正在跟进的推荐项目',
 },
 'topic-search':{
  title:'智能查询 · 订阅列表',description:'整理用于查找现有订阅名称、行业或关键词的简短搜索词。',
  placeholder:'例如：查找与工业控制相关的订阅',
 },
 'push-history':{
  title:'智能查询 · 推送列表',description:'直接查询本账号已收到的资讯，保留当前主题与阅读状态；可区分公告发布日和收到推送日。',
  placeholder:'例如：在推送列表中查找烟气治理相关公告',
 },
 'topic-form':{
  title:'智能填写 · 获客订阅',description:'根据业务能力整理名称、行业、关键词、地区、阶段及可用资讯来源的订阅草稿，先核对再保存。',
  placeholder:'例如：我们提供工业控制系统改造，关注江苏和浙江的招标项目，帮我整理订阅条件',
 },
 'source-form':{
  title:'智能填写 · 监测网站',description:'根据公开网址和读取到的页面整理监测规则草稿。应用后仍需测试抓取，确认结果后再保存启用。',
  placeholder:'例如：监测上面网址的采购公告，帮我识别列表、标题、日期和正文规则',
 },
 'settings-form':{
  title:'智能填写 · 业务关注策略',description:'整理关注词、排除词、优先地区和推荐最低分，应用后由你确认并保存策略。',
  placeholder:'例如：重点关注智慧焚烧和烟气提标，优先江苏，推荐最低60分',
 },
 'model-form':{
  title:'智能填写 · 模型连接',description:'从你提供的服务商资料提取 API 地址、模型名称和输出格式。请手动填写密钥，保存后测试连接。',
  placeholder:'粘贴服务商提供的 API Base URL、模型 ID 和输出格式说明，请勿粘贴密钥',
 },
 'user-form':{
  title:'智能填写 · 团队账号',description:'根据你提供的姓名和命名要求整理账号及显示姓名。编辑已有账号时，仅可填写显示姓名。',
  placeholder:'例如：姓名是张明，账号按姓名拼音填写；密码和角色由我手动设置',
 },
 'followup-form':{
  title:'智能填写 · 跟进计划',description:'根据当前公开公告整理下一步计划和待核实问题。应用前请核对备注草稿，现有个人备注不会发送给模型。',
  placeholder:'例如：根据公告整理下一步要核实的参与条件、截止时间和交付范围，不假设已经联系客户',
 },
 'automation-collection':{
  title:'智能填写 · 自动采集间隔',description:'把你要求的网站采集频率换算为分钟间隔，只填写执行间隔，是否启用由你选择。',
  placeholder:'例如：每3小时采集一次，帮我换算为分钟',
 },
 'automation-personal':{
  title:'智能填写 · 订阅推送间隔',description:'把你要求的站内订阅匹配频率换算为分钟间隔，只填写执行间隔，是否启用由你选择。',
  placeholder:'例如：每2小时检查我的启用订阅，帮我填写执行间隔',
 },
};
const assistSparkIcon='<svg class="assist-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M9 3l1.5 4.5L15 9l-4.5 1.5L9 15l-1.5-4.5L3 9l4.5-1.5L9 3Zm10 9 1 3 3 1-3 1-1 3-1-3-3-1 3-1 1-3Z"/></svg>';
function assistPage(form){
 if(['automation-form','automation-personal-form','automation-collection-form'].includes(form.id))return form.dataset.assistContext==='collection'?'automation-collection':'automation-personal';
 if(form.id==='push-filters')return 'push-history';
 return form.id;
}
function showAssistDraft(panel,title){
 panel.hidden=false;const dialog=popup(title,panel,true);
 dialog.addEventListener('close',()=>{panel.hidden=true;panel._assistProgress?.stop();});
 panel.querySelector('.assist-prompt').focus();
}
function mountAssistEntry(form,entry,isQuery){
 if(isQuery){(form.querySelector('.search-control')||form.querySelector('.filter-row')||form).append(entry);return;}
 form.classList.add('assist-editor-form');
 const header=form.querySelector(':scope > .panel-header');
 if(header){header.append(entry);return;}
 const heading=form.querySelector(':scope > h2, :scope > h3'),bar=document.createElement('div');bar.className='assist-topbar';
 if(heading){heading.before(bar);bar.append(heading,entry);}else{bar.classList.add('assist-topbar-only');bar.append(entry);form.prepend(bar);}
}
function assistControls(form,key){return [...form.elements].filter(x=>x.name===key&&!x.disabled);}
function assistLabel(form,key){const input=assistControls(form,key)[0];return ({source_ids:'资讯来源',stages:'公告阶段',terms:'相关关键词',term_mode:'关键词关系',published_from:'公告发布起日',published_to:'公告发布止日',pushed_from:'收到推送起日',pushed_to:'收到推送止日',deadline_state:'截止条件',subscription_id:'订阅主题',read:'阅读状态',sort:'排序方式'}[key])||input?.labels?.[0]?.textContent.trim()||input?.getAttribute('aria-label')||input?.placeholder||({q:'搜索关键词',province:'地区',stage:'公告阶段',category:'类型',status:'跟进状态',source_ids:'资讯来源',stages:'公告阶段'}[key])||key;}
function assistValue(form,key){const controls=assistControls(form,key);if(controls[0]?.type==='checkbox')return controls.filter(x=>x.checked).map(x=>x.value);return controls[0]?.value??'';}
function setAssistValue(form,key,value){const controls=assistControls(form,key);if(controls[0]?.type==='checkbox'){controls.forEach(x=>x.checked=Array.isArray(value)&&value.includes(x.value));}else if(controls[0]){
  if(controls[0].tagName==='SELECT'&&![...controls[0].options].some(o=>o.value===String(value))){
    const score=typeof value==='number'?value:typeof value==='string'&&/^\d+$/.test(value.trim())?Number(value):NaN;
    if(key!=='min_score'||!Number.isInteger(score)||score<0||score>100)return false;
    const option=document.createElement('option');option.value=String(score);option.textContent=score?`${score} 分以上`:'不限评分';controls[0].add(option);value=score;
  }
  controls[0].value=Array.isArray(value)?key==='terms'?JSON.stringify(value):value.join(key==='extra_urls'?'\n':'，'):value;
} else return false;
controls[0].dispatchEvent(new Event('input',{bubbles:true})); // Invalidate a previous source preview without auto-submitting filters.
if(form.id==='source-form')form.querySelectorAll('[data-mode]').forEach(e=>e.hidden=e.dataset.mode!==form.elements.adapter.value);
controls.forEach(control=>{const details=control.closest('details');if(details)details.open=true;});
return true;}
function captureAssistContext(form){
 const followup=form.id==='followup-form';
 return {form,owner:state.user?.id,session:state.sessionGeneration,render:state.renderVersion,view:state.view,
  news:state.newsTab,config:state.configTab,team:state.teamTab,topic:form.id==='push-filters'?state.pushTopicId:form.id==='recommendation-filters'?state.topicId:null,
  entity:form.dataset.entityId||form.dataset.id||'',request:form._assistRequestNo||0,revision:form._assistFormRevision||0,
  followup,drawer:followup?state.drawerVersion:null,id:followup?state.detail?.id:null,ownerRevision:followup?(state.detail?.team_followup?.owner_revision||0):null,
  dialog:form._assistPanel?.closest('dialog')||form.closest('dialog'),releaseOwn:null};
}
function assistIdentityCurrent(context,form){
 return context.form===form&&form.isConnected&&context.owner===state.user?.id&&context.session===state.sessionGeneration&&
  context.render===state.renderVersion&&context.view===state.view&&context.news===state.newsTab&&context.config===state.configTab&&context.team===state.teamTab&&
  context.entity===(form.dataset.entityId||form.dataset.id||'')&&
  (context.topic===null||context.topic===(form.id==='push-filters'?state.pushTopicId:state.topicId))&&
  (!context.followup||(context.drawer===state.drawerVersion&&context.id===state.detail?.id&&context.ownerRevision===(state.detail?.team_followup?.owner_revision||0)&&canUseProjectAI(state.detail)&&!$('#drawer-backdrop')?.hidden));
}
function assistContextCurrent(context,form){
 return assistIdentityCurrent(context,form)&&context.request===(form._assistRequestNo||0)&&context.revision===(form._assistFormRevision||0)&&
  (!context.dialog||(context.dialog.isConnected&&context.dialog.open));
}
function snapshotAssistFields(form,fields,context){
 if(context.releaseOwn){context.releaseOwn();context.releaseOwn=null;}
 if(!assistContextCurrent(context,form))return null;
 return Object.fromEntries(Object.keys(fields).map(key=>[key,JSON.parse(JSON.stringify(assistValue(form,key)))]));
}
function applyAssistFields(form,fields,context){
 if(context.releaseOwn){context.releaseOwn();context.releaseOwn=null;}
 if(!assistContextCurrent(context,form))return 0;
 const entries=Object.entries(fields),missing=entries.filter(([key])=>!assistControls(form,key).length);
 if(missing.length)throw new Error('当前页面缺少这些填写项，请刷新后重试：'+missing.map(([key])=>assistLabel(form,key)).join('、'));
 form.dataset.assistApplying='true';let count=0;
 try{
  if(form.id!=='followup-form'&&Object.prototype.hasOwnProperty.call(fields,'q')&&!Object.prototype.hasOwnProperty.call(fields,'terms')){
   const terms=form.querySelector('[name=terms]');if(terms)terms.value='';
  }
  for(const [key,value] of entries){if(!setAssistValue(form,key,value))throw new Error('此页面暂不能填入 '+assistLabel(form,key));count++;}
 }finally{delete form.dataset.assistApplying;}
 form._assistFormRevision=(form._assistFormRevision||0)+1;context.revision=form._assistFormRevision;
 return count;
}
function executeAssistQuery(form,context,progress=null){
 if(context.submitted||!assistContextCurrent(context,form))return false;
 context.submitted=true;
 if(progress){if(form.id!=='topic-search')state.assistQueryTransition={context,progress};progress.stage('presenting');}
 else if(context.dialog?.open)context.dialog.close();
 form.requestSubmit();
 if(progress&&form.id==='topic-search'){progress.finish();if(context.dialog?.open)context.dialog.close();if(typeof toast==='function')toast('查询完成，结果已更新。');}
 return true;
}
function attachAssist(form,definition){
 if(form.id==='followup-form'&&!canUseProjectAI(state.detail||{}))return;
 if(form.dataset.assistReady)return;form.dataset.assistReady='true';
 const [target,allKeys]=definition,keys=allKeys.filter(key=>assistControls(form,key).length);if(!keys.length)return;
 const page=assistPage(form),copy=assistPages[page],isQuery=target.startsWith('search_'),labels=Object.fromEntries(keys.map(key=>[key,assistLabel(form,key)]));
 const panel=document.createElement('div');panel.className='assist-panel assist-draft assist-modal-panel';panel.hidden=true;form._assistPanel=panel;
 panel.innerHTML=`<div class="assist-body"><label class="assist-input-label"><span class="assist-input-caption">${isQuery?'查询需求':'填写需求'}</span><textarea class="assist-prompt" rows="3" maxlength="4000" placeholder="${esc(copy.placeholder)}"></textarea></label><div class="assist-actions"><button type="button" class="primary assist-generate">${isQuery?'开始查询':'理解并填写'}</button><button type="button" class="text-button assist-undo" hidden>撤销本次填写</button></div><div class="assist-progress"></div><div class="assist-result" aria-live="polite"></div></div>`;
 form.append(panel);let before=null,after=null,appliedContext=null;
 const changed=event=>{if(!form.dataset.assistApplying&&event.target?.name)form._assistFormRevision=(form._assistFormRevision||0)+1;};
 form.addEventListener('input',changed);form.addEventListener('change',changed);
 panel.addEventListener('input',event=>{if(!form.contains(panel))form._assistFormRevision=(form._assistFormRevision||0)+1;});
 panel.addEventListener('change',event=>{if(!form.contains(panel))form._assistFormRevision=(form._assistFormRevision||0)+1;});
 const entry=document.createElement('button');entry.type='button';entry.className='secondary assist-entry '+(isQuery?'assist-query-entry':'assist-fill-entry');entry.innerHTML=assistSparkIcon+(isQuery?'智能查询':'智能填写');entry.setAttribute('aria-label',copy.title);entry.title=isQuery?'智能查询':'智能填写';
 entry.onclick=()=>{if(target==='followup'&&(!canUseProjectAI(state.detail)||detailActionBusy(state.detail?.id)))return;showAssistDraft(panel,copy.title);};mountAssistEntry(form,entry,isQuery);
 panel.querySelector('.assist-generate').onclick=event=>withButton(event.currentTarget,async()=>{
  if(target==='followup'&&(!canUseProjectAI(state.detail)||detailActionBusy(state.detail?.id)))return;
  const instruction=panel.querySelector('.assist-prompt').value.trim();if(instruction.length<2)throw new Error('请先描述你的需求。');
  const current={};
  keys.filter(key=>key!=='note').forEach(key=>current[key]=assistValue(form,key));
  form._assistRequestNo=(form._assistRequestNo||0)+1;const context=captureAssistContext(form),token={};
  if(target==='followup'){
   const unlock=lockDetailControls(form);state.detailAnalysisBusy=true;state.detailAssistToken=token;
   context.releaseOwn=()=>{if(state.detailAssistToken===token&&context.owner===state.user?.id&&context.drawer===state.drawerVersion){state.detailAnalysisBusy=false;state.detailAssistToken=null;}unlock();};
  }
  const feedback=panel.querySelector('.assist-result');feedback.textContent='';panel.querySelector('.assist-undo').hidden=true;
  const progress=typeof AITaskProgress!=='undefined'?AITaskProgress.mount(panel.querySelector('.assist-progress'),{completeLabel:isQuery?'查询完成':'已填入，尚未保存'}):null;panel._assistProgress=progress;
  try{
   const result=await runAIJob({kind:'assist',request:{target,page,instruction,current,...(target==='followup'?{opportunity_id:context.id,owner_revision:context.ownerRevision}:{})}},
    {current:()=>assistContextCurrent(context,form),renewExpired:isQuery,progressTarget:progress,onStatus:job=>{if(!progress&&assistContextCurrent(context,form))feedback.textContent=job.status==='queued'?'已排队，正在等待 AI 处理…':'AI 正在理解需求…';}});
   if(!result||!assistContextCurrent(context,form)){if(assistIdentityCurrent(context,form)&&context.dialog?.open){feedback.textContent='表单或请求已变化，本次结果未应用；请按新内容重试。';progress?.update({status:'stale',error:feedback.textContent});}return;}
   const fields=Object.fromEntries(Object.entries(result.fields).filter(([key])=>keys.includes(key)));
   if(!Object.keys(fields).length)throw new Error('没有可填入的内容，请补充具体要求。');
   progress?.stage('applying');
   before=snapshotAssistFields(form,fields,context);if(!before)return;
   const count=applyAssistFields(form,fields,context);if(!count)return;
   after=Object.fromEntries(Object.keys(fields).map(key=>[key,JSON.parse(JSON.stringify(assistValue(form,key)))]));appliedContext=context;
   if(isQuery){if(executeAssistQuery(form,context,progress)&&!progress)toast('已理解需求，正在查询。');return;}
   progress?.finish();
   feedback.innerHTML=`<p class="assist-applied"><strong>已填入 ${count} 项</strong> · ${Object.keys(fields).map(key=>esc(labels[key])).join('、')}。请核对后保存。</p>${(result.warnings||[]).map(warning=>`<p class="error-text">${esc(warning)}</p>`).join('')}`;
   panel.querySelector('.assist-undo').hidden=false;toast(`已填入 ${count} 项，请核对后保存。`);
  }catch(error){if(assistContextCurrent(context,form)){progress?.fail(error.message);feedback.textContent=error.message;throw error;}}
  finally{if(context.releaseOwn){context.releaseOwn();context.releaseOwn=null;}}
 });
 panel.querySelector('.assist-undo').onclick=()=>{
  if(!before||!appliedContext||!assistIdentityCurrent(appliedContext,form))return;
  form.dataset.assistApplying='true';let count=0;
  try{for(const [key,value] of Object.entries(before)){if(JSON.stringify(assistValue(form,key))===JSON.stringify(after[key])&&setAssistValue(form,key,value))count++;}}
  finally{delete form.dataset.assistApplying;}
  form._assistFormRevision=(form._assistFormRevision||0)+1;before=null;after=null;panel.querySelector('.assist-undo').hidden=true;
  panel.querySelector('.assist-result').textContent=`已撤销 ${count} 项本次填写；手动修改过的字段已保留。尚未保存。`;toast('已恢复本次填写前的字段，尚未保存。');
 };
}

function scanAssist(){Object.entries(assistForms).forEach(([id,definition])=>{const form=document.getElementById(id);if(form)attachAssist(form,definition);});const password=document.getElementById('password-form');if(password&&!password.dataset.assistReady){password.dataset.assistReady='true';password.insertAdjacentHTML('beforeend','<p class="muted">密码属于保密信息，请本人手动填写，不发送给 AI。</p>');}}
new MutationObserver(scanAssist).observe(document.body,{childList:true,subtree:true});scanAssist();
