const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
function env(){
 let now=0,tick,cleared=0;
 const context={performance:{now:()=>now},setInterval:fn=>{tick=fn;return 1;},clearInterval:()=>cleared++};
 vm.createContext(context);const filename=path.join(__dirname,'../../app/static/ai-progress.js');
 if(fs.existsSync(filename))vm.runInContext(fs.readFileSync(filename,'utf8'),context);
 const node={innerHTML:'',isConnected:true,querySelector(){return null;}};
 return {context,node,advance:ms=>{now+=ms;tick?.();},cleared:()=>cleared};
}
test('queued progress presents actual queue position and five named stages without token percentages',()=>{
 const {context}=env();assert.ok(context.AITaskProgress);
 const html=context.AITaskProgress.markup({status:'queued',progress:{stage:'queued',elapsed_seconds:4,estimated_seconds:40,remaining_seconds:36,queue_position:2,estimated:true}});
 for(const text of ['已提交','等待处理','AI 处理','应用或呈现结果','完成','队列位置 2','36 秒'])assert.ok(html.includes(text),text);
 assert.ok(!/\d+%/.test(html));
});
test('elapsed and estimated remaining update while waiting and overrun is explicit',()=>{
 const {context,node,advance}=env();assert.ok(context.AITaskProgress);
 const controller=context.AITaskProgress.mount(node);
 controller.update({status:'running',progress:{stage:'running',elapsed_seconds:10,estimated_seconds:40,remaining_seconds:30,estimated:true}});
 advance(5000);assert.ok(node.innerHTML.includes('15 秒')&&node.innerHTML.includes('25 秒'));
 advance(30000);assert.ok(node.innerHTML.includes('仍在处理中')&&node.innerHTML.includes('超出预估'));
 controller.stop();
});
test('model success awaits front-end presentation before completing and mount reuses the controller',()=>{
 const {context,node}=env();assert.ok(context.AITaskProgress);
 const controller=context.AITaskProgress.mount(node,{completeLabel:'报告已呈现'});
 assert.equal(context.AITaskProgress.mount(node),controller);
 controller.update({status:'succeeded',result:{}});
 assert.ok(node.innerHTML.includes('data-stage="presenting"'));
 assert.ok(!node.innerHTML.includes('报告已呈现'));
 controller.finish();assert.ok(node.innerHTML.includes('data-stage="completed"')&&node.innerHTML.includes('报告已呈现'));
});
test('waiting configuration and stale tasks stop animation and safely render the error',()=>{
 for(const stage of ['waiting_config','stale']){
  const {context,node,cleared}=env();assert.ok(context.AITaskProgress);
  const controller=context.AITaskProgress.mount(node);controller.update({status:stage,error:'<script>bad</script>'});
  assert.ok(node.innerHTML.includes(`data-stage="${stage}"`)&&node.innerHTML.includes('&lt;script&gt;'));
  assert.ok(cleared()>0);controller.stop();
 }
});
test('detached progress stops its timer without updating a different interface',()=>{
 const {context,node,advance,cleared}=env();assert.ok(context.AITaskProgress);
 context.AITaskProgress.mount(node).update({status:'running'});
 const previous=node.innerHTML;node.isConnected=false;advance(2000);
 assert.equal(node.innerHTML,previous);assert.ok(cleared()>0);
});
test('a new submission resets elapsed fallback after a previous overrun',()=>{
 const {context,node,advance}=env();const controller=context.AITaskProgress.mount(node);
 controller.update({status:'running'});advance(90000);controller.fail('请重试');
 controller.stage('submitted');controller.update({status:'queued'});
 assert.ok(node.innerHTML.includes('已用 0 秒')&&!node.innerHTML.includes('超出预估'));
 controller.stop();
});
test('unknown historical execution time is never presented as a measured zero',()=>{
 const {context}=env();const html=context.AITaskProgress.markup({status:'succeeded',progress:{stage:'succeeded',elapsed_seconds:null,timing_available:false,remaining_seconds:0}});
 assert.ok(html.includes('耗时未记录'));assert.ok(!html.includes('已用 0 秒'));assert.ok(!html.includes('超出预估'));
});
