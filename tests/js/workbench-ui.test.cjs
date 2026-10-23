const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
function env(){
 const context={JSON,URLSearchParams,esc:x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),tag:x=>`<span>${x}</span>`,categories:['智慧焚烧'],provinces:['江苏'],stages:['招标'],statuses:['待研判','跟进中'],state:{defaultMinScore:55},dateText:x=>x||'尚未采集',options:(values,current,label)=>`<option value="">${label}</option>`+values.map(value=>`<option value="${value}"${value===current?' selected':''}>${value}</option>`).join('')};
 vm.createContext(context);
 for(const file of ['querying.js','workbench-ui.js']){const filename=path.join(__dirname,'../../app/static',file);if(fs.existsSync(filename))vm.runInContext(fs.readFileSync(filename,'utf8'),context);}
 return context;
}
test('inline facets preserve date and term fields without a nested disclosure',()=>{
 const html=env().queryFacetFields({terms:'["烟气"]',published_from:'2026-10-01',deadline_state:'open'},false,{inline:true,showTags:false});
 assert.ok(html.includes('name="terms"'));assert.ok(html.includes('value="2026-10-01"'));assert.ok(html.includes('value="open" selected'));
 assert.ok(!html.includes('<details'));assert.ok(!html.includes('当前查询条件'));
});
test('empty keyword values do not create an empty active filter',()=>{
 const context=env();assert.equal(context.noticeFilterChips({q:'',terms:'[]',min_score:'0',sort:'published_at'}),'');
});
test('active filter chips retain independent keys for clearing and escape values',()=>{
 const html=env().noticeFilterChips({category:'智慧焚烧',province:'江苏',terms:'["<script>"]',published_from:'2026-10-01'});
 for(const key of ['category','province','terms','published_from'])assert.ok(html.includes(`data-clear-filter="${key}"`));
 assert.ok(!html.includes('<script>'));assert.ok(html.includes('&lt;script>'));
});
test('all manual notice filters and query fields remain available in one disclosure',()=>{
 const html=env().noticeFilterMarkup({q:'烟气',province:'江苏',min_score:'60',sort:'deadline'});
 for(const key of ['q','category','province','stage','status','min_score','sort','actionable','terms','term_mode','published_from','published_to','deadline_state'])assert.ok(html.includes(`name="${key}"`),key);
 assert.equal((html.match(/<details/g)||[]).length,1);assert.ok(html.includes('应用筛选'));assert.ok(html.includes('value="60" selected'));
});
test('all topic tabs are reachable without expanding a limited first six',()=>{
 const topics=Array.from({length:12},(_,i)=>({id:i+1,name:`主题${i+1}`,push_count:i,enabled:true}));
 const html=env().pushTopicTabs(topics,'12');
 assert.ok(html.includes('data-push-topic="12"'));assert.ok(html.includes('aria-pressed="true"'));assert.ok(!html.includes('展开全部'));
});
test('topic picker searches every topic without changing selected subscription',()=>{
 const context=env(),topics=[{id:1,name:'烟气治理',push_count:3,enabled:true},{id:2,name:'<智慧焚烧>',push_count:4,enabled:false}];
 const html=context.pushTopicChoices(topics,'2','智慧');
 assert.ok(!html.includes('烟气治理'));assert.ok(html.includes('&lt;智慧焚烧>'));assert.ok(html.includes('aria-pressed="true"'));assert.ok(html.includes('已停用'));
});
test('collection status stays a heading disclosure with exact schedule detail',()=>{
 const html=env().collectionStatusMarkup({enabled:true,scheduler_enabled:true,interval_minutes:60},{active:false,run:{started_at:'2026-10-15T11:15:00+08:00',status:'部分完成'}});
 assert.ok(html.startsWith('<details'));assert.ok(html.includes('60 分钟'));assert.ok(html.includes('部分完成'));assert.ok(html.includes('最近采集'));assert.ok(html.includes('任务配置'));
});
