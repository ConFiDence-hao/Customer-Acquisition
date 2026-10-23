const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function environment(){
 const source=fs.readFileSync(path.join(__dirname,'../../app/static/app.js'),'utf8');
 const names=['esc','asText','safeUrl','dateText','shortDate','sourceTime','list'];
 const utilities=source.split(/\r?\n/).filter(line=>names.some(name=>line.startsWith(`const ${name} =`))).join('\n');
 const start=source.indexOf('function detailList(');
 const end=source.indexOf('async function openDetail(',start);
 const nodes={'#drawer-content':{innerHTML:''},'#followup-form':{}};
 const context={
  URL,state:{},$:selector=>nodes[selector]||null,
  isAdmin:()=>true,tag:()=>'',teamFollowupPanel:()=>'',projectAnalysisPanel:()=>'',
  canUseProjectAI:()=>false,isMyTeamFollowup:()=>false,statuses:['待研判'],
  bindTeamFollowup(){},bindProjectAnalysis(){},scheduleProjectPoll(){},syncFollowupAIPermission(){},
 };
 vm.createContext(context);
 vm.runInContext(`'use strict';\n${utilities}\n${source.slice(start,end)}`,context);
 return{context,nodes};
}

test('complete extraction without a warning leaves no incomplete-content notice',()=>{
 const{context}=environment();
 assert.equal(context.extractionCoverageMarkup({content:'完整正文',content_coverage:'complete',pdf_pages_total:3,pdf_pages_read:3,pdf_ocr_pages:0}), '');
});

test('complete OCR extraction shows an identification note without implying missing content',()=>{
 const{context}=environment();
 const html=context.extractionCoverageMarkup({content:'三页完整正文',content_coverage:'complete',extraction_warning:'已通过 OCR 识别，请对照原图核实识别文字。',pdf_pages_total:3,pdf_pages_read:3,pdf_ocr_pages:3});
 assert.match(html,/<strong>采集内容说明<\/strong>/);
 assert.match(html,/已通过 OCR 识别/);
 assert.match(html,/已读取\s*3\s*\/\s*总\s*3\s*页/);
 assert.doesNotMatch(html,/内容采集不完整|仅取得部分内容/);
});

test('nonconsecutive successful PDF pages are a count and show failed pages and the processing limit',()=>{
 const{context}=environment();
 const html=context.extractionCoverageMarkup({content:'第 1 和第 3 页正文',content_coverage:'partial',pdf_pages_total:45,pdf_pages_read:2,pdf_ocr_pages:1,pdf_truncated:true,pdf_page_limit:30,pdf_failed_pages:[2,4]});
 assert.match(html,/已读取\s*2\s*\/\s*总\s*45\s*页/);
 assert.match(html,/已 OCR\s*1\s*页/);
 assert.match(html,/处理范围最多前\s*30\s*页/);
 assert.match(html,/未成功读取页号：2、4/);
 assert.doesNotMatch(html,/前\s*2\s*\/|前\s*2\s*页/);
});

test('partial PDF explains actual page coverage and OCR count with a safe original link',()=>{
 const{context}=environment();
 const item=Object.freeze({content:'已取得部分正文',content_coverage:'partial',extraction_warning:'其余页面未读取',pdf_pages_total:40,pdf_pages_read:3,pdf_ocr_pages:2,pdf_truncated:true,url:'https://example.com/tender.pdf?a=1&b=2'});
 const html=context.extractionCoverageMarkup(item);
 assert.match(html,/内容采集不完整/);
 assert.match(html,/已读取\s*3\s*\/\s*总\s*40\s*页/);
 assert.match(html,/已 OCR\s*2\s*页/);
 assert.match(html,/其余页面未读取/);
 assert.match(html,/href="https:\/\/example.com\/tender.pdf\?a=1&amp;b=2"/);
 assert.match(html,/来源原文/);
 assert.equal(item.pdf_pages_read,3);
});

test('missing PDF counts remain unverified while zero OCR pages remain zero',()=>{
 const{context}=environment();
 const unknown=context.extractionCoverageMarkup({content:'部分正文',content_coverage:'partial',pdf_truncated:true});
 assert.match(unknown,/已读取\s*待核实\s*\/\s*总\s*待核实\s*页/);
 assert.match(unknown,/已 OCR\s*待核实\s*页/);
 const zero=context.extractionCoverageMarkup({content:'部分正文',content_coverage:'partial',pdf_pages_total:12,pdf_pages_read:0,pdf_ocr_pages:0});
 assert.match(zero,/已读取\s*0\s*\/\s*总\s*12\s*页/);
 assert.match(zero,/已 OCR\s*0\s*页/);
});

test('list-only and legacy records retain uncertainty without invented facts or PDF counts',()=>{
 const{context}=environment();
 for(const item of [{content_coverage:'list_only',url:'https://example.com/notice'},{}]){
  const html=context.extractionCoverageMarkup(item);
  assert.match(html,/内容采集不完整/);
  assert.match(html,/仅取得列表线索/);
  assert.match(html,/待核实/);
  assert.doesNotMatch(html,/undefined|null|前\s*0|总\s*0/);
 }
});

test('a warning or truncated PDF is visible even when coverage is marked complete',()=>{
 const{context}=environment();
 assert.match(context.extractionCoverageMarkup({content:'正文',content_coverage:'complete',extraction_warning:'附件待核实'}),/附件待核实/);
 assert.match(context.extractionCoverageMarkup({content:'正文',content_coverage:'complete',pdf_truncated:true}),/采集内容说明/);
});

test('untrusted warnings are escaped and unsafe or missing URLs never become links',()=>{
 const{context}=environment();
 for(const url of ['javascript:alert(1)','data:text/html,unsafe',undefined]){
  const html=context.extractionCoverageMarkup({content_coverage:'partial',extraction_warning:'<img src=x onerror=alert(1)>',url});
  assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html,/原文链接待核实/);
  assert.doesNotMatch(html,/<img|href=|javascript:|data:text/);
 }
});

test('detail places a single coverage notice after its title and identifies rule-generated sections',()=>{
 const{context,nodes}=environment();
 context.renderDetail({id:1,title:'测试采购公告',content_coverage:'list_only',url:'https://example.com/notice',summary:'规则摘要',reasons:['规则命中'],risks:['期限待核实'],evidence:{项目名称:'测试项目'},ai_analysis:{summary:'AI 独立解读',model:'test-model',generated_at:'2026-10-15T02:00:00Z'}});
 const html=nodes['#drawer-content'].innerHTML;
 assert.equal((html.match(/内容采集不完整/g)||[]).length,1);
 assert.ok(html.indexOf('id="detail-title"')<html.indexOf('内容采集不完整'));
 assert.ok(html.indexOf('内容采集不完整')<html.indexOf('class="detail-actions"'));
 assert.doesNotMatch(html,/仅列表线索 · 尚未取得完整正文/);
 assert.match(html,/项目摘要（规则生成）/);
 assert.match(html,/推荐依据（规则评分）/);
 assert.match(html,/风险[^<]*由规则/);
 assert.match(html,/事实证据[^<]*由规则/);
 assert.match(html,/公共 AI 解读/);
 assert.match(html,/AI 独立解读/);
 assert.match(html,/模型 test-model/);
 assert.match(html,/2026/);
 assert.match(html,/采购单位<\/dt><dd>待核实/);
 assert.match(html,/金额 \/ 预算<\/dt><dd>未披露或待核实/);
});
