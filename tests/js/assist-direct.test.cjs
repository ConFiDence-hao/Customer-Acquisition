const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
function env(id='opportunity-filters'){
 const controls=['q','terms','note'].map(name=>({name,value:name==='note'?'original private draft':'',disabled:false,tagName:'INPUT',dispatchEvent(){},closest(){return null;}}));
 const form={id,dataset:{},isConnected:true,elements:controls,_assistRequestNo:1,_assistFormRevision:0,querySelector(selector){return controls.find(node=>selector.includes(node.name))||null;},closest(){return null;},requestSubmit(){this.submits=(this.submits||0)+1;}};
 const context={state:{user:{id:2},sessionGeneration:3,renderVersion:4,drawerVersion:5,view:'today',newsTab:'opportunities',configTab:'subscriptions',pushTopicId:'',detail:{id:8,team_followup:{owner_id:2,owner_revision:1}}},
  document:{body:{},getElementById(){return null;},querySelector(){return null;}},MutationObserver:class{observe(){}},
  $:selector=>selector==='#drawer-backdrop'?{hidden:false}:selector==='#followup-form'?form:null,
  canUseProjectAI:()=>true,Event:class{},setTimeout,clearTimeout,JSON,console,esc:x=>x};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../../app/static/assist.js'),'utf8'),context);
 return{context,form,controls};
}
test('own followup lock is released before controls are automatically filled',()=>{
 const {context,form,controls}=env('followup-form'),note=controls.find(node=>node.name==='note');
 const captured=context.captureAssistContext(form);note.disabled=true;
 captured.releaseOwn=()=>{note.disabled=false;};
 assert.equal(context.applyAssistFields(form,{note:'new AI plan'},captured),1);
 assert.equal(note.value,'new AI plan');assert.equal(note.disabled,false);assert.equal(form.submits,undefined);
});

test('undo snapshot retains the existing private draft after releasing the request lock',()=>{
 const {context,form,controls}=env('followup-form'),note=controls.find(node=>node.name==='note');
 const captured=context.captureAssistContext(form);note.disabled=true;
 captured.releaseOwn=()=>{note.disabled=false;};
 const before=context.snapshotAssistFields(form,{note:'new AI plan'},captured);
 assert.equal(before.note,'original private draft');
 context.applyAssistFields(form,{note:'new AI plan'},captured);
 context.setAssistValue(form,'note',before.note);
 assert.equal(note.value,'original private draft');
});
test('query applies its keyword facet and executes exactly one real form submission',()=>{
 const {context,form,controls}=env();controls.find(node=>node.name==='terms').value='["燃烧"]';
 const captured=context.captureAssistContext(form);
 assert.equal(context.applyAssistFields(form,{q:'消防'},captured),1);
 assert.equal(controls.find(node=>node.name==='terms').value,'');
 assert.equal(context.executeAssistQuery(form,captured),true);assert.equal(form.submits,1);
});
test('AI query remains in presenting until the real list load completes',()=>{
 const {context,form}=env();let closed=false,finished=false;
 const captured=context.captureAssistContext(form);captured.dialog={open:true,isConnected:true,close(){closed=true;}};
 const progress={stage(){},finish(){finished=true;}};
 assert.equal(context.executeAssistQuery(form,captured,progress),true);
 assert.equal(form.submits,1);assert.equal(closed,false);assert.equal(finished,false);
 assert.equal(context.state.assistQueryTransition.progress,progress);
 assert.equal(context.state.assistQueryTransition.context,captured);
});
test('local subscription query completes after its synchronous list draw',()=>{
 const {context,form}=env('topic-search');let closed=false,finished=false;
 const captured=context.captureAssistContext(form);captured.dialog={open:true,isConnected:true,close(){closed=true;}};
 const progress={stage(){},finish(){finished=true;}};
 assert.equal(context.executeAssistQuery(form,captured,progress),true);
 assert.equal(form.submits,1);assert.equal(closed,true);assert.equal(finished,true);
 assert.equal(context.state.assistQueryTransition,undefined);
});
test('600ms delayed result cannot overwrite a manually changed draft',async()=>{
 const {context,form,controls}=env();const captured=context.captureAssistContext(form);
 const delayed=new Promise(resolve=>setTimeout(()=>resolve({q:'old AI keyword'}),600));
 controls[0].value='new manual keyword';form._assistFormRevision++;
 assert.equal(context.applyAssistFields(form,await delayed,captured),0);assert.equal(controls[0].value,'new manual keyword');
});
test('account, view, entity and request changes each invalidate old automatic results',()=>{
 for(const change of [e=>e.context.state.user={id:3},e=>e.context.state.sessionGeneration++,e=>e.context.state.renderVersion++,e=>e.context.state.view='pushes',e=>e.form._assistRequestNo++,e=>e.context.state.drawerVersion++,e=>e.context.state.detail.id=9,e=>e.context.state.detail.team_followup.owner_revision++]){
  const e=env('followup-form'),captured=e.context.captureAssistContext(e.form);change(e);
  assert.equal(e.context.applyAssistFields(e.form,{note:'obsolete private plan'},captured),0);
  assert.equal(e.controls.find(node=>node.name==='note').value,'original private draft');
 }
});

test('query entry mounts beside the visible search control before advanced filter rows',()=>{
 const {context}=env(),mounted=[];
 const search={append:()=>mounted.push('search')},advanced={append:()=>mounted.push('advanced')};
 const form={querySelector:selector=>selector==='.search-control'?search:selector==='.filter-row'?advanced:null,append:()=>mounted.push('form')};
 context.mountAssistEntry(form,{},true);assert.deepEqual(mounted,['search']);
});
