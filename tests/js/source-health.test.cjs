const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function environment(){
 const context={
  esc:value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'),
  tag:(label,tone)=>'<span class="'+(tone||'')+'">'+label+'</span>',
  safeUrl:value=>value,
 };
 vm.createContext(context);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../../app/static/monitoring.js'),'utf8'),context);
 return context;
}

test('source card shows each safe issue instead of losing secondary reading problems',()=>{
 const ctx=environment();
 const html=ctx.publicSourceCard({
  name:'公开来源',kind:'集团采购',availability:'partial',availability_label:'OCR识别失败',
  availability_note:'公开列表仍可使用。',url:'https://example.com/',
  issues:[
   {code:'ocr_failed',label:'OCR识别失败',note:'扫描文字尚未识别。'},
   {code:'pagination_failed',label:'补页未完成',note:'已取得的前页公告仍保留。'},
  ],
 });
 assert.match(html,/图片或扫描件识别失败/);
 assert.match(html,/补页未完成/);
 assert.match(html,/已取得的前页公告仍保留/);
});

test('rendered issue explanations escape markup and never use raw diagnostics',()=>{
 const ctx=environment();
 const html=ctx.publicSourceCard({
  name:'公开来源',availability:'partial',url:'https://example.com/',
  issues:[{code:'unknown',label:'详情缺失',note:'<script>unsafe</script>'}],
  last_error:'private-diagnostic-marker',
 });
 assert.match(html,/&lt;script&gt;unsafe&lt;\/script&gt;/);
 assert.doesNotMatch(html,/<script>|private-diagnostic-marker/);
});

test('directory can filter the secondary cause while keeping broad availability filters',()=>{
 const ctx=environment();
 const source={availability:'partial',issues:[{code:'ocr_failed'},{code:'pagination_failed'}]};
 assert.equal(ctx.sourceMatchesAvailability(source,'issue:pagination_failed'),true);
 assert.equal(ctx.sourceMatchesAvailability(source,'issue:login_required'),false);
 assert.equal(ctx.sourceMatchesAvailability(source,'partial'),true);
 assert.equal(ctx.sourceMatchesAvailability(source,'paused'),false);
 assert.equal(ctx.sourceMatchesAvailability(source,''),true);
});

test('administrator source card replaces the generic badge and raw error with safe public health',async()=>{
 const ctx=environment();let rendered='';
 ctx.state={renderVersion:1,user:{role:'admin'}};
 ctx.isAdmin=()=>true;
 ctx.renderSourceDirectory=(items,_version,admin)=>{rendered=items.map(source=>ctx.publicSourceCard(source,admin)).join('');};
 ctx.api=async endpoint=>({items:endpoint==='/api/sources'?
  [{id:'same',name:'公开来源',enabled:true,adapter_available:true,status:'部分可用',last_error:'private-marker'}]:
  [{id:'same',availability:'partial',availability_label:'OCR识别失败',availability_note:'识别尚未完成。',
    issues:[{code:'ocr_failed',label:'OCR识别失败',note:'请核对原图。'}]}]});
 await ctx.renderMonitoringSources(1);
 assert.match(rendered,/采集失败/);
 assert.match(rendered,/OCR识别失败/);
 assert.match(rendered,/部分内容已保留/);
 assert.doesNotMatch(rendered,/private-marker|部分可用|最近成功/);
});

test('IP security checks have a distinct issue label instead of a generic browser problem',()=>{
 const ctx=environment();
 const html=ctx.sourceIssueMarkup({issues:[{
  code:'ip_security_check',note:'当前采集机访问被网站安全认证拦截。',
 }]});
 assert.match(html,/<strong>IP安全认证<\/strong>/);
 assert.doesNotMatch(html,/读取问题|浏览器验证/);
});
