'use strict';

let businessClockAnchor=null,businessClockTimer=null;
function installBusinessClock(data,receivedAt=performance.now()){
 const instant=Date.parse(data.current_time);
 if(!Number.isFinite(instant))throw new Error('未取得可信北京时间');
 businessClockAnchor={instant,tick:receivedAt,state:data.state,writable:data.writable};
 updateBusinessDateLabel();
}
function businessNow(){
 return businessClockAnchor?new Date(businessClockAnchor.instant+Math.max(0,performance.now()-businessClockAnchor.tick)):null;
}
function businessDateText(locale,options){return businessNow()?.toLocaleDateString(locale,{timeZone:'Asia/Shanghai',...options})||'日期待校准';}
function updateBusinessDateLabel(){
 const node=$('#today');if(!node)return;
 node.textContent=businessClockAnchor?businessDateText('zh-CN',{month:'long',day:'numeric',weekday:'long'}):'北京时间校准中';
 node.title=businessClockAnchor?.state==='read_only'?'时间校验失联，业务写入已暂停':businessClockAnchor?.state==='degraded'?'授时暂不可用，沿用最后可信时间':'统一北京时间';
}
async function syncBusinessClock(){
 try{installBusinessClock(await api('/api/clock'));}catch{updateBusinessDateLabel();}
 if(!businessClockTimer)businessClockTimer=setInterval(()=>{updateBusinessDateLabel();if(!document.hidden)syncBusinessClock();},60000);
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)syncBusinessClock();});
