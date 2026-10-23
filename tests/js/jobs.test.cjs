const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function env(api){
 const sleeps=[];const context={api,performance:{now:()=>0},setTimeout:(fn,ms)=>{sleeps.push(ms);fn();},Error};
 vm.createContext(context);
 const filename=path.join(__dirname,'../../app/static/jobs.js');
 if(fs.existsSync(filename))vm.runInContext(fs.readFileSync(filename,'utf8'),context);
 return {context,sleeps};
}
test('queued AI polls every two seconds and returns the validated result',async()=>{
 const calls=[];let poll=0;
 const {context,sleeps}=env(async(path,options)=>{calls.push(path);return options?{id:9,status:'queued'}:++poll===1?{id:9,status:'running'}:{id:9,status:'succeeded',result:{fields:{name:'控制'}}};});
 const result=await context.runAIJob({kind:'assist',request:{target:'subscription',instruction:'生成控制专题'}});
 assert.equal(result.fields.name,'控制');assert.deepEqual(sleeps,[2000,2000]);assert.deepEqual(calls,['/api/ai-jobs','/api/ai-jobs/9','/api/ai-jobs/9']);
});
test('obsolete context cannot return a late model result',async()=>{
 let current=true;
 const {context}=env(async(path,options)=>{if(options)return{id:9,status:'queued'};current=false;return{id:9,status:'succeeded',result:{private:'old-account'}};});
 assert.equal(await context.runAIJob({kind:'assist'},{current:()=>current}),null);
});
test('expired dated query renews once with a fresh server reference',async()=>{
 let posts=0;const {context}=env(async(path,options)=>options?{id:++posts,status:'queued'}:posts===1?{id:1,status:'stale',valid_until:'2026-10-14T12:00:00+08:00',reference_date:'2026-10-14',server_time:'2026-10-15T00:00:01+08:00'}:{id:2,status:'succeeded',result:{fields:{q:'fresh keyword'}}});
 const result=await context.runAIJob({kind:'assist'},{renewExpired:true});
 assert.equal(result.fields.q,'fresh keyword');assert.equal(posts,2);
});
test('a query that expires again asks to retry instead of endlessly creating requests',async()=>{
 let posts=0;const {context}=env(async(path,options)=>options?{id:++posts,status:'queued'}:{id:posts,status:'stale',valid_until:'2026-10-14T12:00:00+08:00',reference_date:'2026-10-14',server_time:'2026-10-15T00:00:01+08:00'});
 await assert.rejects(context.runAIJob({kind:'assist'},{renewExpired:true}),/重试/);assert.equal(posts,2);
});

test('a successful query that expired in transit is renewed before applying its result',async()=>{
 let posts=0;const {context}=env(async(path,options)=>options?{id:++posts,status:'queued'}:{id:posts,status:'succeeded',valid_until:posts===1?'2026-10-14T12:00:00+08:00':'2026-10-14T12:05:02+08:00',reference_date:'2026-10-14',server_time:'2026-10-14T11:59:59+08:00',result:{fields:{q:posts===1?'obsolete':'fresh'}}});
 context.businessNow=()=>new Date('2026-10-14T12:00:02+08:00');
 assert.equal((await context.runAIJob({kind:'assist'},{renewExpired:true})).fields.q,'fresh');assert.equal(posts,2);
});

test('a repeated completed request reads the authorized cached result before returning',async()=>{
 const calls=[];const {context}=env(async(path,options)=>{calls.push(path);return options?{id:9,status:'succeeded',result:null}:{id:9,status:'succeeded',result:{fields:{q:'cached'}}};});
 assert.equal((await context.runAIJob({kind:'assist'})).fields.q,'cached');
 assert.deepEqual(calls,['/api/ai-jobs','/api/ai-jobs/9']);
});

test('both update callbacks receive server progress including the terminal job',async()=>{
 const updates=[],statuses=[];let poll=0;
 const {context}=env(async(path,options)=>options?{id:9,status:'queued',progress:{stage:'queued',queue_position:2}}:++poll===1?{id:9,status:'running',progress:{stage:'running',elapsed_seconds:2}}:{id:9,status:'succeeded',progress:{stage:'succeeded',elapsed_seconds:4},result:{ok:true}});
 await context.runAIJob({kind:'assist'},{onStatus:job=>statuses.push(job.status),onUpdate:job=>updates.push(job)});
 assert.deepEqual(statuses,['queued','running','succeeded']);
 assert.deepEqual(updates.map(job=>job.status),statuses);assert.equal(updates[2].progress.elapsed_seconds,4);
});
test('progress target receives model stages but front-end completion stays with the caller',async()=>{
 const events=[],progress={stage:value=>events.push(value),update:job=>events.push(job.status),finish:()=>events.push('finished'),stop:()=>events.push('stopped'),fail:()=>events.push('failed')};
 const {context}=env(async()=>({id:9,status:'succeeded',result:{ok:true}}));
 assert.equal((await context.runAIJob({kind:'assist'},{progressTarget:progress})).ok,true);
 assert.deepEqual(events,['submitted','succeeded']);
});
test('terminal failures notify consumers before throwing',async()=>{
 const statuses=[];const {context}=env(async()=>({id:9,status:'waiting_config',error:'等待配置'}));
 await assert.rejects(context.runAIJob({kind:'assist'},{onStatus:job=>statuses.push(job.status)}),/等待配置/);
 assert.deepEqual(statuses,['waiting_config']);
});
