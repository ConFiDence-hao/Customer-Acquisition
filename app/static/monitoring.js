'use strict';
const publicSourceKinds=['集团采购','公共资源','行业门户','商业聚合','前期公示'];
const publicSourceAvailability={
  active:{label:'已启用采集',tone:'dark'},
  partial:{label:'读取有问题',tone:'gold'},
  unavailable:{label:'暂不可采集',tone:'red'},
  paused:{label:'已停用采集',tone:'gold'},
  pending:{label:'尚未接入',tone:''},
  authorization:{label:'待授权接入',tone:'gold'},
};
const sourceIssueLabels={
  text_only:'已保留网页文字',content_partial:'已采集部分内容',pdf_truncated:'PDF已按上限截断',
  ocr_image_blocked:'原图禁止自动读取',
  ip_security_check:'IP安全认证',
  browser_verification:'浏览器验证',login_required:'需要登录',browser_unavailable:'浏览器组件未就绪',
  browser_budget_exhausted:'浏览器页数已达上限',ocr_unavailable:'OCR组件未就绪',
  browser_requests_exhausted:'浏览器请求已达上限',
  ocr_failed:'OCR识别失败',image_body:'正文为图片',detail_missing:'详情正文未取得',
  pagination_failed:'补页未完成',access_restricted:'网站限制访问',network_failed:'网站连接失败',
  read_incomplete:'读取未完成',
};
const sourceCardColumns={
  cecep:'采购公告关键词检索 · 回看180天',
  everbright:'首页及公开采购栏目 · 最多20页',
  bjx:'垃圾发电网招标栏目 · 最多5页',
  gfcl:'固废网招标栏目 · 最多5页',
  cseg:'官网项目公告 · 最多5页',
  ggzy:'首页公开公告',
  ccgp:'中央及地方公告 · 各最多3页',
  hubei:'首页工程建设招标公告',
  datong:'首页、工程建设及政府采购栏目',
  hbzhan:'公开招投标专题 · 最多5页',
  solidwaste:'公开资讯首页及项目栏目',
  cnhw:'公开资讯首页',
  wangneng:'官网信息公示 · 最多4页（前期线索，非采购）',
  bece:'首页、招标及非招标采购栏目',
  bidcenter:'公开邀请招标栏目 · 最多8页',
  bidi:'招标预告、招标公告及采购意向',
  grandblue:'采购平台入口 · 公告栏目未接入',
  sjec:'供应商平台入口 · 公告栏目未接入',
  sinocycle:'官网入口 · 采购栏目待核实',
  dynagreen:'采购平台入口 · 公告栏目未接入',
  junxin:'官网通知公告 · 采购栏目待核实',
  beeg:'集团环境业务介绍 · 采购栏目待核实',
  cebpub:'网站入口 · 公告栏目未接入',
  jilin:'网站入口 · 公告栏目未接入',
  'bjx-environment':'环保总站入口 · 栏目未接入',
  jianyu:'网站入口 · 公开公告栏目未接入',
  qianlima:'网站入口 · 公开公告栏目未接入',
  chinabidding:'网站入口 · 公开公告栏目未接入',
};
const sourceCardIssueNotes={
  text_only:'正文只取得网页文字，图片未采。',
  content_partial:'正文仅取得部分内容，完整信息请查看原文。',
  pdf_truncated:'已保留完成的PDF页面，其余请查看原附件。',
  ocr_image_blocked:'原图禁止自动读取，图片内容请查看原文。',
  ip_security_check:'网站要求IP安全认证，当前无法读取正文。',
  browser_verification:'网站要求访问验证，当前无法完整读取。',
  login_required:'公告或附件需要登录，尚未读取。',
  browser_unavailable:'网页读取组件尚未就绪。',
  browser_budget_exhausted:'本轮网页读取达到页数上限。',
  browser_requests_exhausted:'本轮网页读取达到请求上限。',
  ocr_unavailable:'文字识别组件尚未就绪。',
  ocr_failed:'图片或扫描件识别失败，请核对原文。',
  image_body:'正文为图片或扫描件，尚未取得可读文字。',
  detail_missing:'未取得详情正文，当前保留列表线索。',
  pagination_failed:'补页未完成，已取得的前页公告仍保留。',
  access_restricted:'网站限制自动访问。',
  network_failed:'网站连接失败或超时。',
  read_incomplete:'本次采集未完成，请核查来源。',
};
const sourceCardPendingReasons={
  grandblue:'采购平台拒绝自动访问，公告栏目未接入。',
  sjec:'公告查询需要登录，未接入自动采集。',
  sinocycle:'尚未核实公开采购公告栏目。',
  dynagreen:'采购平台连接超时，公告栏目未接入。',
  junxin:'尚未发现公开采购公告栏目。',
  beeg:'目前仅核实集团业务介绍，采购栏目未适配。',
  cebpub:'网站禁止自动读取，未接入采集。',
  jilin:'公告查询受访问限制，栏目未接入。',
  'bjx-environment':'公开页面要求访问验证，栏目未接入。',
  jianyu:'公开公告栏目尚未适配，部分内容需要登录。',
  qianlima:'完整公告有会员限制，公开栏目尚未适配。',
  chinabidding:'网站限制公开访问，栏目尚未适配。',
};
function sourceCollectionStatus(source){
  let key=source.collection_state;
  if(!['success','failure','pending','paused'].includes(key)){
    if(source.availability==='paused'||source.enabled===false&&source.adapter_available||['已停用','管理员停用'].includes(source.status))key='paused';
    else if(source.status==='待采集')key='pending';
    else if(['partial','unavailable','pending','authorization'].includes(source.availability)||['部分可用','异常','未接入'].includes(source.status))key='failure';
    else if(source.status==='正常')key='success';
    else key='pending';
  }
  return {key,...({success:{label:'采集成功',tone:'dark'},failure:{label:'采集失败',tone:'red'},pending:{label:'待采集',tone:'gold'},paused:{label:'已停用',tone:''}}[key])};
}
const sourceCardWorkflows={
 cecep:['检索 POST 接口 → JSON 列表 → 公告 HTML → 正文解析','近180天；每词最多3页，每轮最多18词、300条'],
 everbright:['首页 HTML → 栏目/内嵌列表 HTML → 公告 HTML → 正文或PDF/OCR','列表最多20次读取、230条；PDF最多前30页'],
 bjx:['招标列表 HTML → 公告 HTML（受限时浏览器渲染）→ 网页文字','最多5页、250条；公告图片不下载'],
 gfcl:['招标列表 HTML → 公告 HTML（受限时浏览器渲染）→ 网页文字','最多5页、250条；公告图片不下载'],
 cseg:['采购列表 HTML → 公告 HTML → 网页解析或图片OCR','最多5页、250条；图片识别文字'],
 ggzy:['首页 HTML 公告窗口 → 公告 HTML → 同条正文 HTML','仅首页公告窗口，最多200条'],
 ccgp:['栏目列表 HTML → 公告 HTML → 概况表及正文解析','中央及地方各最多3页，合计200条'],
 hubei:['首页 HTML 列表 → 公告guid → 详情 POST 接口 → HTML正文','仅首页工程建设窗口，最多200条'],
 datong:['首页 HTML → 栏目列表 HTML → 公告 HTML → 正文/表格解析','最多7次列表读取、200条；缺失标待核实'],
 hbzhan:['专题列表 HTML → 资讯详情 HTML → 正文解析','最多5页、180条；限招投标专题'],
 solidwaste:['资讯首页 HTML → 项目列表 HTML → 文章 HTML → 正文解析','最多6次列表读取、180条'],
 cnhw:['资讯列表 HTML → 文章 HTML → 正文解析','仅当前首页，最多180条；未接通翻页'],
 wangneng:['公示列表 HTML → 公示 HTML → 正文解析（附件原站查看）','最多4页、80条；附件原站查阅，非采购'],
 bece:['首页 HTML → 栏目列表 HTML → 公告 HTML → iframe PDF → 文字/OCR','最多7次列表读取、200条；PDF最多前30页'],
 bidcenter:['邀请列表 HTML → 项目 HTML → 概况/报名须知解析','最多8页、200条；会员联系方式不采'],
 bidi:['浏览器渲染列表 → 可见列表 HTML → 公告 HTML → 正文解析','每栏最多3页、合计250条；以实际访问状态为准'],
};
const sourceCardEntryPaths={
 cecep:'慧采云采购公告关键词检索（非固定列表页）',
 everbright:'首页 → 采购公告、变更等公开栏目',
 bjx:'垃圾发电网 → 招标栏目（/zb/）',
 gfcl:'固废网 → 招标栏目（/zb/）',
 cseg:'官网项目公告 → 公开采购列表（catid=15）',
 ggzy:'首页公开公告窗口（当前仅此窗口）',
 ccgp:'中央及地方公告 → 相应公告列表',
 hubei:'首页 → 工程建设招标公告窗口',
 datong:'首页 → 工程建设 / 政府采购栏目',
 hbzhan:'招投标专题列表（入口直达）',
 solidwaste:'资讯首页 → 项目栏目',
 cnhw:'公开资讯首页列表（/article）',
 wangneng:'官网 → 信息公示（非采购公告）',
 bece:'首页 → 招标及非招标采购公告的“更多”',
 bidcenter:'公开邀请招标栏目 → 项目公告',
 bidi:'招标预告 / 招标公告 / 采购意向列表',
};
const sourceCardListLinks={
 everbright:[['采购公告','https://zcpt.cebenvironment.com.cn/cms/category/bulletinNewList3.html?dates=300&categoryId=2&tabName=招标公告&page=1'],['变更公告','https://zcpt.cebenvironment.com.cn/cms/category/bulletinNewList3.html?dates=300&categoryId=3&tabName=变更公告&page=1']],
 bjx:[['招标列表','https://ljfd.bjx.com.cn/zb/']],
 gfcl:[['招标列表','https://gfcl.bjx.com.cn/zb/']],
 cseg:[['公开采购','https://www.cseg.cn/index.php?a=lists&catid=15']],
 ccgp:[['中央公告','https://www.ccgp.gov.cn/cggg/zygg/'],['地方公告','https://www.ccgp.gov.cn/cggg/dfgg/']],
 datong:[['工程建设','https://ggzyjy.dt.gov.cn:443/jyxxgc/index.jhtml'],['政府采购','https://ggzyjy.dt.gov.cn:443/jyxxzc/index.jhtml']],
 hbzhan:[['招投标专题','https://www.hbzhan.com/news/topic-99.html']],
 solidwaste:[['资讯列表','https://www.solidwaste.com.cn/news/'],['项目列表','https://www.solidwaste.com.cn/news/list?cid=246']],
 cnhw:[['资讯列表','https://www.cn-hw.net/article']],
 wangneng:[['信息公示','https://wannaenergy.com/news/notice']],
 bece:[['招标公告','https://ecp.capitaleco-pro.com/sccms/category/list.html?dates=300&categoryId=2&tabName=招标公告&page=1'],['采购公告','https://ecp.capitaleco-pro.com/sccms/category/list.html?dates=300&categoryId=13&tabName=采购公告&page=1']],
 bidcenter:[['邀请招标','https://www.bidcenter.com.cn/yqzb/index.html']],
 bidi:[['招标预告','https://www.bidizhaobiao.com/zbyg/'],['招标公告','https://www.bidizhaobiao.com/zbgg']],
};
function sourceListLinks(source){
 const links=source.custom?[[source.adapter==='rss'?'订阅源':'列表入口',source.url]]:(sourceCardListLinks[source.id]||[]);
 const result=links.filter(([,url])=>safeUrl(url));
 const sourceUrl=safeUrl(source.url),same=result.some(([,url])=>url===sourceUrl);
 return `${sourceUrl?`<a class="source-url" href="${esc(sourceUrl)}" target="_blank" rel="noopener noreferrer">${same?'列表入口':'访问来源'} ↗</a>`:''}${result.filter(([,url])=>url!==sourceUrl).map(([label,url])=>`<a class="source-url source-list-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`).join('')}`||'<span class="muted">暂无公开网址</span>';
}
function sourceCollectionProfile(source){
 if(source.custom){
  const extra=Array.isArray(source.extra_urls)?source.extra_urls.length:0;
  const listOnly=(source.adapter==='html'&&!source.content_selector)||(source.adapter==='json'&&!source.content_field);
  return {entry:(source.adapter==='rss'?'已配置订阅栏目':'已配置列表')+(extra?' · '+extra+'个补充栏目':''),
   method:source.adapter==='rss'?'RSS/Atom → 条目解析':source.adapter==='json'?'GET 列表接口 → JSON → '+(listOnly?'仅列表':'正文字段解析'):'列表 HTML → '+(listOnly?'仅列表':'公告 HTML → 正文解析'),
   scope:source.max_items?'每轮最多'+Number(source.max_items)+'条':'范围由管理员配置'};
 }
 const entry=(sourceCardEntryPaths[source.id]||sourceCardColumns[source.id]||'管理员配置栏目').replace(/ · 最多.*$/,'').replace(/ · 各最多.*$/,'').replace(' · 回看180天','');
 const workflow=sourceCardWorkflows[source.id];
 return {entry,method:workflow?.[0]||(sourceCardColumns[source.id]?'公告栏目未接入':'按管理员配置规则读取'),
  scope:workflow?.[1]||(sourceCardColumns[source.id]?'当前不自动采集':'范围由管理员配置')};
}
function sourceCollectionLogic(source){const profile=sourceCollectionProfile(source);return [profile.entry,profile.method,profile.scope].join(' · ');}

function sourceIssueMarkup(source){
  const issues=Array.isArray(source.issues)?source.issues:[];
  const partial=source.availability==='partial'||source.status==='部分可用';
  const issueItem=issue=>`<li><strong>${esc(sourceIssueLabels[issue.code]||issue.label||'读取问题')}</strong>：${esc(sourceCardIssueNotes[issue.code]||issue.note||'请查看来源原文。')}</li>`;
  const primary=issues.find(issue=>!['text_only','content_partial'].includes(issue.code))||issues[0];
  const others=issues.filter(issue=>issue!==primary);
  const reasons=primary?issueItem(primary):
    `<li>${esc(sourceCardPendingReasons[source.id]||(source.availability==='authorization'?'公开公告栏目未接入，需核实授权渠道。':source.availability==='pending'?'公告栏目尚未适配。':'本次采集未完成，请核查来源。'))}</li>`;
  return `<div class="source-failure"><h4>失败原因</h4>${partial?'<p class="source-kept">部分内容已保留</p>':''}<ul class="source-health-issues">${reasons}</ul>${others.length?`<details class="source-issue-details"><summary>其他 ${others.length} 项问题 <span aria-hidden="true">⌄</span></summary><ul class="source-health-issues">${others.map(issueItem).join('')}</ul></details>`:''}</div>`;
}
function sourceMatchesAvailability(source,selected){
  if(!selected)return true;
  if(selected.startsWith('issue:'))return Array.isArray(source.issues)&&source.issues.some(issue=>'issue:'+issue.code===selected);
  return publicSourceStatus(source).key===selected;
}
function publicSourceGroup(source){return publicSourceKinds.includes(source.kind)?source.kind:'其他登记来源';}
function publicSourceStatus(source){
  const info=publicSourceAvailability[source.availability];
  return {key:info?source.availability:'unknown',label:source.availability_label||info?.label||'接入状态待确认',tone:info?.tone||'',note:source.availability_note||'接入状态由管理员维护，登记网站不代表已经开始采集。'};
}
function publicSourceCard(source,admin=false){
  const status=sourceCollectionStatus(source),url=safeUrl(source.url);
  const operations=admin?`${source.custom?`<button type="button" class="text-button" data-edit-source="${esc(source.id)}">编辑 / 测试</button>`:!source.adapter_available?`<button type="button" class="text-button" data-adapt-source="${esc(source.id)}">配置此网站</button>`:''}${source.enabled||!source.custom&&source.adapter_available?`<button type="button" class="text-button" data-toggle-source="${esc(source.id)}">${source.enabled?'停用监测':'启用监测'}</button>`:''}`:'';
  const profile=sourceCollectionProfile(source);
  return `<article class="source-card source-card-compact" data-collection-state="${status.key}"><div class="source-status"><h3>${esc(source.name)}</h3>${tag(status.label,status.tone)}</div><div class="source-card-body"><div class="source-card-main"><p class="source-collection-entry"><span>采集入口</span>${esc(profile.entry)}</p><p class="source-collection-logic"><span>采集逻辑</span>${esc(profile.method)}</p></div><div class="source-card-side"><p class="source-collection-scope"><span>采集范围</span>${esc(profile.scope)}</p>${status.key==='failure'?sourceIssueMarkup(source):''}</div></div><div class="source-card-actions"><div class="source-card-links">${sourceListLinks(source)}</div><div class="source-card-controls">${operations}</div></div></article>`;
}
async function renderPublicSources(version) {
  const owner=state.user?.id,session=state.sessionGeneration,response=await api('/api/public-sources');
  if(version!==state.renderVersion||owner!==state.user?.id||session!==state.sessionGeneration)return;
  renderSourceDirectory(response.items||[],version,false);
}
function renderSourceDirectory(items,version,admin=false){
  const owner=state.user?.id,session=state.sessionGeneration;
  const groups=[...publicSourceKinds,...(items.some(source=>publicSourceGroup(source)==='其他登记来源')?['其他登记来源']:[])];
  // Remember type, filters and page so saving or toggling a source keeps the admin's place.
  const view=state.sourceDirectory||={group:'',query:'',availability:'',page:1};
  let {group,query,availability,page}=view;if(group&&!groups.includes(group))group='';
  $('#content').innerHTML=`${admin?'<div id="source-editor"></div>':''}<form id="source-catalog-filters" class="filters"><div class="filter-row"><input class="search-field" name="q" type="search" maxlength="150" aria-label="搜索来源网站" placeholder="搜索网站名称、类型或网址"><select name="availability" aria-label="筛选网站接入状态"><option value="">全部接入状态</option>${Object.entries(publicSourceAvailability).map(([value,info])=>`<option value="${value}">${esc(info.label)}</option>`).join('')}</select><button type="submit" class="secondary">筛选</button>${admin?'<button class="primary" id="new-source" type="button">＋ 添加监测网站</button>':''}</div></form><section class="topic-navigation source-type-navigation" aria-label="网站类型分类"><nav class="source-kind-options topic-tab-strip" aria-label="网站类型"></nav></section><div class="count-line source-directory-count"><span id="source-directory-count" aria-live="polite"></span><span>共登记 ${items.length} 个网站</span></div><div id="source-directory-results"></div>`;
  const draw=()=>{
    if(version!==state.renderVersion||owner!==state.user?.id||session!==state.sessionGeneration)return;
    const word=query.trim().toLowerCase(),filtered=items.filter(source=>(!group||publicSourceGroup(source)===group)&&sourceMatchesAvailability(source,availability)&&(!word||[source.name,source.kind,source.url].join(' ').toLowerCase().includes(word)));
    $('#source-directory-count').textContent=`${group||'全部网站类型'} · 找到 ${filtered.length} 个网站`;
    $('.source-kind-options').innerHTML=`<button type="button" class="topic-tab${!group?' active':''}" data-source-kind="" aria-pressed="${!group}">全部类型 <small>${items.length}</small></button>${groups.map(kind=>`<button type="button" class="topic-tab${group===kind?' active':''}" data-source-kind="${esc(kind)}" aria-pressed="${group===kind}"><span>${esc(kind)}</span><small>${items.filter(source=>publicSourceGroup(source)===kind).length}</small></button>`).join('')}`;
    const size=pageSize('sources');page=Math.max(1,Math.min(page,Math.ceil(filtered.length/size)||1));Object.assign(view,{group,query,availability,page});
    const visible=filtered.slice((page-1)*size,page*size);
    const results=$('#source-directory-results');
    results.innerHTML=filtered.length?`<div class="source-grid public-source-grid">${visible.map(source=>publicSourceCard(source,admin)).join('')}</div>${filtered.length>pageSizeOptions('sources')[0]?pagination({total:filtered.length,page,page_size:size},'sources').replace('条 ·','个网站 ·'):''}`:empty(items.length?'没有符合条件的网站':'暂无登记来源',items.length?'可调整网站类型、接入状态或搜索词。':'管理员登记的网站会在这里显示。');
    results.querySelectorAll('[data-page]').forEach(button=>button.onclick=event=>{event.stopPropagation();page=Number(button.dataset.page);draw();scrollToTarget(results);});
    const sizeControl=results.querySelector('[data-page-size]');
    if(sizeControl)sizeControl.onchange=event=>{event.stopPropagation();state.sizes.sources=Number(sizeControl.value);page=1;draw();scrollToTarget(results);};
    $('.source-kind-options').querySelectorAll('[data-source-kind]').forEach(button=>button.onclick=()=>{group=button.dataset.sourceKind;page=1;draw();[...$('.source-kind-options').querySelectorAll('[data-source-kind]')].find(current=>current.dataset.sourceKind===group)?.focus({preventScroll:true});});
  };
  const form=$('#source-catalog-filters');
  form.querySelector('[name=availability]').insertAdjacentHTML('beforeend',`<optgroup label="具体读取问题">${Object.entries(sourceIssueLabels).map(([code,label])=>`<option value="issue:${code}">${esc(label)}</option>`).join('')}</optgroup>`);
  form.elements.q.value=query;form.elements.availability.value=availability;
  form.onsubmit=event=>{event.preventDefault();const values=new FormData(form);query=values.get('q')||'';availability=values.get('availability')||'';page=1;draw();};
  form.querySelector('[name=q]').oninput=event=>{query=event.target.value;page=1;draw();};form.querySelector('[name=availability]').onchange=()=>form.requestSubmit();draw();
  if(admin){
    $('#new-source').onclick=()=>editSource();
    $('#source-directory-results').onclick=e=>{const edit=e.target.closest('[data-edit-source]'),adapt=e.target.closest('[data-adapt-source]'),toggle=e.target.closest('[data-toggle-source]');
      if(edit)editSource(items.find(source=>source.id===edit.dataset.editSource));
      if(adapt){const source=items.find(item=>item.id===adapt.dataset.adaptSource);editSource({name:source.name,url:source.url,kind:source.kind});}
      if(toggle)withButton(toggle,async()=>{const source=items.find(item=>item.id===toggle.dataset.toggleSource);await api(`/api/source-configs/${source.id}/state`,{method:'PATCH',body:JSON.stringify({enabled:!source.enabled})});toast(source.enabled?'已停用，历史公告保留':'已启用，将在下一轮采集');loadView();});
    };
  }
}
async function renderMonitoringSources(version) {
  if(!isAdmin())return renderPublicSources(version);
  const owner=state.user?.id,session=state.sessionGeneration;
  const [response,publicResponse]=await Promise.all([api('/api/sources'),api('/api/public-sources')]);if(version!==state.renderVersion||owner!==state.user?.id||session!==state.sessionGeneration)return;
  const healthById=new Map((publicResponse.items||[]).map(source=>[source.id,source]));
  const items=(response.items||[]).map(source=>({...source,...healthById.get(source.id),name:source.name,url:source.url,kind:source.kind}));
  renderSourceDirectory(items,version,true);
}
function sourceFormData(form){const f=new FormData(form),result={};
  ['name','url','kind','adapter','item_selector','link_selector','title_selector','date_selector','content_selector','items_path','title_field','url_field','date_field','content_field','note'].forEach(k=>result[k]=String(f.get(k)||''));
  ['extra_urls','include_keywords','exclude_keywords'].forEach(k=>result[k]=k==='extra_urls'?String(f.get(k)||'').split(/\n/).map(s=>s.trim()).filter(Boolean):splitWords(f.get(k)));
  result.max_items=Number(f.get('max_items'));result.enabled=f.has('enabled');return result;
}
function editSource(source={}) {
  if(!isAdmin())return;
  const s={name:'',url:'',kind:'自定义监测',adapter:'html',extra_urls:[],item_selector:'a[href]',link_selector:'',title_selector:'',date_selector:'',content_selector:'article,.article-content,.content',items_path:'',title_field:'title',url_field:'url',date_field:'published_at',content_field:'',include_keywords:[],exclude_keywords:[],max_items:20,note:'',enabled:false,...source};let token='';
  const field=(label,key,extra='')=>formField(label,key,s[key],extra);
  $('#source-editor').innerHTML=`<form class="panel editor-panel compact-form" id="source-form"><div class="panel-header"><h2>${s.id?'编辑监测网站':'添加监测网站'}</h2><button class="text-button" type="button" id="close-source-editor">取消</button></div><p class="muted">填好公开公告地址，可展开规则手动配置，也可使用智能填写。启用前需测试当前规则。</p><div class="form-grid">${field('监测名称','name','required maxlength="80"')}<div class="form-field"><label for="source-adapter">采集模式</label><select name="adapter" id="source-adapter"><option value="html"${s.adapter==='html'?' selected':''}>网页列表 · HTML</option><option value="rss"${s.adapter==='rss'?' selected':''}>RSS / Atom 订阅源</option><option value="json"${s.adapter==='json'?' selected':''}>公开 JSON 接口 · GET</option></select></div><div class="full">${field('入口网址（公开列表 / RSS / JSON）','url','type="url" required placeholder="https://网站/公告列表"')}</div><label class="check-label full"><input name="enabled" type="checkbox"${s.enabled?' checked':''}>启用定时监测（需先测试当前配置）</label></div><details class="form-options"><summary><span class="form-options-heading">采集规则<span class="form-options-note" data-source-options-summary></span></span><span class="form-options-toggle"><span class="form-options-expand">展开</span><span class="form-options-collapse">收起</span><span class="form-options-chevron" aria-hidden="true"></span></span></summary><div class="form-grid">${field('来源分类','kind','maxlength="40"')}${field('每轮最多采集条数','max_items','type="number" min="1" max="40" required')}${textField('补充列表页（同域，每行一个，最多4个）','extra_urls',s.extra_urls.join('\n'))}<div class="full source-rule-group" data-mode="html"><h3>网页提取规则</h3><p class="muted">选择器用于定位页面元素。默认提取链接；建议用智能填写识别具体公告列表，再检查测试样例。</p><div class="form-grid">${field('列表项选择器','item_selector','placeholder="例如 .notice-list li"')}${field('列表项内的链接（空=自动识别）','link_selector','placeholder="例如 a[href]"')}${field('标题选择器（空=链接文字）','title_selector')}${field('发布日期选择器（可选）','date_selector','placeholder="例如 time 或 .date"')}</div></div><div class="full source-rule-group" data-mode="json"><h3>JSON 字段路径</h3><p class="muted">路径用点号分隔，如 data.records；根节点就是数组时，列表路径留空。</p><div class="form-grid">${field('列表路径','items_path')}${field('标题字段','title_field')}${field('详情网址字段','url_field')}${field('发布日期字段','date_field')}${field('正文字段（可选）','content_field')}</div></div>${field('详情正文选择器（留空只采列表）','content_selector','placeholder="例如 article 或 .article-content"')}${textField('来源关注词（留空采集全部匹配条目）','include_keywords',s.include_keywords.join('，'))}${textField('来源排除词（标题命中则跳过）','exclude_keywords',s.exclude_keywords.join('，'))}${textField('配置备注','note',s.note)}</div></details><div class="form-footer"><button type="button" id="test-source" class="secondary">测试抓取 · 不入库</button><button type="submit" class="primary">保存监测网站</button></div><div id="source-test-result" role="status"></div></form>`;
  const form=$('#source-form');
  const showMode=()=>form.querySelectorAll('[data-mode]').forEach(e=>e.hidden=e.dataset.mode!==form.elements.adapter.value);showMode();
  const updateRulesSummary=()=>{const value=sourceFormData(form),parts=[({html:'网页列表',rss:'RSS / Atom',json:'公开 JSON'}[value.adapter]||value.adapter),'每轮最多 '+value.max_items+' 条'];if(value.extra_urls.length)parts.push(value.extra_urls.length+' 个补充列表');if(value.include_keywords.length)parts.push(value.include_keywords.length+' 个关注词');if(value.exclude_keywords.length)parts.push(value.exclude_keywords.length+' 个排除词');form.querySelector('[data-source-options-summary]').textContent=parts.join(' · ');};updateRulesSummary();
  form.addEventListener('invalid',e=>{const options=e.target.closest('details.form-options');if(options)options.open=true;},true);
  form.addEventListener('input',e=>{updateRulesSummary();if(e.target.name&&e.target.name!=='enabled'){token='';$('#source-test-result').textContent='配置有变化，请重新测试后启用。';}});
  form.addEventListener('change',e=>{showMode();updateRulesSummary();if(e.target.name&&e.target.name!=='enabled')token='';});
  $('#close-source-editor').onclick=()=>closePopups();
  $('#test-source').onclick=e=>{if(!form.reportValidity())return;withButton(e.currentTarget,async()=>{token='';const result=await api('/api/source-preview',{method:'POST',body:JSON.stringify(sourceFormData(form))});if(!form.isConnected)return;token=result.test_token || '';
    $('#source-test-result').innerHTML=`<div class="warning-box"><strong>${result.passed?'测试通过':'没有匹配到公告'} · ${result.found} 条</strong><p>${esc(result.notice)}</p>${result.warnings.map(w=>`<p>${esc(w)}</p>`).join('')}</div>${result.items.map(x=>`<article class="preview-row"><h3>${esc(x.title)}</h3><p>${esc(x.published_at || '发布日期未提取')} · ${esc(x.detail_status || (x.content?'正文可读':'仅列表线索'))}</p><a class="source-url" href="${esc(safeUrl(x.url))}" target="_blank" rel="noopener noreferrer">核对原始公告 ↗</a>${x.content?`<p>${esc(x.content.slice(0,260))}…</p>`:''}</article>`).join('')}`;
  });};
  form.onsubmit=e=>{e.preventDefault();withButton(form.querySelector('[type=submit]'),async()=>{await api(s.id?`/api/source-configs/${s.id}`:'/api/source-configs',{method:s.id?'PUT':'POST',body:JSON.stringify({...sourceFormData(form),test_token:token})});toast('网站配置已保存；已启用来源将参与后续采集。');loadView();});};
  mountEditor($('#source-editor').firstElementChild);
}
