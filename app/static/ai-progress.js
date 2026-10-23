'use strict';
(function(){
 const controllers=new WeakMap(),labels=['已提交','等待处理','AI 处理','应用或呈现结果','完成'];
 const terminal=new Set(['failed','stale','waiting_config','completed']);
 const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
 const finite=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0?value:null;
 const now=()=>typeof performance!=='undefined'?performance.now():Date.now();
 const seconds=value=>{const n=Math.max(0,Math.ceil(value));return n<60?`${n} 秒`:`${Math.floor(n/60)} 分${n%60?` ${n%60} 秒`:''}`;};
 function view(job={},options={}){
  const progress=job.progress||{},raw=progress.stage||job.status||'submitted',stage=raw==='succeeded'?'presenting':raw;
  const elapsed=finite(progress.elapsed_seconds)??0,estimate=finite(progress.estimated_seconds)??options.estimatedSeconds??60;
  return {stage,elapsed,estimate,timingKnown:progress.timing_available!==false,remaining:finite(progress.remaining_seconds)??Math.max(0,estimate-elapsed),queue:finite(progress.queue_position),error:job.error||'',completeLabel:options.completeLabel||'结果已呈现'};
 }
 function phase(stage){return ({submitted:0,queued:1,retry_wait:1,waiting_retry:1,running:2,presenting:3,applying:3,completed:4,waiting_config:1})[stage]??2;}
 function statusText(value){
  if(value.stage==='completed')return value.completeLabel;
  if(value.stage==='waiting_config')return value.error||'等待管理员完善模型配置';
  if(value.stage==='stale')return value.error||'任务已失效，请按当前内容重试';
  if(value.stage==='failed')return value.error||'处理未完成，请重试';
  if(value.stage==='queued'||value.stage==='retry_wait'||value.stage==='waiting_retry')return value.queue===null?'正在等待处理':`正在等待处理 · 队列位置 ${value.queue}`;
  if(value.stage==='running')return 'AI 正在处理';
  if(value.stage==='presenting'||value.stage==='applying')return '正在应用或呈现结果';
  return '请求已提交';
 }
 function timing(value){
  const elapsed=value.timingKnown===false?'耗时未记录':`已用 ${seconds(value.elapsed)}`;
  if(terminal.has(value.stage))return elapsed;
  if(['presenting','applying'].includes(value.stage))return elapsed+' · 正在更新结果';
  return elapsed+(value.remaining<=0?' · 仍在处理中，已超出预估':` · 预计剩余约 ${seconds(value.remaining)}`);
 }
 function render(value){
  const current=phase(value.stage),failed=['failed','stale','waiting_config'].includes(value.stage);
  const nodes=labels.map((label,index)=>{const done=value.stage==='completed'||index<current,active=index===current&&value.stage!=='completed';return `<li class="ai-progress-step${done?' is-done':''}${active?' is-current':''}"><span class="ai-progress-dot" aria-hidden="true">${done?'✓':index+1}</span><span>${label}</span></li>`;}).join('');
  return `<section class="ai-task-progress${failed?' is-error':''}" data-ai-progress data-stage="${escape(value.stage)}" aria-label="AI 任务进度"><ol class="ai-progress-steps">${nodes}</ol><p class="ai-progress-status" role="status">${escape(statusText(value))}</p><p class="ai-progress-time" data-ai-time>${escape(timing(value))}</p><small class="ai-progress-estimate">时间为估算，节点按实际任务状态推进</small></section>`;
 }
 function markup(job={},options={}){return render(view(job,options));}
 function mount(node,options={}){
  if(!node)return null;
  if(controllers.has(node))return controllers.get(node);
  let state=view({},options),updatedAt=now(),timer=null,skeleton='';
  const sample=()=>{const delta=terminal.has(state.stage)?0:Math.max(0,(now()-updatedAt)/1000);return {...state,elapsed:state.elapsed+delta,remaining:Math.max(0,state.remaining-delta)};};
  function paint(){
   if(node.isConnected===false){stop();return;}
   const value=sample(),key=[value.stage,value.queue,value.error,value.remaining<=0].join('|');
   const time=node.querySelector?.('[data-ai-time]');
   if(key!==skeleton||!time){node.innerHTML=render(value);skeleton=key;}else time.textContent=timing(value);
  }
  function stop(){if(timer!==null){clearInterval(timer);timer=null;}}
  function schedule(){if(!terminal.has(state.stage)&&timer===null)timer=setInterval(paint,1000);}
  function update(job){
   const previous=sample(),next=view(job,options),progress=job?.progress||{};
   if(finite(progress.elapsed_seconds)===null)next.elapsed=previous.elapsed;
   if(finite(progress.remaining_seconds)===null)next.remaining=Math.max(0,next.estimate-next.elapsed);
   state=next;updatedAt=now();paint();if(terminal.has(state.stage))stop();else schedule();return controller;
  }
  function stage(next){state=next==='submitted'?view({},options):{...sample(),stage:next};updatedAt=now();paint();if(terminal.has(next))stop();else schedule();return controller;}
  const controller={update,stage,stop,fail(message){state={...sample(),stage:['stale','waiting_config'].includes(state.stage)?state.stage:'failed',error:String(message||'处理未完成，请重试')};updatedAt=now();paint();stop();return controller;},finish(){return stage('completed');}};
  controllers.set(node,controller);paint();schedule();return controller;
 }
 globalThis.AITaskProgress={markup,mount};
})();
