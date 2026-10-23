const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function env(){
 const panel={hidden:false,innerHTML:''};
 const context={state:{user:{id:2},detail:{id:8,team_followup:{owner_id:2,owner_revision:1}}},
  $:selector=>selector==='#project-analysis-panel'?panel:null,isAdmin:()=>false,isMyTeamFollowup:item=>item.team_followup?.owner_id===2,
  esc:x=>String(x).replaceAll('<','&lt;'),tag:x=>x,list:x=>x||[],detailList:x=>JSON.stringify(x),dateText:x=>x,
  setTimeout:()=>1,clearTimeout(){}};
 vm.createContext(context);
 const filename=path.join(__dirname,'../../app/static/project-analysis.js');
 if(fs.existsSync(filename))vm.runInContext(fs.readFileSync(filename,'utf8'),context);
 return{context,panel};
}
test('loss of ownership clears shared report without changing the private draft',()=>{
 const {context,panel}=env();context.state.detail.note='my unsaved note';panel.innerHTML='old private project report';
 context.refreshProjectAnalysisPanel({id:8,team_followup:{owner_id:3,owner_revision:2}});
 assert.equal(panel.innerHTML,'');assert.equal(panel.hidden,true);assert.equal(context.state.detail.note,'my unsaved note');
});
test('failed regeneration keeps prior report visible with explicit old-version marker',()=>{
 const {context}=env();
 const html=context.projectAnalysisContent({project_analysis:{status:'failed',stale:true,error:'safe error',report:{summary:'old valid report',facts:[],questions:[],next_actions:[],topics:[]}}});
 assert.ok(html.includes('old valid report')&&html.includes('旧版')&&html.includes('safe error'));
});
