'use strict';

function refreshAssignmentBadge(summary){
 const badge=$('#assignment-badge');if(!badge)return;
 const count=Math.max(0,Number(summary?.unviewed_count)||0);
 badge.hidden=count===0;badge.textContent=count>99?'99+':String(count);
 badge.setAttribute('aria-label',`${count} 个管理员派发项目尚未查看`);
}
function assignmentMarkers(item){
 if(!item.is_team_owner||item.assignment_source!=='admin')return '';
 return `<div class="assignment-markers">${tag('管理员派发','gold')}${item.assignment_unviewed?'<b class="assignment-new">新指派</b>':''}</div>`;
}
function assignmentContextCurrent(context){
 return Number(state.user?.id)===Number(context.owner)&&state.sessionGeneration===context.sessionGeneration&&
  state.drawerVersion===context.drawerVersion&&String(state.detail?.id)===String(context.id)&&
  Number(state.detail?.team_followup?.owner_revision)===Number(context.owner_revision)&&
  !$('#drawer-backdrop')?.hidden;
}
async function acknowledgeDisplayedAssignment(item,context){
 if(!item.is_team_owner||item.assignment_source!=='admin'||!item.assignment_unviewed||!assignmentContextCurrent(context))return;
 try{
  const result=await api(`/api/opportunities/${encodeURIComponent(item.id)}/assignment-viewed`,{method:'PATCH',body:JSON.stringify({owner_revision:context.owner_revision})});
  if(!assignmentContextCurrent(context))return;
  state.assignmentBadgeEpoch=(state.assignmentBadgeEpoch||0)+1;
  item.assignment_unviewed=false;refreshAssignmentBadge(result);
  const markers=$('#followup-assignment-markers');if(markers)markers.innerHTML=assignmentMarkers(item);
  const row=document.querySelector(`[data-followup-id="${Number(item.id)}"] [data-assignment-markers]`);if(row)row.innerHTML=assignmentMarkers(item);
 }catch{/* A stale ownership version stays unread until its current detail is displayed. */}
}
function stopNotificationPolling(){
 clearInterval(state.notificationPoll);state.notificationPoll=null;state.notificationPollBusy=null;
 state.notifications=[];refreshAssignmentBadge({unviewed_count:0});
 const badge=$('#notification-badge');if(badge){badge.hidden=true;badge.textContent='';}
}
function startNotificationPolling(){
 stopNotificationPolling();if(!state.user)return;
 pollNotifications();state.notificationPoll=setInterval(pollNotifications,15000);
}
async function pollNotifications(){
 if(!state.user||document.hidden||state.notificationPollBusy)return;
 const token={},owner=state.user.id,session=state.sessionGeneration,badgeEpoch=state.assignmentBadgeEpoch||0;state.notificationPollBusy=token;
 const current=()=>owner===state.user?.id&&session===state.sessionGeneration&&!document.hidden;
 try{
  const [summary,inbox]=await Promise.all([api('/api/my-assignments/summary'),api('/api/notifications')]);
  if(!current())return;if(badgeEpoch===(state.assignmentBadgeEpoch||0))refreshAssignmentBadge(summary);state.notifications=inbox.items;
  const badge=$('#notification-badge');if(badge){const count=Number(inbox.unread_count||0);badge.hidden=!count;badge.textContent=count>99?'99+':String(count);}
  const item=state.detail,drawerVersion=state.drawerVersion,metadataEpoch=state.detailMetadataEpoch||0;
  if(item&&!$('#drawer-backdrop').hidden){
   const updated=await api(`/api/opportunities/${encodeURIComponent(item.id)}`);
   if(!current()||drawerVersion!==state.drawerVersion||state.detail!==item||metadataEpoch!==(state.detailMetadataEpoch||0))return;
   if(updated.team_followup&&item.team_followup&&Number(updated.team_followup.owner_revision)<Number(item.team_followup.owner_revision))return;
   // Only ownership metadata changes here. Private drafts and assignee selection stay untouched.
   for(const key of ['team_followup','is_team_owner','assignment_source','assignment_unviewed']){
    if(Object.prototype.hasOwnProperty.call(updated,key))item[key]=updated[key];else delete item[key];
   }
   patchTeamFollowupMetadata(item);
   if(typeof refreshProjectAnalysisPanel==='function')refreshProjectAnalysisPanel(updated);
   await acknowledgeDisplayedAssignment(item,{owner,sessionGeneration:session,drawerVersion,id:item.id,owner_revision:item.team_followup?.owner_revision});
  }
  if(current()&&state.view==='followups')await refreshVisibleFollowups(owner,session);
 }catch{/* Keep the last successful badge; authentication errors are handled by api(). */}
 finally{if(state.notificationPollBusy===token)state.notificationPollBusy=null;}
}
async function refreshVisibleFollowups(owner,session){
 const version=state.renderVersion,params=queryToParams({...state.followupFilters,page:state.followupPage,page_size:pageSize('followups')});
 const data=await api('/api/followups?'+params);
 if(owner!==state.user?.id||session!==state.sessionGeneration||version!==state.renderVersion||state.view!=='followups'||document.hidden)return;
 const region=$('#followup-live-results');if(!region)return;
 // No form is reconstructed. Preserve keyboard focus if it is in an existing result row.
 const focused=document.activeElement?.closest?.('[data-followup-id]'),focusId=focused?.dataset.followupId;
 const wanted=new Set(data.items.map(item=>String(item.id)));
 const body=region.querySelector('tbody');
 if(body){
  body.querySelectorAll('[data-followup-id]').forEach(row=>{if(!wanted.has(row.dataset.followupId)&&row!==focused)row.remove();});
  for(const item of data.items){
   const row=body.querySelector(`[data-followup-id="${Number(item.id)}"]`);
   if(row){if(row!==focused)row.innerHTML=followupRowContent(item);else{const markers=row.querySelector('[data-assignment-markers]');if(markers)markers.innerHTML=assignmentMarkers(item);}}
   else body.insertAdjacentHTML('beforeend',followupRow(item));
  }
  if(!data.items.length&&!focusId)region.innerHTML=followupResults(data.items);
 }else region.innerHTML=followupResults(data.items);
 const count=$('#followup-total');if(count)count.textContent=data.total;
 const paging=$('#followup-live-pagination');if(paging&&!paging.contains(document.activeElement))paging.innerHTML=pagination(data,'followups');
}
async function openNotificationInbox(){
 const owner=state.user?.id,session=state.sessionGeneration;
 if(!owner)return;
 try{
  const data=await api('/api/notifications');if(owner!==state.user?.id||session!==state.sessionGeneration)return;
  const labels={assignment:'管理员派发',transferred:'负责人已转交',claimed:'员工领取任务',released:'员工放弃／拒绝任务',stage_changed:'公开阶段更新',report_ready:'项目 AI 研判已完成',report_failed:'项目 AI 研判未完成'};
  openText(isAdmin()?'员工跟进通知':'站内通知',data.items.length?`<div class="notification-list">${data.items.map(item=>`<article><p>${tag(labels[item.kind]||'项目动态')} ${esc(dateText(item.created_at))}${item.read_at?'':' · 未读'}</p><strong>${esc(item.payload?.title||'项目动态')}</strong><p>${esc(item.payload?.owner_name||item.payload?.actor_name||'')}${item.payload?.status?' · '+esc(item.payload.status):''}</p><button type="button" class="text-button" data-notification-open="${Number(item.id)}">打开项目 ↗</button>${item.superseded&&!item.read_at?`<button type="button" class="text-button" data-notification-read="${Number(item.id)}">旧指派 · 标为已读</button>`:''}</article>`).join('')}</div>`:`<p class="muted">${isAdmin()?'暂时没有员工领取或放弃消息。':'暂时没有项目通知。'}</p>`);
  document.querySelectorAll('[data-notification-read]').forEach(button=>button.onclick=()=>withButton(button,async()=>{
   if(owner!==state.user?.id||session!==state.sessionGeneration)return;
   await api(`/api/notifications/${button.dataset.notificationRead}/read`,{method:'PATCH'});
   if(owner===state.user?.id&&session===state.sessionGeneration&&button.isConnected){button.textContent='旧指派已读';button.hidden=true;pollNotifications();}
  }));
  document.querySelectorAll('[data-notification-open]').forEach(button=>button.onclick=async()=>{
   if(owner!==state.user?.id||session!==state.sessionGeneration)return;
   const item=data.items.find(n=>Number(n.id)===Number(button.dataset.notificationOpen));if(!item)return;
   closePopups();await openDetail(item.opportunity_id);
   if((item.kind!=='assignment'||item.superseded)&&owner===state.user?.id&&session===state.sessionGeneration&&String(state.detail?.id)===String(item.opportunity_id)){
    await api(`/api/notifications/${item.id}/read`,{method:'PATCH'});pollNotifications();
   }
  });
 }catch(error){if(owner===state.user?.id&&session===state.sessionGeneration)toast(error.message,true);}
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollNotifications();});
const notificationButton=$('#open-notifications');if(notificationButton)notificationButton.onclick=openNotificationInbox;
