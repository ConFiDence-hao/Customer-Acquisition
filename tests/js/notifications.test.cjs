const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function environment(api=async()=>({unviewed_count:0})){
 const nodes = {'#assignment-badge':{hidden:true,textContent:'',setAttribute(k,v){this[k]=v;}}, '#drawer-backdrop':{hidden:false}, '#followup-assignment-markers':{innerHTML:''}};
 const context = {state:{user:{id:2},sessionGeneration:4,drawerVersion:7,detail:{id:8,team_followup:{owner_id:2,owner_revision:3}}},
  $:s=>nodes[s]||null, document:{hidden:false,addEventListener(){},querySelector(){return null;},querySelectorAll(){return[];}},
  api, esc:x=>String(x), list:x=>x||[], setInterval(){return 1;},clearInterval(){}, setTimeout,clearTimeout,
  isAdmin:()=>false, openDetail(){}, tag:x=>x};
 vm.createContext(context);
 const filename=path.join(__dirname,'../../app/static/notifications.js');
 if(fs.existsSync(filename))vm.runInContext(fs.readFileSync(filename,'utf8'),context);
 return {context,nodes};
}

test('assignment badge displays current unread count and hides zero',()=>{
 const {context,nodes}=environment();
 context.refreshAssignmentBadge({unviewed_count:2});
 assert.equal(nodes['#assignment-badge'].textContent,'2');assert.equal(nodes['#assignment-badge'].hidden,false);
 context.refreshAssignmentBadge({unviewed_count:100});assert.equal(nodes['#assignment-badge'].textContent,'99+');
 context.refreshAssignmentBadge({unviewed_count:0});assert.equal(nodes['#assignment-badge'].hidden,true);
});

test('successful displayed-assignment ack sends exact revision and updates one badge',async()=>{
 let input;
 const {context,nodes}=environment(async(path,options)=>{input={path,body:JSON.parse(options.body)};return {unviewed_count:1};});
 const item={id:8,is_team_owner:true,assignment_source:'admin',assignment_unviewed:true,team_followup:{owner_id:2,owner_revision:3}};
 await context.acknowledgeDisplayedAssignment(item,{owner:2,sessionGeneration:4,drawerVersion:7,id:8,owner_revision:3});
 assert.deepEqual(input,{path:'/api/opportunities/8/assignment-viewed',body:{owner_revision:3}});
 assert.equal(item.assignment_unviewed,false);assert.equal(nodes['#assignment-badge'].textContent,'1');
});

test('late acknowledgement from another drawer or account cannot clear current badge',async()=>{
 let finish;
 const {context,nodes}=environment(()=>new Promise(resolve=>finish=resolve));
 context.refreshAssignmentBadge({unviewed_count:2});
 const item={id:8,is_team_owner:true,assignment_source:'admin',assignment_unviewed:true,team_followup:{owner_id:2,owner_revision:3}};
 const pending=context.acknowledgeDisplayedAssignment(item,{owner:2,sessionGeneration:4,drawerVersion:7,id:8,owner_revision:3});
 context.state.drawerVersion=8;context.state.user={id:3};context.state.sessionGeneration=5;
 finish({unviewed_count:0});await pending;
 assert.equal(nodes['#assignment-badge'].textContent,'2');assert.equal(item.assignment_unviewed,true);
});

test('summary started before a detail ack cannot restore an obsolete red dot',async()=>{
 let finishSummary;
 const {context,nodes}=environment((path)=>{
  if(path==='/api/my-assignments/summary')return new Promise(resolve=>finishSummary=resolve);
  if(path==='/api/notifications')return Promise.resolve({items:[],unread_count:0});
  return Promise.resolve({unviewed_count:0});
 });
 context.state.detail=null;context.refreshAssignmentBadge({unviewed_count:1});
 const poll=context.pollNotifications();
 const item={id:8,is_team_owner:true,assignment_source:'admin',assignment_unviewed:true,team_followup:{owner_id:2,owner_revision:3}};
 context.state.detail=item;
 await context.acknowledgeDisplayedAssignment(item,{owner:2,sessionGeneration:4,drawerVersion:7,id:8,owner_revision:3});
 context.state.detail=null;
 finishSummary({unviewed_count:1});await poll;
 assert.equal(nodes['#assignment-badge'].hidden,true);
});

test('late detail polling cannot restore an older owner revision or overwrite a same-revision mutation',async()=>{
 for(const sameRevision of [false,true]){
  let finish,started;
  const ready=new Promise(resolve=>started=resolve);
  const {context}=environment(async path=>{
   if(path==='/api/my-assignments/summary')return {unviewed_count:0};
   if(path==='/api/notifications')return {items:[],unread_count:0};
   started();return new Promise(resolve=>finish=resolve);
  });
  context.patchTeamFollowupMetadata=()=>{};const item=context.state.detail;
  const poll=context.pollNotifications();await ready;
  item.team_followup={owner_id:sameRevision?2:3,owner_revision:sameRevision?3:4,status:'已联系'};
  context.state.detailMetadataEpoch=(context.state.detailMetadataEpoch||0)+1;
  finish({id:8,team_followup:{owner_id:2,owner_revision:3,status:'跟进中'},is_team_owner:true});await poll;
  assert.equal(item.team_followup.owner_revision,sameRevision?3:4);assert.equal(item.team_followup.status,'已联系');
 }
});

test('a fresh poll still applies a legitimate remote release',async()=>{
 const {context}=environment(async path=>path==='/api/my-assignments/summary'?{unviewed_count:0}:path==='/api/notifications'?{items:[],unread_count:0}:{id:8,team_followup:null,is_team_owner:false});
 context.patchTeamFollowupMetadata=()=>{};await context.pollNotifications();
 assert.equal(context.state.detail.team_followup,null);assert.equal(context.state.detail.is_team_owner,false);
});
