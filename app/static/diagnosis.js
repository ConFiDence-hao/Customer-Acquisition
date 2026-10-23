'use strict';
const diagnosisReasons={account_disabled:'订阅所属账号已停用',subscription_paused:'此订阅已暂停',automation_paused:'个人推送任务已暂停',scheduler_paused:'系统定时调度已暂停',first_check_pending:'尚未完成首次订阅检查',revision_check_pending:'订阅修改后正在等待重新检查',source_unavailable:'当前选定来源尚无可运行的采集入口',no_matches:'当前公共库没有符合全部条件的资讯',topic_association_pending:'已有资讯通过其他主题送达，等待补充本主题关联'};
function diagnosisCurrent(context){
 return context.owner===state.user?.id&&context.session===state.sessionGeneration&&context.render===state.renderVersion&&context.view===state.view&&context.config===state.configTab&&context.topic===state.pushTopicId&&context.dialog.isConnected&&context.dialog.open;
}
function diagnosisMarkup(data){
 const c=data.counts,a=data.automation;
 return `<h3>${esc(data.topic.name)}</h3><p>${data.reasons.length?data.reasons.map(code=>esc(diagnosisReasons[code]||code)).join('；'):'订阅和任务可运行，当前匹配资讯已关联本主题。'}</p><div class="diagnosis-counts"><span>当前匹配 <strong>${c.matching}</strong></span><span>尚未送达 <strong>${c.undelivered}</strong></span><span>已关联本主题 <strong>${c.delivered_for_topic}</strong></span><span>已由其他主题送达 <strong>${c.delivered_by_other_topics}</strong></span><span>待补充主题关联 <strong>${c.pending_topic_association}</strong></span><span>匹配但不推送 <strong>${c.push_filtered||0}</strong></span></div>${(data.push_filters||[]).some(f=>f.count)?`<p class="muted">不推送原因：${data.push_filters.filter(f=>f.count).map(f=>esc(f.label)+' '+f.count+' 条').join('；')}。这些公告仍可在今日资讯和订阅预览中查看。</p>`:''}<p>上次成功检查：${esc(a.last_run?dateText(a.last_run):'尚未执行')} · 上轮新增推送 ${a.last_count} 条</p><p class="muted">同账号同公告只推送一次，新增为零也可能是已送达。待关联是当前状态，系统没有历史新增关联时间。</p><h3>筛选过程</h3><p class="muted">公共库共 ${data.library_count} 条；按下表顺序逐项筛选。单项去除数量仍保留其他条件，仅作调整预览。</p><div class="table-wrap"><table><thead><tr><th>条件</th><th>筛选后</th><th>本步排除</th><th>仅去掉此条件</th></tr></thead><tbody>${data.funnel.map(step=>`<tr><td>${esc(step.label)}</td><td>${step.remaining}</td><td>${step.removed}</td><td>${step.without_condition}</td></tr>`).join('')}</tbody></table></div><details><summary>来源与任务状态</summary><p>可运行来源 ${data.available_source_count} 个 · 检查间隔 ${a.interval_minutes} 分钟 · 北京时间${data.clock.writable?'可用':'写入暂停'}</p>${data.sources.map(s=>`<p>${esc(s.name)} · ${esc(s.status||'状态待核实')} · ${s.enabled&&s.adapter_available?'采集入口可运行':'采集入口不可运行'}${s.last_success?' · 最近成功 '+esc(dateText(s.last_success)):''}</p>`).join('')}</details>${data.samples.length?`<details><summary>当前匹配样本</summary>${data.samples.map(s=>`<p><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a> · 命中 ${esc(s.matched_keywords.join('、'))}</p>`).join('')}</details>`:''}<p class="muted">${esc(data.notice)}</p><button type="button" class="secondary" data-diagnosis-explain>AI 解释这些统计</button><div class="diagnosis-explanation" aria-live="polite"></div><p class="muted">统计时间：${esc(dateText(data.checked_at))}；AI 只解释统计，配置调整仍需你确认保存。</p>`;
}
async function openPushDiagnosis(topicId){
 const node=document.createElement('div');node.className='dialog-body diagnosis-body';node.textContent='正在核对订阅和推送记录…';
 const dialog=popup('推送诊断',node),context={owner:state.user?.id,session:state.sessionGeneration,render:state.renderVersion,view:state.view,config:state.configTab,topic:state.pushTopicId,dialog};
 try{
  const data=await api(`/api/subscriptions/${encodeURIComponent(topicId)}/diagnosis`);if(!diagnosisCurrent(context))return;
  node.innerHTML=diagnosisMarkup(data);
  node.querySelector('[data-diagnosis-explain]').onclick=event=>withButton(event.currentTarget,async()=>{
   const resultNode=node.querySelector('.diagnosis-explanation');resultNode.innerHTML='<div class="diagnosis-ai-progress"></div><div class="diagnosis-ai-result"></div>';
   const progress=AITaskProgress.mount(resultNode.querySelector('.diagnosis-ai-progress'),{completeLabel:'解释完成'});progress.stage('submitted');
   try{
    // Dedicated authorized endpoint creates the job; only its own result is polled.
    let job=await api(`/api/subscriptions/${encodeURIComponent(topicId)}/diagnosis/explain`,{method:'POST'});const began=performance.now();
    progress.update(job);
    if(job.status==='succeeded'&&job.result==null&&diagnosisCurrent(context))job=await api(`/api/ai-jobs/${job.id}`);
    while(diagnosisCurrent(context)&&!['succeeded','failed','waiting_config','stale'].includes(job.status)){
     if(performance.now()-began>600000)throw new Error('解释仍在处理，稍后可重新查看。');
     await new Promise(resolve=>setTimeout(resolve,2000));if(!diagnosisCurrent(context))return;job=await api(`/api/ai-jobs/${job.id}`);progress.update(job);
    }
    if(!diagnosisCurrent(context)){progress.stop();return;}
    progress.update(job);
    if(job.status!=='succeeded')throw new Error(job.error||'暂时无法生成解释；程序统计仍可使用。');
    progress.stage('presenting');resultNode.querySelector('.diagnosis-ai-result').innerHTML=`<p>${esc(job.result.summary)}</p><ul>${(job.result.suggestions||[]).map(s=>`<li>${esc(s)}</li>`).join('')}</ul>`;progress.finish();
   }catch(error){if(diagnosisCurrent(context)){progress.fail(error.message);resultNode.querySelector('.diagnosis-ai-result').textContent=error.message+'；可继续参考上方程序统计。';}else progress.stop();}
  });
 }catch(error){if(diagnosisCurrent(context))node.textContent='诊断暂不可用：'+error.message;}
}
document.addEventListener('click',event=>{const button=event.target.closest('[data-push-diagnosis]');if(button)openPushDiagnosis(button.dataset.pushDiagnosis);});
