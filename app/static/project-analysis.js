'use strict';

function canUseProjectAI(item){return isAdmin()||isMyTeamFollowup(item);}
function projectStatusLabel(status){return ({none:'尚未生成',queued:'已排队',running:'生成中',succeeded:'已生成',stale:'需更新',failed:'生成失败',waiting_config:'等待模型配置'})[status]||'待核验';}
function projectStatusBadge(item){const status=item.project_ai_status||item.project_analysis?.status;return status?`<span class="cell-sub">项目 AI · ${esc(projectStatusLabel(status))}${item.project_ai_stale?' · 旧版':''}</span>`:'';}
function projectAnalysisContent(item){
 const data=item.project_analysis||{status:'none'},report=data.report,pending=['queued','running'].includes(data.status);
 return `<div class="panel-header"><h3>项目 AI 研判</h3><button type="button" class="secondary" id="generate-project-analysis"${pending?' disabled':''}>${pending?projectStatusLabel(data.status):data.status==='failed'?'重试研判':report?'更新研判':'生成研判'}</button></div><p class="muted">管理员与当前负责人共享 · ${esc(projectStatusLabel(data.status))}${data.stale?' · 当前显示旧版报告，等待更新':''}</p>${data.job_id&&typeof AITaskProgress!=='undefined'?`<div data-project-ai-progress>${AITaskProgress.markup({id:data.job_id,status:data.status,progress:data.progress},{completeLabel:'报告完成'})}</div>`:''}${data.error?`<p class="error-text">${esc(data.error)}；已保留原有报告。</p>`:''}${report?`<p><strong>AI 归纳：</strong>${esc(report.summary||'待核实')}</p><p><strong>采购需求建议：</strong>${esc(report.procurement_need||'待核实')}</p><p><strong>业务契合建议：</strong>${esc(report.fit||'能力待核实')}</p><h4>已验证的原文事实</h4>${list(report.facts).length?`<dl class="evidence-list">${report.facts.map(fact=>`<div><dt>${esc(fact.label)}</dt><dd>${esc(fact.value)}<blockquote>${esc(fact.evidence)}</blockquote>${safeUrl(fact.url)?`<a href="${esc(safeUrl(fact.url))}" target="_blank" rel="noopener noreferrer">核对原文 ↗</a>`:''}</dd></div>`).join('')}</dl>`:'<p class="muted">未取得可验证的补充事实，请查阅来源原文。</p>'}<p class="muted">${esc(report.evidence_limit||'')}</p><h4>风险与待核实项</h4>${detailList(report.questions,'暂无补充问题，仍需核实参与条件。')}<h4>下一步计划</h4>${detailList(report.next_actions,'先核实项目最新阶段与参与资格。')}<p class="muted">采用主题：${report.topics?.length?report.topics.map(topic=>esc(topic.name)).join('、'):'无相关业务配置，能力待补充'} · ${esc(dateText(report.generated_at))}</p>`:'<p class="muted">开始团队跟进或管理员派发后，系统自动整理项目事实、业务建议和下一步计划。</p>'}<p class="muted">手写备注仅本人可见，AI 不会替你修改负责人或跟进阶段。</p>`;
}
function projectAnalysisPanel(item){return `<section id="project-analysis-panel" class="panel"${canUseProjectAI(item)?'':' hidden'}>${canUseProjectAI(item)?projectAnalysisContent(item):''}</section>`;}
function refreshProjectAnalysisPanel(item){
 const panel=$('#project-analysis-panel');if(!panel)return;
 clearTimeout(state.projectAnalysisPoll);
 if(!canUseProjectAI(item)){panel.hidden=true;panel.innerHTML='';if(state.detail)delete state.detail.project_analysis;syncFollowupAIPermission();return;}
 if(state.detail){state.detailMetadataEpoch=(state.detailMetadataEpoch||0)+1;state.detail.project_analysis=item.project_analysis;}
 const html=projectAnalysisContent(item);panel.hidden=false;if(panel.innerHTML!==html)panel.innerHTML=html;
 bindProjectAnalysis();syncFollowupAIPermission();scheduleProjectPoll();
}
function syncFollowupAIPermission(){
 if(typeof document==='undefined')return;
 const form=$('#followup-form'),allowed=canUseProjectAI(state.detail||{});
 const entry=form?.querySelector('.assist-entry');if(entry)entry.hidden=!allowed;
 if(!allowed)form?.querySelectorAll('.assist-panel').forEach(panel=>panel.hidden=true);
}
function projectContext(){return {owner:state.user?.id,session:state.sessionGeneration,drawer:state.drawerVersion,id:state.detail?.id,revision:state.detail?.team_followup?.owner_revision||0};}
function projectContextCurrent(context){return context.owner===state.user?.id&&context.session===state.sessionGeneration&&context.drawer===state.drawerVersion&&context.id===state.detail?.id&&context.revision===(state.detail?.team_followup?.owner_revision||0)&&!$('#drawer-backdrop')?.hidden&&canUseProjectAI(state.detail);}
function scheduleProjectPoll(){
 clearTimeout(state.projectAnalysisPoll);
 if(!['queued','running'].includes(state.detail?.project_analysis?.status))return;
 const context=projectContext();
 state.projectAnalysisPoll=setTimeout(async()=>{
  if(!projectContextCurrent(context)||document.hidden)return;
  try{const data=await api(`/api/opportunities/${context.id}/project-analysis`);if(projectContextCurrent(context))refreshProjectAnalysisPanel({...state.detail,project_analysis:data});}
  catch(error){if(projectContextCurrent(context)){const panel=$('#project-analysis-panel');if(panel)panel.querySelector('.muted').textContent=error.message;}}
 },2000);
}
function bindProjectAnalysis(){
 const progressNode=$('[data-project-ai-progress]'),data=state.detail?.project_analysis;
 if(progressNode&&data&&typeof AITaskProgress!=='undefined'){const progress=AITaskProgress.mount(progressNode,{completeLabel:'报告完成'});progress.update({id:data.job_id,status:data.status,progress:data.progress});if(data.status==='succeeded'&&data.report)progress.finish();}
 const button=$('#generate-project-analysis');if(!button)return;
 button.onclick=()=>withButton(button,async()=>{
  const context=projectContext();if(!projectContextCurrent(context))return;
  const data=state.detail.project_analysis,job=data?.status==='failed'&&data.job_id?
   await api(`/api/ai-jobs/${data.job_id}/retry`,{method:'POST'}):await api(`/api/opportunities/${context.id}/project-analysis`,{method:'POST'});
  if(!projectContextCurrent(context))return;
  refreshProjectAnalysisPanel({...state.detail,project_analysis:{...data,status:job.status,job_id:job.id,progress:job.progress}});
 });
}
