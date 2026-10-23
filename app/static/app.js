'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const asText = value => value == null || value === '' ? '待核实' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
const safeUrl = value => { try { const url = new URL(value); return /^https?:$/.test(url.protocol) ? url.href : ''; } catch { return ''; } };
const dateText = value => { if (!value) return '暂无'; const d = new Date(value); return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}); };
const shortDate = value => value ? String(value).slice(0,10) : '日期待核实';
const sourceTime = (value, precision) => !value ? '未取得' : precision === 'minute' ? dateText(value) : `${shortDate(value)}（${precision === 'day' ? '原文仅提供日期' : '时分待核实'}）`;
const list = value => Array.isArray(value) ? value : value ? [value] : [];
const categories = ['智慧焚烧','烟气提标','控制系统','设备技改','其他'];
const stages = ['招标','询价','采购意向','变更','中标候选人','中标结果','终止','资讯'];
const statuses = ['待研判','跟进中','已联系','已排除','已归档'];
const subscriptionRecallNotice='已启用主题关键词参与所选已接入来源后续采集，匹配预览只看当前入库；网站覆盖与规则仍需管理员维护。';
const provinces = ['北京','天津','河北','山西','内蒙古','辽宁','吉林','黑龙江','上海','江苏','浙江','安徽','福建','江西','山东','河南','湖北','湖南','广东','广西','海南','重庆','四川','贵州','云南','西藏','陕西','甘肃','青海','宁夏','新疆'];
const state = {sessionGeneration:0,view:'today',page:1,companyPage:1,followupPage:1,followupFilters:{q:'',status:'',sort:'followup_updated_at'},detailSavePromise:null,detailSaved:false,filters:{q:'',category:'',province:'',stage:'',status:'',min_score:'55',actionable:false,sort:'score'},defaultMinScore:55,filtersTouched:false,strategyPromise:null,companyFilters:{q:'',province:''},dashboard:null,detail:null,renderVersion:0,drawerVersion:0,poll:null,toastTimer:null,focusBeforeDrawer:null};
const isAdmin = () => state.user?.role === 'admin';
const followupSaves=new Map();
const followupSaveKey=(owner,id)=>owner+':'+String(id);
function pendingFollowupSave(owner,id){return followupSaves.get(followupSaveKey(owner,id));}
function lockDetailControls(form){const controls=[...form.querySelectorAll('input,select,textarea,button'),...document.querySelectorAll('#team-followup-panel button,#team-followup-panel select'),$('#analyze-detail')].filter(Boolean),previous=controls.map(control=>[control,!!control.disabled]);controls.forEach(control=>control.disabled=true);return()=>{previous.forEach(([control,disabled])=>{if(control.isConnected)control.disabled=disabled;});if(form.isConnected&&$('#followup-form')===form)updateTeamAssignmentControls();};}
function trackFollowupSave(owner,id,promise){
  const key=followupSaveKey(owner,id),entry={owner,id,promise};followupSaves.set(key,entry);
  const clear=()=>{if(followupSaves.get(key)===entry)followupSaves.delete(key);};
  promise.then(clear,clear);
  return entry;
}
const viewMeta = {
  opportunities:['公共情报','OPPORTUNITY RADAR','发现值得跟进的机会','从公开招采信息中，筛选与你的业务相关的产业信号。'],
  subscriptions:['获客订阅','SUBSCRIPTIONS','让机会围绕你的业务而来','按行业、业务能力、关键词和信息来源，建立自己的获客专题。'],
  recommendations:['订阅推荐','YOUR OPPORTUNITIES','与你的业务有关的机会','按订阅匹配公开证据，清楚了解推荐原因，再安排跟进。'],
  users:['账号管理','ACCOUNT MANAGEMENT','让团队各司其职','管理普通用户与超级管理员；停用或重置密码后，旧会话立即失效。'],
  model:['智能化配置','AI CONNECTION','连接适合你的智能分析服务','配置本地模型或外部兼容 API，测试连接后用于公告解读。'],
  account:['账号配置','ACCOUNT SETTINGS','管理个人与团队账号','修改自己的登录密码；创建、停用团队账号或重置密码后，相关旧会话立即失效。'],
  companies:['目标企业','COMPANY DIRECTORY','找到机会背后的企业','以焚烧厂名录为基础，建立可检索的目标客户地图。'],
  report:['情报日报','DAILY INTELLIGENCE','今天，值得关注什么','汇总最新有效商机与采集风险，把精力放在下一步行动上。'],
  sources:['监测网站','COLLECTION OPERATIONS','让每一条信号都有出处','配置监测网站、测试采集规则，持续关注与你业务相关的公开信息。'],
  settings:['推荐策略','RECOMMENDATION STRATEGY','让推荐更贴近你的业务','调整关注方向、区域偏好与采集频率，持续收敛高价值线索。']
};
const regularViewMeta = {
  today:['今日资讯','SHARED INTELLIGENCE','查看共享公开资讯','查看团队共享的公告，按业务条件筛选并保存自己的跟进记录。'],
  sources:['监测网站','PUBLIC SOURCES','查看公开资讯的来源网站','了解公告来自哪些公开网站，来源由管理员统一维护。'],
  configuration:['系统配置','PERSONAL SETTINGS','配置我的订阅与推送','设置自己的关注主题和自动推送计划；启用主题参与后续采集，网站覆盖与规则由管理员维护。'],
  account:['账号配置','MY ACCOUNT','管理你的登录信息','跟进记录与个人订阅按账号保存。'],
  automation:['自动化任务','PERSONAL AUTOMATION','查看我的订阅推送任务','查看个人推送任务的状态、执行计划和最近新增推送数量。']
};
function resetFollowupState(){state.followupPage=1;state.followupFilters={q:'',status:'',sort:'followup_updated_at'};state.detailSavePromise=null;state.detailSaved=false;state.detailAnalysisBusy=false;if(state.sizes)delete state.sizes.followups;}
function toast(message, error = false) { clearTimeout(state.toastTimer); const element = $('#toast'); element.textContent = message; element.classList.toggle('error', error); element.hidden = false; state.toastTimer = setTimeout(() => element.hidden = true, error ? 8000 : 4500); }
function showLogin() { state.assistQueryTransition?.progress.stop();state.assistQueryTransition=null;stopNotificationPolling(); closeAccountMenu(); closePopups(); ++state.sessionGeneration;state.user=null;resetFollowupState();resetTeamDashboardState();state.topicQuery='';state.topicQueryDraft='';if(state.localPages)state.localPages.topics=1;if(state.sizes)delete state.sizes.topics; state.pushQuery='';state.pushRead='';state.pushFacets={};state.pushPage=1;state.pushTopicId='';state.pushSort='pushed_at';state.topicSearch='';state.topicExpanded=false;state.viewScroll={};state.renderedViewKey=null;state.topics=[];state.sourceDirectory=null;state.accountTab='profile'; state.strategyPromise=null; state.detail=null; state.topicId=''; state.recommendationQuery=''; state.recommendationStatus=''; state.recommendationPage=1; ++state.renderVersion; ++state.drawerVersion; $('#content').innerHTML='';$('#content').inert=false;$('#content').style.minHeight='';$('#content').classList.remove('is-loading','view-arrive');$('#content').removeAttribute('aria-busy'); $('#drawer-content').innerHTML=''; $('#shell').hidden = true; $('#login').hidden = false; closeDrawer(); clearInterval(state.poll); state.poll = null; }
async function api(path, options = {}) {
  const requestOwner=state.user?.id,requestSession=state.sessionGeneration;
  const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), path==='/api/companies/import' ? 300000 : (path.includes('/analyze') || path.endsWith('/test') || path==='/api/assist' || path==='/api/source-preview') ? 120000 : 45000);
  try {
    const response = await fetch(path, {...options, credentials:'same-origin', signal:controller.signal, headers:{...(options.body ? {'Content-Type':'application/json'} : {}),...options.headers}});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { if (response.status === 401 && path !== '/api/login'&&requestOwner===state.user?.id&&requestSession===state.sessionGeneration) showLogin(); const detail = typeof data.detail === 'string' ? data.detail : data.detail ? JSON.stringify(data.detail) : ''; throw new Error(detail || `请求未完成（${response.status}），请稍后重试。`); }
    return data;
  } catch (error) { if (error.name === 'AbortError') throw new Error('请求超时，请稍后刷新。采集或分析可能仍在后台执行。'); if (error instanceof TypeError) throw new Error('暂时无法连接服务器，请检查网络后重试。'); throw error; }
  finally { clearTimeout(timeout); }
}
function options(values, selected, placeholder) { return `<option value="">${esc(placeholder)}</option>` + values.map(value => `<option value="${esc(value)}"${value === selected ? ' selected' : ''}>${esc(value)}</option>`).join(''); }
function empty(title, description) { return `<div class="empty"><div class="empty-symbol">◎</div><h3>${esc(title)}</h3><p>${esc(description)}</p></div>`; }
function loading(label = '正在读取情报…') { return `<div class="loading">${esc(label)}</div>`; }
function errorPanel(error) { return `<div class="empty"><div class="empty-symbol">↻</div><h3>暂时未能加载</h3><p>${esc(error.message)}</p><button class="secondary" data-action="retry" style="margin-top:20px">重新加载</button></div>`; }
function pagination(data,type='opportunities'){const total=Number(data.total||0),size=Number(data.page_size||pageSize(type)),page=Number(data.page||1),pages=Math.max(1,Math.ceil(total/size));return `<div class="pagination"><span>共 ${total} 条</span><label>每页 <select data-page-size="${type}" aria-label="每页显示数量">${[5,10,20].map(n=>`<option value="${n}"${n===size?' selected':''}>${n} 条</option>`).join('')}</select></label><button class="secondary" data-page="${page-1}" data-page-type="${type}"${page<=1?' disabled':''}>← 上一页</button><span>第 ${page} / ${pages} 页</span><button class="secondary" data-page="${page+1}" data-page-type="${type}"${page>=pages?' disabled':''}>下一页 →</button></div>`;}
function opportunityQuery() { return queryToParams({...state.filters,page:state.page,page_size:pageSize('opportunities')}).toString(); }
function tag(value, type = '') { return `<span class="tag ${type}">${esc(value)}</span>`; }
function card(item) {
  const score = Number(item.score || 0), id = esc(item.id), risk = ['中标结果','中标候选人','终止','资讯'].includes(item.stage);
  return `<article class="opportunity-card"><div class="score-block ${score >= 75 ? 'high' : ''}"><div class="score-number">${score}</div><div class="score-label">匹配分</div></div><div><button class="card-title" data-detail="${id}">${esc(item.title || '标题待核实')}</button><div class="card-tags">${tag(item.category || '其他')}${tag(item.stage || '阶段待核实',risk ? 'gold' : 'dark')}${tag(item.province || '区域待核实','blue')}${item.detail_status === '仅列表线索' ? tag('仅列表线索','gold') : ''}${item.actionable ? tag('可跟进','dark') : tag(item.document_state === 'expired' ? '文件领取或报名已截止' : '观察线索','gold')}</div><p class="card-summary">${esc(item.summary || '暂未生成摘要，请查看原文及研判依据。')}</p><div class="card-meta"><span>${esc(item.source_name || '来源待核实')}</span><span>发布 ${esc(sourceTime(item.published_at,item.published_at_precision))}</span>${item.deadline ? `<span>截止 ${esc(sourceTime(item.deadline,item.deadline_precision))}</span>` : ''}${item.created_at ? `<span>采集 ${esc(dateText(item.created_at))}</span>` : ''}${item.budget_text ? `<span>${esc(item.budget_text)}</span>` : ''}</div>${teamFollowupBadge(item)}</div><div class="card-side">${personalFollowupBadge(item)}<button class="secondary" data-detail="${id}">查看研判 <span>↗</span></button></div></article>`;
}
function dashboardHtml(data) {
  const s = data.stats || {}, ai = data.ai || {};
  const metric = (label,value,note,featured = false,symbol = '↗') => `<div class="metric ${featured ? 'featured' : ''}"><span class="metric-label">${esc(label)}</span><span class="metric-symbol">${symbol}</span><div class="metric-value">${Number(value || 0).toLocaleString('zh-CN')}</div><div class="metric-note">${esc(note)}</div></div>`;
  const publicMetrics=`<section class="metrics" aria-label="情报概览">${metric('高优先级商机',s.high_priority,'优先安排核实与跟进',true)}${metric('有效跟进线索',s.actionable,'已排除已知参与期限结束公告')}${metric('累计情报',s.total,'持续积累产业公开信号','', '▤')}${metric('目标企业',s.companies,'焚烧厂名录关联匹配','', '▦')}</section>`;
  return publicMetrics;
}
const filterStorageKey = () => state.user?.id ? 'incineration-radar.filters.v2.'+state.user.id : null;
function persistFilters() { state.filtersTouched = true; try { const key=filterStorageKey();if(key)sessionStorage.setItem(key,JSON.stringify(state.filters)); } catch { /* Browser storage is optional. */ } }
function restoreFilters() {
  state.filters={q:'',terms:'',term_mode:'any',published_from:'',published_to:'',deadline_state:'',category:'',province:'',stage:'',status:'',min_score:'0',actionable:false,sort:'published_at'};state.filtersTouched=false;
  try {
    const key=filterStorageKey(),saved = key?JSON.parse(sessionStorage.getItem(key) || 'null'):null;
    if (!saved || typeof saved !== 'object') return;
    for (const key of ['q','terms','term_mode','published_from','published_to','deadline_state','category','province','stage','status']) if (typeof saved[key] === 'string') state.filters[key] = saved[key];
    const score = Number(saved.min_score);
    if (Number.isFinite(score) && score >= 0 && score <= 100) state.filters.min_score = String(score);
    state.filters.actionable = saved.actionable === true;
    state.filters.sort = ['published_at','deadline'].includes(saved.sort) ? saved.sort : 'score';
    state.filtersTouched = true;
  } catch { /* Ignore unavailable or invalid browser storage. */ }
}
async function ensureRecommendationDefaults() {
  if (!state.strategyPromise) state.strategyPromise = api('/api/settings').then(settings => {
    const score = Number(settings.min_score ?? 55);
    state.defaultMinScore = Number.isFinite(score) ? Math.max(0,Math.min(100,score)) : 55;
    if (!state.filtersTouched) {state.filters.min_score='0';state.filters.sort='published_at';}
  }).catch(error => { state.strategyPromise = null; throw error; });
  await state.strategyPromise;
}
async function renderOpportunities(version) {
  await ensureRecommendationDefaults(); if (version !== state.renderVersion) return;
  // Paging and filtering do not change the headline counts; reuse them for a minute instead of recounting the library.
  const cached=state.reuseDashboard&&state.dashboard&&Date.now()-state.dashboardAt<60000;
  const [dashboard, data] = await Promise.all([cached?state.dashboard:api('/api/dashboard'),api(`/api/opportunities?${opportunityQuery()}`)]); if (version !== state.renderVersion) return;
  if(!cached)state.dashboardAt=Date.now();
  const lastPage=Math.max(1,Math.ceil(data.total/pageSize('opportunities')));if(state.page>lastPage){state.page=lastPage;return renderOpportunities(version);}
  state.dashboard = dashboard; const f = state.filters;queryRoute('today',f,state.page);
  $('#content').innerHTML = dashboardHtml(dashboard) + `<div class="section-heading"><div><h2>公开资讯列表</h2><p>查看最新公告，也可按业务条件筛选。</p></div><button class="secondary" id="export">导出 CSV ↓</button></div>${noticeFilterMarkup(f)}<div class="count-line"><span>找到 <strong>${Number(data.total || 0)}</strong> 条相关情报</span><span>评分是筛选依据，不代表中标概率</span></div><div class="opportunity-list">${data.items?.length ? data.items.map(card).join('') : empty('还没有符合条件的商机',isAdmin()?'可调整筛选条件，或手动采集更新公开资讯。来源异常可到监测网站查看。':'可调整筛选条件，或等待共享资讯更新。')}</div>${pagination(data)}`;
  $('#opportunity-filters').addEventListener('submit', event => { event.preventDefault(); readFilters(); });
  $('#opportunity-filters').addEventListener('change', event => { if (!event.target.closest('.assist-panel,.filter-popover') && event.target.matches('select,input[type=checkbox]')) readFilters(); });
  $('#reset-filters').onclick = () => { state.filters = {q:'',category:'',province:'',stage:'',status:'',min_score:String(state.defaultMinScore),actionable:false,sort:'score'}; persistFilters(); state.page = 1; loadView(); };
  bindCompactNoticeFilters($('#opportunity-filters'));
  $('#export').onclick = exportCsv;
}
function readFilters() { const form=$('#opportunity-filters'),data=new FormData(form);for(const key of Object.keys(state.filters))state.filters[key]=key==='actionable'?data.has(key):data.get(key)||'';Object.assign(state.filters,readQueryFacets(form));persistFilters();state.page=1;queryRoute('today',state.filters,state.page);loadView({anchor:resultsAnchor()}); }
async function exportCsv() {
  const button = $('#export'); button.disabled = true; button.textContent = '正在导出…';
  try { const response = await fetch(`/api/export?${opportunityQuery()}`, {credentials:'same-origin'}); if (!response.ok) { if (response.status === 401) showLogin(); throw new Error('导出失败，请重新登录或稍后重试。'); } const blob = await response.blob(); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `焚烧产业商机-${businessDateText('sv-SE',{})}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url),1000); toast('已导出当前筛选条件下的商机。'); } catch(error) { toast(error.message,true); } finally { button.disabled = false; button.textContent = '导出 CSV ↓'; }
}
async function renderCompanies(version) {
  const data = await api(`/api/companies?${new URLSearchParams({...state.companyFilters,page:state.companyPage,page_size:pageSize('companies')})}`); if (version !== state.renderVersion) return;
  const lastPage=Math.max(1,Math.ceil(data.total/pageSize('companies')));if(state.companyPage>lastPage){state.companyPage=lastPage;return renderCompanies(version);}
  $('#content').innerHTML = `<form id="company-filters" class="filters"><div class="filter-row"><input class="search-field" name="q" aria-label="搜索企业" placeholder="搜索企业、集团或城市" value="${esc(state.companyFilters.q)}"><select name="province" aria-label="企业所在区域">${options(data.provinces?.length ? data.provinces : provinces,state.companyFilters.province,'全部区域')}</select><button class="primary" type="submit">搜索企业</button></div></form><div class="count-line"><span>目标企业 <strong>${Number(data.total || 0)}</strong> 家</span><span>来自导入名录 · 联系信息需自行核实</span></div>${data.items?.length ? `<div class="table-wrap"><table><thead><tr><th>企业 / 所属集团</th><th>区域</th><th>焚烧炉</th><th>投运时间</th><th>名录联系信息</th></tr></thead><tbody>${data.items.map(c => `<tr><td class="wrap-cell"><strong>${esc(c.name)}</strong><span class="cell-sub">${esc(c.group_name || '集团待核实')}</span>${c.address ? `<span class="cell-sub">${esc(c.address)}</span>` : ''}</td><td>${esc(c.province || '待核实')}<span class="cell-sub">${esc(c.city || '')}</span></td><td>${esc(asText(c.furnaces))}</td><td>${esc(asText(c.commissioned_at))}</td><td>${esc(c.contact_name || '暂无联系人')}<span class="cell-sub">${esc(c.contact_phone || '暂无联系方式')}</span></td></tr>`).join('')}</tbody></table></div>` : empty('暂无符合条件的企业','尝试更短的企业名称，或切换区域条件。')}${pagination(data,'companies')}`;
  $('#company-filters').onsubmit = event => { event.preventDefault(); const f = new FormData(event.target); state.companyFilters = {q:String(f.get('q') || ''),province:String(f.get('province') || '')}; state.companyPage = 1; loadView(); };
}
function sourceStatus(source) { const status = String(source.status || '').toLowerCase(); if (source.enabled === false) return ['未启用','gold']; if (['ok','success','healthy','正常'].includes(status)) return ['正常','dark']; if (['running','collecting','运行中'].includes(status)) return ['采集中','blue']; if (['pending','never','idle','unknown','待采集','未接入','部分可用',''].includes(status)) return [source.status || '待采集','gold']; return [source.status || '待核实','red']; }
const runStatus = value => ({success:'完成',completed:'完成',partial:'部分成功',failed:'失败',error:'失败',running:'运行中',queued:'排队中',skipped:'已跳过'}[value] || value || '待核实');
async function renderSources(version) {
  if(!isAdmin())return renderPublicSources(version);
  const sources = await api('/api/sources'); if (version !== state.renderVersion) return;
  $('#content').innerHTML = `<div class="panel"><h2>采集覆盖与边界</h2><p class="muted">采集公开可访问的信息。登录、验证码或反爬限制会记录为来源异常，系统不会绕过限制。单个来源失败不影响其他来源继续运行。</p></div><div class="section-heading"><h2>来源监测</h2><span class="muted">${sources.items?.length || 0} 个配置来源</span></div><div class="source-grid">${sources.items?.length ? sources.items.map(s => {const [label,tone] = sourceStatus(s), url = safeUrl(s.url); return `<article class="source-card"><div class="source-status"><h3>${esc(s.name)}</h3>${tag(label,tone)}</div>${url ? `<a class="source-url" href="${esc(url)}" target="_blank" rel="noopener noreferrer">访问来源 ↗</a>` : ''}<p>类型：${esc(s.kind || '公开来源')} · 最近采集 ${Number(s.last_count || 0)} 条</p><p>最近成功：${esc(dateText(s.last_success))}</p>${s.last_error ? `<p class="error-text">${esc(s.last_error)}</p>` : ''}${s.note ? `<p>${esc(s.note)}</p>` : ''}</article>`; }).join('') : empty('尚未配置采集来源','请检查部署配置中的来源清单。')}</div><div class="source-actions"><button type="button" class="text-button" data-view="configuration" data-config-target="automation" data-nav-anchor="#task-runs">查看采集运行记录 ↗</button></div>`;
}
async function renderSettings(version) {
  const settings = await api('/api/settings'); if (version !== state.renderVersion) return;
  $('#content').innerHTML = `<form id="settings-form" class="panel settings-form compact-form"><div class="panel-header"><div><h2>业务关注策略</h2><p class="muted">管理公共资讯的关注方向和评分。采集频率在系统配置的自动化任务中设置。</p></div></div><div class="form-grid"><div class="form-field full"><label for="focus-keywords">重点关注关键词</label><textarea id="focus-keywords" name="focus_keywords" rows="4" placeholder="智慧焚烧，烟气提标，智能控制">${esc(list(settings.focus_keywords).join('，'))}</textarea><small>使用逗号或换行分隔。建议填写具体技术、设备和业务需求。</small></div><div class="form-field full"><label for="exclude-keywords">排除关键词</label><textarea id="exclude-keywords" name="exclude_keywords" rows="3" placeholder="填写确定不相关的业务词">${esc(list(settings.exclude_keywords).join('，'))}</textarea><small>排除词可能降低相关线索的优先级，请谨慎使用宽泛词语。</small></div><div class="form-field full"><label for="preferred-provinces">优先区域</label><input id="preferred-provinces" name="preferred_provinces" value="${esc(list(settings.preferred_provinces).join('，'))}" placeholder="如：江苏，浙江，广东；留空关注全国"><small>使用逗号分隔；填写省份或直辖市名称。</small></div><div class="form-field"><label for="min-score">推荐最低分</label><input type="number" id="min-score" name="min_score" min="0" max="100" required value="${Number(settings.min_score ?? 55)}"><small>用于公共资讯优先级统计与分数筛选，个人订阅按自身条件匹配。</small></div></div><div class="form-footer"><button class="primary" type="submit">保存推荐策略</button><span id="settings-feedback" class="result-note" role="status">修改后需点击保存。</span></div></form><div class="panel settings-form"><h3>AI 解读配置</h3><p class="muted">管理员可在智能化配置中选择本地模型或外部 API，保存后测试连接；表单智能填写与公告解读使用同一配置。未配置时规则推荐继续运行。<a href="#model" data-view="model">打开智能化配置 ↗</a></p></div>`;
  mountCompanyDirectory(version).catch(error=>toast(error.message,true));
  $('#settings-form').onsubmit = async event => { event.preventDefault(); const button = $('button[type=submit]',event.target), form = new FormData(event.target), split = key => String(form.get(key) || '').split(/[,，;；\n]+/).map(s => s.trim()).filter(Boolean); button.disabled = true; button.textContent = '正在保存…'; try { await api('/api/settings',{method:'PUT',body:JSON.stringify({focus_keywords:split('focus_keywords'),exclude_keywords:split('exclude_keywords'),preferred_provinces:split('preferred_provinces'),min_score:Number(form.get('min_score'))})}); state.defaultMinScore = Number(form.get('min_score')); if ($('#settings-feedback')) $('#settings-feedback').textContent = `已保存 · ${businessNow()?.toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})||'北京时间待校准'}`; toast('推荐策略已保存。'); } catch(error) { toast(error.message,true); } finally { button.disabled = false; button.textContent = '保存推荐策略'; } };
}
async function renderReport(version) {
  const report = await api('/api/report'); if (version !== state.renderVersion) return;
  $('#content').innerHTML = `<div class="report-title"><p class="eyebrow">产业情报</p><h2>产业商机情报日报</h2><p>生成时间 ${esc(dateText(report.generated_at))}</p></div><div class="panel"><div class="panel-header"><h2>今日观察</h2><button id="print-report" class="secondary report-print">打印 / 保存 PDF</button></div><p style="white-space:pre-wrap">${esc(asText(report.summary))}</p></div>${list(report.warnings).length ? `<div class="warning-box"><strong>需要留意</strong><ul class="report-list">${list(report.warnings).map(w => `<li>${esc(asText(w))}</li>`).join('')}</ul></div>` : ''}<div class="section-heading"><h2>优先关注</h2><span class="muted">${report.opportunities?.length || 0} 条精选线索</span></div><div class="opportunity-list">${report.opportunities?.length ? report.opportunities.map(card).join('') : empty('本期暂无符合条件的有效商机','系统仍将持续采集；可到推荐策略调整关注方向，或查看商机池中的观察线索。')}</div>`;
  $('#print-report').onclick = () => window.print();
}
function resultsAnchor() {
  return state.view==='pushes'?'.push-table, #content .empty':state.view==='followups'?'.followups-table, #content .empty':state.view==='team-dashboard'?(state.teamTab==='followups'?'.team-followups-table, #content .empty':'.team-table, #content .empty'):state.view==='today'?'.opportunity-list':'.table-wrap, #content .empty';
}
function scrollToTarget(target) {
  const element=typeof target==='string'?$(target):target;if(!element)return;
  const offset=Math.max(72,Math.min(130,window.innerHeight*.15));
  const top=Math.max(0,window.scrollY+element.getBoundingClientRect().top-offset);
  const nearby=Math.abs(top-window.scrollY)<window.innerHeight*.8;
  window.scrollTo({top,behavior:nearby&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches?'smooth':'instant'});
}
async function loadView(intent={}) {
  const transition=state.assistQueryTransition,context=transition?.context;
  const keepQuery=!!context&&context.owner===state.user?.id&&context.session===state.sessionGeneration&&context.render===state.renderVersion&&context.view===state.view;
  if(transition&&!keepQuery){transition.progress.stop();state.assistQueryTransition=null;}
  if(!intent.automaticRefresh&&!keepQuery)closePopups();
  if (['opportunities','companies','report'].includes(state.view)) {state.newsTab=state.view;state.view='today';}
   if(state.view==='recommendations')state.view='pushes';
   if(state.view==='users'){state.view='account';state.accountTab=isAdmin()?'team':'profile';history.replaceState(null,'',`#account?tab=${state.accountTab}`);}
   if(state.view==='account'&&!isAdmin())state.accountTab='profile';
   if (['subscriptions','subscription-status','automation'].includes(state.view)) { state.configTab=state.view==='automation'?'automation':'subscriptions';state.view='configuration';history.replaceState(null,'',`#configuration?tab=${state.configTab}`); }
   if (!viewMeta[state.view]) state.view='today';
  if (['users','model','settings','team-dashboard'].includes(state.view) && !isAdmin()) {state.view='today';history.replaceState(null,'','#today');}
  $('#collect').hidden=state.view!=='today'||state.user?.role!=='admin';
  const headingStatus=$('#today-collection-status');if(headingStatus){headingStatus.hidden=true;headingStatus.innerHTML='';}
  const key=state.view+(state.view==='configuration'?':'+state.configTab:state.view==='account'?':'+(state.accountTab||'profile'):state.view==='team-dashboard'?':'+state.teamTab:''),previousY=window.scrollY;
  state.viewScroll ||= {};
  if(state.renderedViewKey)state.viewScroll[state.renderedViewKey]=previousY;
  const restoreY=intent.navigation?state.viewScroll[key]:previousY;
  const content=$('#content');content.style.minHeight=Math.ceil(content.getBoundingClientRect().height)+'px';
  content.inert=true;content.setAttribute('aria-busy','true');content.classList.remove('view-arrive');content.classList.add('is-loading');
  $('.page').classList.toggle('form-page',['settings','model','account','users'].includes(state.view));
  state.reuseDashboard=!!intent.anchor&&!intent.navigation;
  const version = ++state.renderVersion, metadata = !isAdmin()&&regularViewMeta[state.view]||viewMeta[state.view]; $('#breadcrumb').textContent = metadata[0]; $('#page-title').textContent = metadata[0]; $('#page-description').textContent = metadata[3];
  document.querySelectorAll('[data-view]').forEach(button => { const active=!button.hidden&&button.dataset.view===state.view;button.classList.toggle('active',active);if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current'); });
  if(!content.children.length)content.innerHTML=loading();
  if(['today','pushes','followups','team-dashboard'].includes(state.view)){
    const owner=state.user?.id,pending=[...followupSaves.values()].filter(entry=>entry.owner===owner);
    if(pending.length)await Promise.allSettled(pending.map(entry=>entry.promise));
    if(version!==state.renderVersion||owner!==state.user?.id)return;
  }
   let viewError=null;
   try { await ({today:renderToday,configuration:renderConfiguration,automation:renderAutomation,'subscription-status':renderSubscriptionStatus,pushes:renderPushes,followups:renderFollowups,'team-dashboard':renderTeamDashboard,opportunities:renderOpportunities,companies:renderCompanies,report:renderReport,sources:renderMonitoringSources,settings:renderSettings,subscriptions:renderTopicConfig,recommendations:renderRecommendations,users:renderUsers,model:renderModel,account:renderAccountConfig}[state.view])(version); } catch(error) {viewError=error;if (version === state.renderVersion) $('#content').innerHTML = errorPanel(error); }
  if(version!==state.renderVersion)return;
  await new Promise(resolve=>requestAnimationFrame(resolve));if(version!==state.renderVersion)return;
  content.style.minHeight='';content.inert=false;content.removeAttribute('aria-busy');content.classList.remove('is-loading');content.classList.add('view-arrive');
  if(intent.anchor)scrollToTarget(intent.anchor);
  else if(restoreY!=null)window.scrollTo({top:restoreY,behavior:'instant'});
  else if(intent.navigation)scrollToTarget('.page-heading');
  state.renderedViewKey=key;
  if(keepQuery&&state.assistQueryTransition===transition){
    state.assistQueryTransition=null;
    if(viewError)transition.progress.fail(viewError.message);else transition.progress.finish();
    if(context.dialog?.open)context.dialog.close();
    toast(viewError?'查询未完成：'+viewError.message:'查询完成，结果已更新。',!!viewError);
  }
}
function detailList(values, fallback) { const items = list(values); return items.length ? `<ul class="detail-list">${items.map(value => `<li>${esc(asText(value))}</li>`).join('')}</ul>` : `<p class="muted">${esc(fallback)}</p>`; }
function extractionCoverageMarkup(item) {
  const coverage=item.content_coverage || (item.content ? 'complete' : 'list_only');
  const warning=String(item.extraction_warning || '').trim();
  const incomplete=coverage==='partial' || coverage==='list_only';
  if(!incomplete && item.pdf_truncated!==true && !warning)return '';
  const count=value=>Number.isInteger(value) && value>=0 ? String(value) : '待核实';
  const hasPdf=item.pdf_truncated===true || ['pdf_pages_total','pdf_pages_read','pdf_ocr_pages'].some(key=>item[key]!=null);
  const explanation=coverage==='list_only' ? '当前仅取得列表线索，尚未取得完整正文。' : incomplete ? '当前仅取得部分内容，缺失部分须查阅来源原文。' : '以下为采集与识别注意事项，请对照来源原文核实。';
  const limit=item.pdf_truncated===true && Number.isInteger(item.pdf_page_limit) && item.pdf_page_limit>0 ? `处理范围最多前 ${item.pdf_page_limit} 页。` : '';
  const failed=Array.isArray(item.pdf_failed_pages) ? item.pdf_failed_pages.filter(page=>Number.isInteger(page) && page>0) : [];
  const pages=hasPdf ? `PDF 采集：已读取 ${count(item.pdf_pages_read)} / 总 ${count(item.pdf_pages_total)} 页；已 OCR ${count(item.pdf_ocr_pages)} 页。${limit}${failed.length ? `未成功读取页号：${failed.join('、')}。` : ''}` : '采集范围：待核实。';
  const url=safeUrl(item.url);
  return `<div class="warning-box"><strong>${incomplete ? '内容采集不完整' : '采集内容说明'}</strong><p>${explanation}${warning ? ` ${esc(warning)}` : ''}</p><p>${pages}</p><p>未取得的日期、主体、预算等信息均待核实。${url ? ` <a href="${esc(url)}" target="_blank" rel="noopener noreferrer">查看来源原文 ↗</a>` : ' 原文链接待核实。'}</p></div>`;
}
function renderDetail(item) {
  if (item.business_context && item.topic_match) {
    const score=item.topic_match.score;
    item={...item,category:item.business_context.industry || item.business_context.name,score,priority:score>=75?'高':score>=50?'中':'低',reasons:item.topic_match.reasons,
      summary:`${item.title}。公告阶段：${item.stage}。${item.actionable?'已取得未截止证据，仍需核实参与资格。':'参与期限或有效性待核实，详见下方风险。'}`,
      risks:list(item.risks).filter(r=>!['未确认焚烧行业场景','与核心解决方案关联较弱','属于相邻工业场景，需确认是否在本企业获客范围内','常规采购可作客户线索，但未显示智慧焚烧或提标改造需求'].includes(r))};
  }
  state.detail = item; const url = safeUrl(item.url), ai = item.ai_analysis,canAnalyze=isAdmin()||!!item.business_context; let aiHtml;
  if (ai && typeof ai === 'object') aiHtml = `<p>${esc(ai.summary || '暂无摘要')}</p>${ai.fit ? `<p><strong>业务契合：</strong>${esc(asText(ai.fit))}</p>` : ''}<h3 style="margin-top:17px">建议核实的问题</h3>${detailList(ai.questions,'暂无补充问题。')}<h3>建议行动</h3>${detailList(ai.suggested_actions,'暂无补充行动。')}<p class="muted">模型 ${esc(ai.model || '未记录')} · ${esc(dateText(ai.generated_at))}</p>`;
  else aiHtml = `<p class="muted">${ai ? esc(asText(ai)) : canAnalyze?'点击「生成 AI 解读」获取进一步的业务契合分析与核实问题。未配置模型时，系统会提示当前状态。':'暂无公共 AI 解读，可先查看公告事实与原文。公共解读由管理员统一生成。'}</p>`;
  const evidence = item.evidence && typeof item.evidence === 'object' ? Object.entries(item.evidence) : [];
  $('#drawer-content').innerHTML = `${item.business_context?`<div class="signal-strip"><span>获客专题：<strong>${esc(item.business_context.name)}</strong></span><span>${esc(item.business_context.offering || '请在订阅中完善业务能力')}</span></div>`:''}<div class="card-tags">${tag(item.category || '其他')}${tag(item.stage || '阶段待核实')}${tag(`${Number(item.score || 0)} 分 · ${item.priority || '待研判'}优先级`,'dark')}${tag(item.actionable ? '可跟进' : '观察线索',item.actionable ? 'dark' : 'gold')}</div><h2 id="detail-title">${esc(item.title || '商机详情')}</h2>${extractionCoverageMarkup(item)}<div class="detail-actions">${url ? `<a class="primary" href="${esc(url)}" target="_blank" rel="noopener noreferrer">查看来源原文 ↗</a>` : '<span class="muted">原文链接待核实</span>'}<span class="muted">${esc(item.source_name || '来源待核实')}</span></div>${teamFollowupPanel(item)}<dl class="detail-meta"><div><dt>采购单位</dt><dd>${esc(item.buyer || '待核实')}</dd></div><div><dt>项目区域</dt><dd>${esc([item.province,item.city].filter(Boolean).join(' · ') || '待核实')}</dd></div><div><dt>发布时间</dt><dd>${esc(sourceTime(item.published_at,item.published_at_precision))}</dd></div><div><dt>截止时间</dt><dd>${esc(item.deadline ? sourceTime(item.deadline,item.deadline_precision) : '未识别，需查阅原文')}</dd></div><div><dt>文件获取 / 报名截止</dt><dd>${esc(item.document_deadline ? sourceTime(item.document_deadline,item.document_deadline_precision) : '未取得，须核对参与条件')}</dd></div><div><dt>金额 / 预算</dt><dd>${esc(item.budget_text || '未披露或待核实')}</dd></div><div><dt>首次采集</dt><dd>${esc(dateText(item.created_at))}</dd></div><div><dt>最近更新</dt><dd>${esc(dateText(item.updated_at))}</dd></div></dl><section class="panel"><h3>项目摘要（规则生成）</h3><p>${esc(item.summary || '暂无摘要，请核实原文。')}</p><h3 style="margin-top:18px">推荐依据（规则评分）</h3>${detailList(item.reasons,'暂无评分依据。')}</section><section class="panel"><h3>风险与下一步</h3><p class="muted">风险与建议由规则从已采集内容中提取，缺失信息仍需查阅原文核实。</p>${detailList(item.risks,'暂无已识别风险，不代表项目没有风险。')}<h3 style="margin-top:18px">建议下一步</h3>${detailList(item.next_actions,'先查阅完整招采文件，确认项目阶段、资格与截止时间。')}</section><section class="panel"><h3>匹配的目标企业</h3>${list(item.matched_companies).length ? `<ul class="detail-list">${list(item.matched_companies).map(c => `<li>${esc(typeof c === 'object' ? c.name || '名称待核实' : c)}${typeof c === 'object' && c.province ? ` <span class="muted">${esc(c.province)}</span>` : ''}</li>`).join('')}</ul><p class="muted">名称匹配仅供定位，需核实与采购单位的实际关系。</p>` : '<p class="muted">暂未匹配名录企业，可通过目标企业库进一步查找。</p>'}</section><section class="panel"><h3>事实证据</h3><p class="muted">事实证据由规则从已采集内容中提取，未识别的信息保持待核实。</p>${evidence.length ? `<dl class="evidence-list">${evidence.map(([key,value]) => `<div><dt>${esc(key)}</dt><dd>${esc(asText(value))}</dd></div>`).join('')}</dl>` : '<p class="muted">暂无结构化证据，详见来源原文。</p>'}</section>${projectAnalysisPanel(item)}<section class="panel"><details${ai||!canUseProjectAI(item)?' open':''}><summary>${item.business_context?'专题 AI 解读':'公共 AI 解读'}</summary><div class="panel-header"><h3>${item.business_context?'专题 AI 解读':'公共 AI 解读'}</h3>${canAnalyze?`<button id="analyze-detail" class="secondary">${ai ? '重新分析' : '生成 AI 解读'}</button>`:''}</div>${aiHtml}<p class="muted">AI 分析仅供辅助，事实与资格条件以原文为准。</p></details></section><section class="panel"><h3>我的研判与备注</h3><p id="followup-privacy-note" class="muted">${isMyTeamFollowup(item)?'你是当前负责人，保存状态会更新团队公开阶段；备注仍仅自己可见。':'个人状态与备注仅自己可见，保存不会自动成为团队负责人。'}</p><form id="followup-form"><div class="form-field" style="margin-top:15px"><label for="followup-status" id="followup-status-label">${isMyTeamFollowup(item)?'我的公开跟进阶段（团队可见）':'我的个人研判状态（仅自己）'}</label><select id="followup-status" name="status">${statuses.map(s => `<option${s === (item.status || '待研判') ? ' selected' : ''}>${esc(s)}</option>`).join('')}</select></div><div class="form-field" style="margin-top:15px"><label for="followup-note">跟进备注</label><textarea id="followup-note" name="note" rows="4" placeholder="记录核实结果、联系人与下一步安排">${esc(item.note || '')}</textarea></div><button type="submit" class="primary" style="margin-top:18px">保存跟进记录</button><span id="followup-feedback" class="result-note" role="status"></span></form></section><section class="panel"><details><summary>查看已采集正文</summary><div class="original-content">${esc(item.content || '暂无正文，请访问来源原文。')}</div></details></section>`;
  $('#followup-form').onsubmit = async event => {
    event.preventDefault();if(detailActionBusy(item.id))return;
    const formNode=event.target,button=$('button[type=submit]',formNode),form=new FormData(formNode),version=state.drawerVersion,owner=state.user?.id,id=item.id;
    const current=()=>state.drawerVersion===version&&state.user?.id===owner&&String(state.detail?.id)===String(id)&&formNode.isConnected&&$('#followup-form')===formNode&&!$('#drawer-backdrop').hidden;
    const unlock=lockDetailControls(formNode);button.textContent='正在保存…';
    const request=api(`/api/opportunities/${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify({status:form.get('status'),note:form.get('note')})});state.detailSavePromise=request;trackFollowupSave(owner,id,request);
    try {
      const saved=await request;if(!current()||String(saved.id)!==String(id))return;
      applyFollowupFields(item,saved);refreshTeamFollowupPanel(item);refreshProjectAnalysisPanel({...item,project_analysis:saved.project_analysis});
      $('#followup-status').value=saved.status;$('#followup-note').value=saved.note;state.detailSaved=true;
      $('#followup-feedback').textContent='  已保存';toast('跟进记录已保存。');
    }catch(error){if(current())toast(error.message,true);}
    finally{if(state.detailSavePromise===request)state.detailSavePromise=null;unlock();if(button.isConnected)button.textContent='保存跟进记录';}
  };
  const analyzeButton=$('#analyze-detail');if(analyzeButton)analyzeButton.onclick=async event=>{
    if(detailActionBusy(item.id))return;
    const button=event.currentTarget,version=state.drawerVersion,owner=state.user?.id,formNode=$('#followup-form'),draftStatus=$('#followup-status')?.value,draftNote=$('#followup-note')?.value,unlock=lockDetailControls(formNode);
    state.detailAnalysisBusy=true;button.textContent='正在分析…';
    const progressNode=document.createElement('div');progressNode.className='public-ai-progress';button.closest('.panel-header').after(progressNode);
    const progress=AITaskProgress.mount(progressNode,{completeLabel:'解读完成'});
    try{
      const topic=state.detailTopic,session=state.sessionGeneration,current=()=>version===state.drawerVersion&&owner===state.user?.id&&session===state.sessionGeneration&&state.detail?.id===item.id;
      const result=await runAIJob({kind:topic?'topic_analysis':'public_analysis',opportunity_id:item.id,...(topic?{subscription_id:Number(topic)}:{})},{current,progressTarget:progress,onStatus:job=>{if(current()&&button.isConnected)button.textContent=job.status==='queued'?'已排队…':'正在分析…';}});
      if(!result||!current()){progress.stop();return;}
      progress.stage('presenting');
      const updated=await api(`/api/opportunities/${encodeURIComponent(item.id)}${topic?`?subscription_id=${encodeURIComponent(topic)}`:''}`);
      if(!current()){progress.stop();return;}
      renderDetail(updated);$('#analyze-detail')?.closest('.panel-header').after(progressNode);progress.finish();
      if(draftStatus!=null)$('#followup-status').value=draftStatus;if(draftNote!=null)$('#followup-note').value=draftNote;toast('分析已完成。');
    }catch(error){if(version===state.drawerVersion&&owner===state.user?.id){progress.fail(error.message);toast(error.message,true);}else progress.stop();}
    finally{if(version===state.drawerVersion&&owner===state.user?.id)state.detailAnalysisBusy=false;unlock();if(button.isConnected)button.textContent=ai?'重新分析':'生成 AI 解读';}
  };
  bindTeamFollowup(item);bindProjectAnalysis();scheduleProjectPoll();syncFollowupAIPermission();
}
async function openDetail(id,topicId='') { const owner=state.user?.id,pending=pendingFollowupSave(owner,id);state.detailTopic=topicId;state.detail=null;state.detailSaved=false;state.detailSavePromise=null;state.detailAnalysisBusy=false; const version = ++state.drawerVersion; state.focusBeforeDrawer = document.activeElement; $('#drawer-backdrop').hidden = false; document.body.style.overflow = 'hidden'; $('#drawer-content').innerHTML = loading('正在加载项目详情…'); $('#drawer').focus({preventScroll:true}); try { if(pending){try{await pending.promise;}catch{/* Reopen the last saved record after a failed save. */}}if(version!==state.drawerVersion||owner!==state.user?.id)return;const item = await api(`/api/opportunities/${encodeURIComponent(id)}${topicId?`?subscription_id=${encodeURIComponent(topicId)}`:''}`); if (version === state.drawerVersion&&owner===state.user?.id) {renderDetail(item);await acknowledgeDisplayedAssignment(item,{owner,sessionGeneration:state.sessionGeneration,drawerVersion:version,id:item.id,owner_revision:item.team_followup?.owner_revision});} } catch(error) { if (version === state.drawerVersion) $('#drawer-content').innerHTML = `<div class="empty"><h3>详情加载失败</h3><p>${esc(error.message)}</p></div>`; } }
async function closeDrawer() {
  clearTimeout(state.projectAnalysisPoll);
  const opened=!$('#drawer-backdrop').hidden,item=state.detail,origin=state.focusBeforeDrawer,owner=state.user?.id,pendingEntry=pendingFollowupSave(state.user?.id,state.detail?.id),pending=pendingEntry?.promise||state.detailSavePromise,view=state.view,focusAtClose=document.activeElement;
  const queryForm=view==='today'?'#opportunity-filters':view==='pushes'?'#push-filters':view==='followups'?'#followup-filters':view==='team-dashboard'&&state.teamTab==='followups'?'#team-followup-filters':'',queryDraft=queryForm?$(queryForm)?.querySelector('[name=q]'):null;
  const saved=state.detailSaved;
  const identity=origin?{detail:origin.dataset.detail,topic:origin.dataset.topic||'',push:origin.dataset.pushOpen,text:origin.textContent}:null;
  $('#drawer-backdrop').hidden=true;document.body.style.overflow='';const closedVersion=++state.drawerVersion;state.detail=null;state.detailSaved=false;state.detailSavePromise=null;
  if(!opened)return;
  if(item&&!$('#shell').hidden){
    if(saved&&view==='followups')state.followupPage=1;
    const renderVersion=state.renderVersion+1;
    if(pending)pending.then(result=>{if(String(result.id)===String(item.id)&&state.user?.id===owner&&state.drawerVersion===closedVersion&&state.renderVersion===renderVersion&&state.view===view&&view==='followups')state.followupPage=1;},()=>{});
    await loadView({automaticRefresh:true});
    if(state.renderVersion!==renderVersion||state.drawerVersion!==closedVersion||state.user?.id!==owner||state.view!==view)return;
    if(queryDraft){const query=$(queryForm)?.querySelector('[name=q]');if(query)query.value=queryDraft.value;}
    const active=document.activeElement;
    if(!$('#account-dropdown').hidden||document.querySelector('dialog[open]')||!(active===document.body||active===focusAtClose||active===origin||active?.closest?.('#drawer')))return;
    const candidates=[...document.querySelectorAll('[data-detail],[data-push-open]')].filter(node=>identity&&(identity.push?node.dataset.pushOpen===identity.push:node.dataset.detail===identity.detail&&(node.dataset.topic||'')===identity.topic));
    (candidates.find(node=>node.textContent===identity?.text)||candidates[0])?.focus({preventScroll:true});
  }else if(origin?.isConnected)origin.focus({preventScroll:true});
}
window.addEventListener('hashchange',()=>{
  if(!state.user)return;
  const target=location.hash.replace('#','').split('?')[0];
  if(['configuration','subscription-status','subscriptions','automation'].includes(target)){const tab=target==='automation'||target==='configuration'&&new URLSearchParams(location.hash.split('?')[1]||'').get('tab')==='automation'?'automation':'subscriptions';if(target!=='configuration')history.replaceState(null,'',`#configuration?tab=${tab}`);if(state.view!=='configuration'||state.configTab!==tab){state.view='configuration';state.configTab=tab;loadView({navigation:true});}return;}
  if(target==='followups'){if(state.view!=='followups'){state.view='followups';loadView({navigation:true});}return;}
  if(target==='team-dashboard'&&isAdmin()){const tab=new URLSearchParams(location.hash.split('?')[1]||'').get('tab')==='followups'?'followups':'subscriptions';if(state.view!==target||state.teamTab!==tab){state.view=target;state.teamTab=tab;loadView({navigation:true});}return;}
  if(['users','account'].includes(target)){const tab=isAdmin()&&(target==='users'||new URLSearchParams(location.hash.split('?')[1]||'').get('tab')==='team')?'team':'profile';if(target==='users')history.replaceState(null,'',`#account?tab=${tab}`);if(state.view!=='account'||state.accountTab!==tab){state.view='account';state.accountTab=tab;loadView({navigation:true});}return;}
  if(isAdmin())return;
  if(target==='sources'){if(state.view!=='sources'){state.view='sources';loadView({navigation:true});}return;}
  if(!['settings','model','users','team-dashboard'].includes(target))return;
  state.view='today';history.replaceState(null,'','#today');loadView({navigation:true});
});
async function enterApp() { const session=++state.sessionGeneration,user=await api('/api/me');if(session!==state.sessionGeneration)return;state.user=user;state.topicQuery='';state.topicQueryDraft='';if(state.localPages)state.localPages.topics=1;if(state.sizes)delete state.sizes.topics;restoreFilters();resetFollowupState();resetTeamDashboardState();state.strategyPromise=null;state.dashboard=null;state.page=1;state.collection=null;state.collectionSchedule=null;$('#management-nav-label').textContent=isAdmin()?'系统管理':'个人设置'; document.querySelectorAll('[data-admin]').forEach(x=>x.hidden=state.user.role!=='admin');document.querySelectorAll('[data-regular]').forEach(x=>x.hidden=isAdmin()); $('#collect').hidden=state.user.role!=='admin'; $('#identity').textContent=state.user.display_name+' · '+state.user.username; $('#avatar').textContent=state.user.display_name.slice(0,1).toUpperCase(); $('#login').hidden = true; $('#shell').hidden = false; const hash = location.hash.replace('#','').split('?')[0]; if (viewMeta[hash]) state.view = hash; const route=new URLSearchParams(location.hash.split('?')[1]||'');state.accountTab=hash==='users'||route.get('tab')==='team'?'team':'profile'; if(hash==='pushes'){state.pushTopicId=route.get('topic')||route.get('subscription_id')||'';state.pushQuery=route.get('q')||'';state.pushRead=route.get('read')||'';state.pushSort=route.get('sort')||'pushed_at';state.pushFacets=Object.fromEntries([...route].filter(([key])=>!['q','read','sort','topic','subscription_id','page','page_size'].includes(key)));state.pushPage=Number(route.get('page')||1);}if(hash==='today'){Object.assign(state.filters,Object.fromEntries([...route].filter(([key])=>!['tab','page','page_size'].includes(key))));state.page=Number(route.get('page')||1);}if(hash==='followups'){Object.assign(state.followupFilters,Object.fromEntries([...route].filter(([key])=>!['page','page_size'].includes(key))));state.followupPage=Number(route.get('page')||1);} if(hash==='configuration')state.configTab=route.get('tab')==='automation'?'automation':'subscriptions';if(hash==='team-dashboard')state.teamTab=route.get('tab')==='followups'?'followups':'subscriptions'; await syncBusinessClock(); await loadView(); startNotificationPolling(); if(isAdmin()){const owner=state.user.id;api('/api/runs').then(result => { if (isAdmin()&&state.user.id===owner&&session===state.sessionGeneration&&['running','queued','运行中'].includes(result.items?.[0]?.status)) startCollectionPolling(); }).catch(() => {});} }
updateBusinessDateLabel();
$('#login-form').onsubmit = async event => { event.preventDefault(); const button = $('button[type=submit]',event.target); button.disabled = true; button.textContent = '正在验证…'; $('#login-error').textContent = ''; try { await api('/api/login',{method:'POST',body:JSON.stringify({username:$('#username').value,password:$('#password').value})}); $('#password').value = ''; await enterApp(); } catch(error) { $('#login-error').textContent = error.message; } finally { button.disabled = false; button.innerHTML = '进入工作台 <span>→</span>'; } };
$('#logout').onclick = async () => { try { await api('/api/logout',{method:'POST'}); showLogin(); } catch(error) { toast(error.message,true); } };
$('#collect').onclick = () => collect(); $('#close-drawer').onclick = closeDrawer;
$('#drawer-backdrop').onclick = event => { if (event.target === $('#drawer-backdrop')) closeDrawer(); };
document.addEventListener('keydown', event => { if ($('#drawer-backdrop').hidden || document.querySelector('dialog[open]')) return; if (event.key === 'Escape') closeDrawer(); if (event.key === 'Tab') { const focusable = [...$('#drawer').querySelectorAll('button:not(:disabled),a[href],input,select,textarea,summary')]; const first = focusable[0], last = focusable.at(-1); if (event.shiftKey && (document.activeElement === first || document.activeElement === $('#drawer'))) { event.preventDefault(); last?.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); } } });
document.addEventListener('click', event => { const nav = event.target.closest('[data-view]'); if (nav) { event.preventDefault();if(nav.dataset.view==='pushes'){state.pushTopicId='';state.pushPage=1;state.pushQuery='';state.pushRead='';state.topicSearch='';state.topicExpanded=false;} state.view = nav.dataset.view;if(nav.dataset.configTarget)state.configTab=nav.dataset.configTarget;if(nav.dataset.view==='account')state.accountTab=nav.dataset.accountTab||state.accountTab||'profile';history.replaceState(null,'',state.view==='configuration'?`#configuration?tab=${state.configTab}`:state.view==='team-dashboard'?`#team-dashboard?tab=${state.teamTab}`:state.view==='account'&&isAdmin()?`#account?tab=${state.accountTab}`:`#${state.view}`); loadView({navigation:true,anchor:nav.dataset.navAnchor||(nav.dataset.configTarget?'.config-tabs':undefined)}); } const detail = event.target.closest('[data-detail]'); if (detail) openDetail(detail.dataset.detail,detail.dataset.topic || ''); const page = event.target.closest('[data-page]'); if (page && !page.disabled) { if (page.dataset.pageType === 'companies') state.companyPage = Number(page.dataset.page); else if (page.dataset.pageType==='recommendations') state.recommendationPage=Number(page.dataset.page); else if(page.dataset.pageType==='pushes')state.pushPage=Number(page.dataset.page); else if(page.dataset.pageType==='followups')state.followupPage=Number(page.dataset.page); else if(page.dataset.pageType==='team-dashboard')state.teamPage=Number(page.dataset.page); else if(page.dataset.pageType==='team-followups')state.teamFollowupPage=Number(page.dataset.page); else state.page = Number(page.dataset.page); loadView({anchor:resultsAnchor()}); } if (event.target.closest('[data-action="retry"]')) loadView(); });
async function bootstrapApp() { let session=state.sessionGeneration;try { const me = await api('/api/me');if(session!==state.sessionGeneration)return;if(me.authenticated===false)showLogin();else {const entering=enterApp();session=state.sessionGeneration;await entering;} } catch {if(session===state.sessionGeneration)showLogin();} }
// Defer scripts run while the document is interactive; wait for every view helper.
if(document.readyState==='complete')bootstrapApp();
else document.addEventListener('DOMContentLoaded',bootstrapApp,{once:true});
