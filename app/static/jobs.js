'use strict';

async function runAIJob(request,{current=()=>true,onStatus=()=>{},onUpdate=()=>{},renewExpired=false,progressTarget=null,completeLabel='结果已呈现'}={}){
 if(!current())return null;
 const progress=progressTarget?.update?progressTarget:progressTarget&&typeof AITaskProgress!=='undefined'?AITaskProgress.mount(progressTarget,{completeLabel}):null;
 const notify=job=>{if(!current())return;progress?.update(job);onStatus(job);onUpdate(job);};
 progress?.stage('submitted');
 try{
 let job=await api('/api/ai-jobs',{method:'POST',body:JSON.stringify(request)});
 const began=performance.now();let renewed=false;
 while(current()){
  notify(job);
  const trustedNow=typeof businessNow==='function'?businessNow():null;
  const currentTime=trustedNow?trustedNow.toISOString():job.server_time;
  const beijingDay=trustedNow?new Date(trustedNow.getTime()+8*3600000).toISOString().slice(0,10):job.server_time?.slice(0,10);
  const expired=job.valid_until&&currentTime&&(Date.parse(currentTime)>=Date.parse(job.valid_until)||beijingDay!==job.reference_date);
  if(expired&&renewExpired){
   if(renewed)throw new Error('查询已过期或日期已变化，重建后仍未完成，请重试。');
   renewed=true;if(!current())return null;job=await api('/api/ai-jobs',{method:'POST',body:JSON.stringify(request)});continue;
  }
  if(expired)throw new Error('查询已过期，请重试。');
  if(job.status==='succeeded'&&job.result==null){job=await api(`/api/ai-jobs/${encodeURIComponent(job.id)}`);continue;}
  if(job.status==='succeeded')return job.result;
  if(['failed','waiting_config','stale'].includes(job.status))throw new Error(job.error||'AI 任务未完成，原有内容已保留。');
  if(performance.now()-began>600000)throw new Error('任务仍在处理，可稍后重新打开查看结果。');
  await new Promise(resolve=>setTimeout(resolve,2000));
  if(!current())return null;
  job=await api(`/api/ai-jobs/${encodeURIComponent(job.id)}`);
 }
 return null;
 }catch(error){if(current())progress?.fail(error.message);throw error;}
 finally{if(!current())progress?.stop();}
}
