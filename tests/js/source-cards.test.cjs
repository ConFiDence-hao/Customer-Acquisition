const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function environment(){
 const context={esc:value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
  tag:(label,tone)=>`<span class="tag ${tone||''}">${label}</span>`,safeUrl:value=>/^https?:\/\//.test(value||'')?value:''};
 vm.createContext(context);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../../app/static/monitoring.js'),'utf8'),context);
 return context;
}

test('compact cards show only name, actual collection state, column and original link',()=>{
 const ctx=environment(),html=ctx.publicSourceCard({id:'bjx',name:'北极星 · 垃圾发电',kind:'行业门户',enabled:true,
  availability:'active',collection_state:'success',url:'https://ljfd.bjx.com.cn/zb/',
  note:'公开检索接口+详情',availability_note:'内置采集器运行参数长说明',last_success:'2026-10-15'});
 assert.match(html,/采集成功/);assert.match(html,/采集逻辑/);assert.match(html,/垃圾发电网.*招标栏目/);
 assert.match(html,/访问来源|列表入口/);
 assert.doesNotMatch(html,/最近成功|网站类型|公开检索接口|内置采集器|运行参数|失败原因/);
});

test('enabled or historical success alone never becomes collection success',()=>{
 const ctx=environment();
 for(const source of [{enabled:true,availability:'active'},{enabled:true,status:'待采集',last_success:'2026-10-01'},
  {enabled:true,collection_state:'pending',availability:'partial',issues:[{code:'ocr_failed'}]}]){
  const html=ctx.publicSourceCard({name:'来源',...source});
  assert.match(html,/待采集/);assert.doesNotMatch(html,/采集成功|失败原因/);
 }
});

test('unadapted, partial and abnormal states all show collection failure',()=>{
 const ctx=environment();
 for(const source of [{availability:'pending',enabled:false},{availability:'authorization',enabled:false},
  {availability:'partial',enabled:true},{availability:'unavailable',status:'异常',enabled:true}]){
  const html=ctx.publicSourceCard({name:'来源',...source});
  assert.match(html,/采集失败/);assert.match(html,/失败原因/);assert.doesNotMatch(html,/采集成功/);
 }
 const partial=ctx.publicSourceCard({name:'来源',availability:'partial',issues:[{code:'text_only'}]});
 assert.match(partial,/部分内容已保留/);assert.match(partial,/图片未采/);
});

test('explicit pause suppresses old errors and uses paused status',()=>{
 const ctx=environment(),html=ctx.publicSourceCard({name:'暂停来源',collection_state:'paused',availability:'paused',
  enabled:false,issues:[{code:'ocr_failed'}],availability_note:'旧失败信息'});
 assert.match(html,/已停用/);assert.doesNotMatch(html,/采集失败|失败原因|OCR识别失败|旧失败信息/);
});

test('column descriptions reflect actual bounded adapters and unadapted entrances',()=>{
 const ctx=environment();
 for(const [id,column] of [['ggzy','首页公开公告'],['ccgp','中央及地方公告'],['gfcl','固废网.*招标栏目'],
  ['cnhw','公开资讯首页'],['wangneng','信息公示'],['bece','招标及非招标采购'],['beeg','采购栏目待核实']]){
  assert.match(ctx.publicSourceCard({id,name:id,availability:'pending'}),new RegExp(column));
 }
 assert.match(ctx.publicSourceCard({id:'wangneng',name:'旺能'}),/非采购/);
 assert.doesNotMatch(ctx.publicSourceCard({id:'beeg',name:'北控'}),/已接入采购|全部公告/);
});

test('custom rules describe configured lists rather than inventing a homepage',()=>{
 const ctx=environment();
 const html=ctx.publicSourceCard({id:'custom_x',custom:true,name:'企业公告',adapter:'html',content_selector:'',
  extra_urls:['https://example.com/list2'],max_items:20,collection_state:'pending'});
 assert.match(html,/已配置列表/);assert.match(html,/1个补充栏目/);assert.match(html,/仅列表/);
 assert.doesNotMatch(html,/首页/);assert.match(html,/HTML/);
 assert.match(ctx.publicSourceCard({id:'custom_public',name:'用户目录来源'}),/管理员配置栏目/);
});

test('administrator uses the same compact card and retains source operations',()=>{
 const ctx=environment();
 const custom=ctx.publicSourceCard({id:'custom_1',name:'自定义',custom:true,enabled:true,max_items:20},true);
 assert.match(custom,/data-edit-source="custom_1"/);assert.match(custom,/data-toggle-source="custom_1"/);
 const planned=ctx.publicSourceCard({id:'grandblue',name:'瀚蓝',adapter_available:false,availability:'pending'},true);
 assert.match(planned,/data-adapt-source="grandblue"/);
 assert.doesNotMatch(ctx.publicSourceCard({id:'custom_1',name:'用户目录',custom:true,enabled:true}),/data-edit-source|data-toggle-source/);
});

test('specific reasons stay concise and preserve IP, login and secondary causes',()=>{
 const ctx=environment(),html=ctx.publicSourceCard({name:'来源',availability:'partial',issues:[
  {code:'ip_security_check'},{code:'login_required'},{code:'text_only'},{code:'pagination_failed'}]});
 assert.match(html,/IP安全认证/);assert.match(html,/需要登录/);assert.match(html,/图片未采/);assert.match(html,/补页未完成/);
 assert.doesNotMatch(html,/需由维护人员|公开检索接口/);
});

test('administrator rendering does not call the verbose legacy source renderer',async()=>{
 const ctx=environment();let mounted;
 ctx.state={renderVersion:1,user:{id:1,role:'admin'}};ctx.isAdmin=()=>true;
 ctx.renderSources=()=>assert.fail('legacy source renderer must not run');
 ctx.renderSourceDirectory=(items,version,admin)=>{mounted={items,version,admin};};
 ctx.api=async endpoint=>({items:endpoint==='/api/sources'?[{id:'bjx',name:'北极星',url:'https://ljfd.bjx.com.cn/zb/',status:'部分可用',last_error:'private-marker'}]:
  [{id:'bjx',availability:'partial',collection_state:'failure',issues:[{code:'text_only'}]}]});
 await ctx.renderMonitoringSources(1);
 assert.equal(mounted.admin,true);assert.equal(mounted.version,1);
 const html=ctx.publicSourceCard(mounted.items[0],true);
 assert.match(html,/采集失败/);assert.doesNotMatch(html,/private-marker/);
});

test('collection explanations distinguish keyword search, list navigation and document reading',()=>{
 const ctx=environment();
 const search=ctx.sourceCollectionProfile({id:'cecep'});
 assert.match(search.method,/POST/);assert.match(search.method,/JSON.*HTML/);assert.match(search.scope,/180天/);assert.match(search.scope,/每词.*3页/);
 const article=ctx.sourceCollectionProfile({id:'bjx'});
 assert.match(article.method,/HTML/);assert.match(article.method,/浏览器/);assert.match(article.method,/网页文字/);assert.match(article.scope,/图片不下载/);
 const pdf=ctx.sourceCollectionProfile({id:'bece'});
 assert.match(pdf.method,/PDF/);assert.match(pdf.method,/OCR/);assert.match(pdf.scope,/前30页/);
});

test('cards separate route and scope into side-by-side regions without score admission claims',()=>{
 const html=environment().publicSourceCard({id:'gfcl',name:'北极星固废',collection_state:'failure',availability:'partial',issues:[{code:'text_only'}]});
 assert.match(html,/class="source-card-body"/);assert.match(html,/class="source-card-main"/);assert.match(html,/class="source-card-side"/);
 assert.match(html,/采集入口/);assert.match(html,/采集逻辑/);assert.match(html,/采集范围/);
 assert.ok(html.indexOf('source-card-side')<html.indexOf('失败原因'));
 assert.doesNotMatch(html,/85分|最低分.*入库|全部历史公告/);
});

test('unadapted sites explain the missing path without describing an active collector',()=>{
 const ctx=environment(),profile=ctx.sourceCollectionProfile({id:'grandblue',availability:'pending'});
 assert.match(profile.method,/未接入/);assert.match(profile.scope,/不自动采集/);
 const custom=ctx.sourceCollectionProfile({id:'custom_1',custom:true,adapter:'html',max_items:20,content_selector:''});
 assert.match(custom.method,/HTML/);assert.match(custom.method,/仅列表/);assert.match(custom.scope,/20条/);
});
test('hybrid HTML and API collectors expose their actual call chain',()=>{
 const ctx=environment();
 const hubei=ctx.sourceCollectionProfile({id:'hubei'});
 assert.match(hubei.method,/首页 HTML.*guid.*POST.*HTML/);
 const capital=ctx.sourceCollectionProfile({id:'bece'});
 assert.match(capital.method,/首页 HTML.*列表 HTML.*公告 HTML.*PDF.*OCR/);
 assert.doesNotMatch(capital.method,/标题筛选/);
});
test('known lists are directly linked and search POST is never offered as a browser page',()=>{
 const ctx=environment();
 const government=ctx.publicSourceCard({id:'ccgp',name:'政府采购',url:'https://www.ccgp.gov.cn/'});
 assert.match(government,/href="https:\/\/www.ccgp.gov.cn\/cggg\/zygg\/"/);
 assert.match(government,/href="https:\/\/www.ccgp.gov.cn\/cggg\/dfgg\/"/);
 const capital=ctx.publicSourceCard({id:'bece',name:'首创',url:'https://ecp.capitaleco-pro.com/'});
 assert.match(capital,/categoryId=2/);assert.match(capital,/categoryId=13/);assert.doesNotMatch(capital,/categoryIds=/);
 const search=ctx.publicSourceCard({id:'cecep',name:'中节能',url:'https://new.ebidding.cecep.cn/'});
 assert.doesNotMatch(search,/href="[^"]*getFullTextDataNew/);
});
