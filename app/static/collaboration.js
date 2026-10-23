'use strict';

function isMyTeamFollowup(item){return !!item.team_followup&&Number(item.team_followup.owner_id)===Number(state.user?.id);}
function detailActionBusy(id){return state.detailAnalysisBusy||!!state.detailSavePromise||!!pendingFollowupSave(state.user?.id,id);}
function teamFollowupBadge(item){
 const team=item.team_followup;
 if(!team)return '<span class="cell-sub team-followup-marker">团队未领取</span>'+projectStatusBadge(item);
 return `<div class="team-followup-badge"><span>团队负责人：${esc(team.owner_name||'名称待核实')}${isMyTeamFollowup(item)?'（我）':''}${team.owner_enabled===false?'（账号已停用）':''}</span>${tag(team.status||'跟进中','dark')}${projectStatusBadge(item)}</div>`;
}
function personalFollowupBadge(item){
 const status=item.status||'待研判',tone=['跟进中','已联系'].includes(status)?'dark':['已排除','已归档'].includes(status)?'gold':'';
 return `${tag(status,tone)}<span class="cell-sub followup-marker">${isMyTeamFollowup(item)?'本人负责 · 公开阶段':item.has_followup?'个人研判 · 仅自己':'尚未保存个人研判'}</span>`;
}
function teamFollowupContent(item){
 const team=item.team_followup,mine=isMyTeamFollowup(item),mayRelease=team&&(mine||isAdmin());
 return `<h3>团队跟进</h3><p id="team-owner-line">${teamOwnerLine(item)}</p><p id="team-owner-times" class="muted"${team?'':' hidden'}>${team?`开始 ${esc(dateText(team.claimed_at))} · 更新 ${esc(dateText(team.updated_at))}`:''}</p><div id="followup-assignment-markers">${assignmentMarkers(item)}</div><p class="team-privacy-note">主动开始后，团队可查看负责人姓名与公开阶段；备注仅自己可见。浏览、AI 解读或保存个人研判不会自动领取。</p><div class="detail-actions"><button type="button" class="primary" data-team-action="claim"${team?' hidden':''}>开始跟进</button><button type="button" class="secondary" data-team-action="release"${mayRelease?'':' hidden'}>放弃跟进</button><span id="team-nonowner-hint" class="muted"${team&&!mayRelease?'':' hidden'}>已有负责人，需要转交时联系管理员。</span></div>${isAdmin()?`<div class="team-assignment"><label for="team-assignee">${team?'转交给':'指派给'}</label><select id="team-assignee" aria-label="选择接手账号" disabled><option value="">正在读取可接手账号…</option></select><button type="button" class="secondary" data-team-action="transfer" disabled>${team?'确认转交':'确认指派'}</button><small>指派进入接手人的我的跟进，转交保留团队阶段；私人备注不共享。</small></div>`:''}<span id="team-followup-feedback" class="result-note" role="status"></span>`;
}
function teamOwnerLine(item){const team=item.team_followup;return team?`已跟进 · 负责人：<strong>${esc(team.owner_name||'名称待核实')}${isMyTeamFollowup(item)?'（我）':''}</strong>${team.owner_enabled===false?' · 账号已停用':''} ${tag(team.status||'跟进中','dark')}`:'此项目尚无团队负责人。';}
function patchTeamFollowupMetadata(item){
 const panel=$('#team-followup-panel');if(!panel)return;
 const team=item.team_followup,mayRelease=team&&(isMyTeamFollowup(item)||isAdmin());
 const line=$('#team-owner-line');if(line)line.innerHTML=teamOwnerLine(item);
 const times=$('#team-owner-times');if(times){times.hidden=!team;times.textContent=team?`开始 ${dateText(team.claimed_at)} · 更新 ${dateText(team.updated_at)}`:'';}
 const markers=$('#followup-assignment-markers');if(markers)markers.innerHTML=assignmentMarkers(item);
 const claim=panel.querySelector('[data-team-action="claim"]'),release=panel.querySelector('[data-team-action="release"]');
 if(claim)claim.hidden=!!team;if(release)release.hidden=!mayRelease;
 const hint=$('#team-nonowner-hint');if(hint)hint.hidden=!team||!!mayRelease;
 const label=panel.querySelector('label[for="team-assignee"]'),transfer=panel.querySelector('[data-team-action="transfer"]');
 if(label)label.textContent=team?'转交给':'指派给';if(transfer)transfer.textContent=team?'确认转交':'确认指派';
 updateFollowupPrivacy(item);syncFollowupAIPermission();
}
function teamFollowupPanel(item){return `<section id="team-followup-panel" class="panel team-followup-panel">${teamFollowupContent(item)}</section>`;}
function updateFollowupPrivacy(item){
 const label=$('#followup-status-label'),notice=$('#followup-privacy-note');
 if(label)label.textContent=isMyTeamFollowup(item)?'我的公开跟进阶段（团队可见）':'我的个人研判状态（仅自己）';
 if(notice)notice.textContent=isMyTeamFollowup(item)?'你是当前负责人，保存状态会更新团队公开阶段；备注仍仅自己可见。':'个人状态与备注仅自己可见，保存不会自动成为团队负责人。';
}
function refreshTeamFollowupPanel(item){const panel=$('#team-followup-panel');if(!panel)return;panel.innerHTML=teamFollowupContent(item);updateFollowupPrivacy(item);bindTeamFollowup(item);}
function applyFollowupFields(item,result){state.detailMetadataEpoch=(state.detailMetadataEpoch||0)+1;for(const key of ['team_followup','is_team_owner','status','has_followup','followup_updated_at','note','assignment_source','assignment_unviewed']){if(Object.prototype.hasOwnProperty.call(result,key))item[key]=result[key];else if(key.startsWith('assignment_'))delete item[key];}}
function updateTeamAssignmentControls(){
 const panel=$('#team-followup-panel'),select=$('#team-assignee'),button=panel?.querySelector('[data-team-action="transfer"]');if(!select||!button)return;
 const available=select.dataset.assigneesLoaded==='true'&&select.dataset.assigneesAvailable==='true',busy=detailActionBusy(state.detail?.id);
 select.disabled=!available||busy;button.disabled=!available||busy||!select.value;
}
function bindTeamFollowup(item){
 const panel=$('#team-followup-panel');if(!panel)return;
 const owner=state.user?.id,version=state.drawerVersion;
 const current=()=>owner===state.user?.id&&version===state.drawerVersion&&String(state.detail?.id)===String(item.id)&&panel.isConnected&&$('#team-followup-panel')===panel&&!$('#drawer-backdrop').hidden;
 panel.querySelectorAll('[data-team-action]').forEach(button=>button.onclick=()=>runTeamFollowupAction(button.dataset.teamAction,item,$('#team-assignee')?.value));
 if(!isAdmin())return;
 api('/api/users').then(data=>{
  if(!current()||!isAdmin())return;
  const users=list(data.items).filter(user=>user.enabled).map(user=>({id:user.id,name:user.display_name||user.username})),select=$('#team-assignee'),button=panel.querySelector('[data-team-action="transfer"]');
  select.innerHTML=`<option value="">${users.length?'选择启用账号':'暂无可接手账号'}</option>${users.map(user=>`<option value="${Number(user.id)}">${esc(user.name)}</option>`).join('')}`;select.dataset.assigneesLoaded='true';select.dataset.assigneesAvailable=String(users.length>0);
  select.onchange=updateTeamAssignmentControls;updateTeamAssignmentControls();
 }).catch(error=>{if(current()){$('#team-assignee').innerHTML='<option value="">账号名单暂未能读取</option>';const feedback=$('#team-followup-feedback');if(feedback)feedback.textContent=error.message;}});
}
async function runTeamFollowupAction(action,item,targetId){
 if(!state.user||detailActionBusy(item.id))return;
 const mine=isMyTeamFollowup(item);
 if(action==='claim'&&item.team_followup&&!mine)return;
 if(action==='release'&&(!item.team_followup||(!mine&&!isAdmin())))return;
 if(action==='transfer'&&(!isAdmin()||!Number.isInteger(Number(targetId))||Number(targetId)<=0))return;
 if(!['claim','release','transfer'].includes(action))return;
 const owner=state.user.id,version=state.drawerVersion,id=item.id,form=$('#followup-form'),draftStatus=$('#followup-status')?.value,draftNote=$('#followup-note')?.value,statusEdited=draftStatus!=null&&draftStatus!==(item.status||'待研判');
 if(!form)return;
 const current=()=>owner===state.user?.id&&version===state.drawerVersion&&String(state.detail?.id)===String(id)&&form.isConnected&&$('#followup-form')===form&&!$('#drawer-backdrop').hidden;
 if(!current())return;
 const unlock=lockDetailControls(form),path=`/api/opportunities/${encodeURIComponent(id)}/${action==='transfer'?'assignee':'claim'}`,options={method:action==='claim'?'POST':action==='release'?'DELETE':'PUT'};
 if(action==='transfer')options.body=JSON.stringify({user_id:Number(targetId)});
 const request=api(path,options);state.detailSavePromise=request;trackFollowupSave(owner,id,request);
 try{
  const result=await request;if(!current()||String(result.id)!==String(id))return;
  applyFollowupFields(item,result);state.detailSaved=true;refreshTeamFollowupPanel(item);
  if(canUseProjectAI(item)){const reportContext=projectContext();api(`/api/opportunities/${id}/project-analysis`).then(report=>{if(current()&&projectContextCurrent(reportContext))refreshProjectAnalysisPanel({...item,project_analysis:report});}).catch(()=>{});}
  else refreshProjectAnalysisPanel(item);
  if(!statusEdited&&result.status!=null)$('#followup-status').value=result.status;
  if(draftNote!=null)$('#followup-note').value=draftNote;
  const assignedMessage=`已${action==='transfer'?'派发给 '+(result.team_followup?.owner_name||'接手人'):''}，已进入其我的跟进`;
  const feedback=$('#team-followup-feedback');if(feedback)feedback.textContent=action==='claim'?'已开始跟进':action==='release'?'已放弃跟进':assignedMessage;
  toast(action==='claim'?'已开始团队跟进，个人备注仅自己可见。':action==='release'?'已放弃团队跟进，个人研判记录仍保留。':assignedMessage);pollNotifications();
 }catch(error){
  if(!current())return;toast(error.message,true);
  // A concurrent claim can change the public owner; refresh only public metadata.
  try{const updated=await api(`/api/opportunities/${encodeURIComponent(id)}`);if(current()&&String(updated.id)===String(id)){applyFollowupFields(item,updated);refreshTeamFollowupPanel(item);if(!statusEdited)$('#followup-status').value=updated.status||'待研判';if(draftNote!=null)$('#followup-note').value=draftNote;}}catch{/* Keep the draft and let the user retry. */}
 }finally{if(state.detailSavePromise===request)state.detailSavePromise=null;unlock();}
}
