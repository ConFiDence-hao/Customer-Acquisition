'use strict';
const queryFacetKeys=['terms','term_mode','published_from','published_to','deadline_state','pushed_from','pushed_to'];
function queryToParams(fields){
 const values={...fields};if(Array.isArray(values.terms))values.terms=JSON.stringify(values.terms);
 if(values.terms&&values.terms!=='[]')delete values.q;else{delete values.terms;delete values.term_mode;}
 return new URLSearchParams(Object.fromEntries(Object.entries(values).filter(([,value])=>value!==''&&value!==false&&value!==null&&value!==undefined)));
}
function readQueryFacets(form){
 const values=new FormData(form),fields={q:values.get('q')||''};
 for(const key of queryFacetKeys)fields[key]=values.get(key)||'';
 if(fields.terms&&fields.terms!=='[]')fields.q='';
 if(!fields.term_mode)fields.term_mode='any';
 if(!form.querySelector('[name=pushed_from]')){delete fields.pushed_from;delete fields.pushed_to;}
 return fields;
}
function bindQueryFacets(form){
 if(form.dataset.queryFacetsReady)return;form.dataset.queryFacetsReady='true';
 form.addEventListener('input',event=>{if(event.target.name==='q'&&!form.dataset.assistApplying){const terms=form.querySelector('[name=terms]');if(terms)terms.value='';}});
}
function queryFacetFields(fields,push=false,{inline=false,showTags=true}={}){
 let terms=fields.terms||'';if(Array.isArray(terms))terms=JSON.stringify(terms);
 const active=!!(terms&&terms!=='[]'||fields.published_from||fields.published_to||fields.deadline_state||fields.pushed_from||fields.pushed_to);
 return `<input type="hidden" name="terms" value="${esc(terms)}">${inline?'<div class="query-advanced inline-query-facets">':`<details class="query-advanced"${active?' open':''}><summary>更多查询条件 <span aria-hidden="true">⌄</span></summary>`}<div class="filter-row"><label>词语关系<select name="term_mode" aria-label="关键词关系"><option value="any"${fields.term_mode!=='all'?' selected':''}>任意词命中</option><option value="all"${fields.term_mode==='all'?' selected':''}>全部词命中</option></select></label><label>公告发布起日<input type="date" name="published_from" value="${esc(fields.published_from||'')}"></label><label>公告发布止日<input type="date" name="published_to" value="${esc(fields.published_to||'')}"></label><label>截止条件<select name="deadline_state" aria-label="截止条件">${[['','全部截止情况'],['open','明确未截止'],['eligible','还能报名 · 证据齐全'],['expired','已截止或已结束'],['unknown','截止时间待核实']].map(([value,label])=>`<option value="${value}"${value===(fields.deadline_state||'')?' selected':''}>${label}</option>`).join('')}</select></label>${push?`<label>收到推送起日<input type="date" name="pushed_from" value="${esc(fields.pushed_from||'')}"></label><label>收到推送止日<input type="date" name="pushed_to" value="${esc(fields.pushed_to||'')}"></label>`:''}</div>${inline?'</div>':'</details>'}${showTags?queryFacetTags(fields):''}`;
}
function queryFacetTags(fields){
 const values=[];let terms=fields.terms;
 if(typeof terms==='string'){try{terms=JSON.parse(terms);}catch{terms=terms.split(/[,，]/);}}
 if(terms?.length)values.push(`${fields.term_mode==='all'?'全部词':'任意词'}：${terms.join('、')}`);
 if(fields.published_from||fields.published_to)values.push(`公告发布：${fields.published_from||'不限'} 至 ${fields.published_to||'不限'}`);
 if(fields.pushed_from||fields.pushed_to)values.push(`收到推送：${fields.pushed_from||'不限'} 至 ${fields.pushed_to||'不限'}`);
 const labels={open:'明确未截止',eligible:'还能报名 · 证据齐全',expired:'已截止或已结束',unknown:'截止时间待核实'};
 if(fields.deadline_state)values.push(labels[fields.deadline_state]);
 return values.length?`<div class="keyword-tags query-condition-tags" aria-label="当前查询条件">${values.map(value=>tag(value)).join('')}</div>`:'';
}
function queryRoute(view,fields,page){const params=queryToParams({...fields,page});history.replaceState(null,'',`#${view}${params.size?'?'+params:''}`);}
