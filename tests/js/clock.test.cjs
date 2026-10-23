const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function environment(){
 let ticks=100;
 const nodes={'#today':{textContent:''}};
 const context={performance:{now:()=>ticks}, $:selector=>nodes[selector]||null, document:{hidden:false,addEventListener(){}},
  setInterval(){return 1;},clearInterval(){},Date, api:async()=>({state:'synced',writable:true,current_time:'2026-10-14T18:00:00+08:00'})};
 vm.createContext(context);
 const filename=path.join(__dirname,'../../app/static/clock.js');
 if(fs.existsSync(filename))vm.runInContext(fs.readFileSync(filename,'utf8'),context);
 return {context,nodes,advance:ms=>ticks+=ms};
}
test('browser business time advances from server anchor without browser wall clock',()=>{
 const {context,advance}=environment();
 context.installBusinessClock({state:'synced',writable:true,current_time:'2026-10-14T18:00:00+08:00'},100);
 advance(2000);
 assert.equal(context.businessNow().toISOString(),'2026-10-14T10:00:02.000Z');
});
test('unavailable server time never silently falls back to the browser clock',()=>{
 const {context,nodes}=environment();
 assert.equal(context.businessNow(),null);
 context.updateBusinessDateLabel();
 assert.equal(nodes['#today'].textContent,'北京时间校准中');
 assert.throws(()=>context.installBusinessClock({current_time:'invalid'},100));
 assert.equal(context.businessNow(),null);
});
