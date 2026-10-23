// Administrator company directory: import summary on the strategy page.
function fileBase64(file) {
  return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]||'');reader.onerror=()=>reject(new Error('文件读取失败'));reader.readAsDataURL(file);});
}
function companySummaryText(s) {
  return s.total?`当前名录 <strong>${Number(s.total)}</strong> 家企业，覆盖 ${Number(s.provinces)} 个省份${s.source_file?`，来源文件 ${esc(s.source_file)}`:''}；已有 <strong>${Number(s.matched_notices)}</strong> 条公告精确匹配到名录企业。`:'当前尚未导入企业名录，公告不会进行名录企业匹配，“目标企业”显示为 0。';
}
async function mountCompanyDirectory(version) {
  if(!isAdmin())return;
  const summary=await api('/api/companies/summary');if(version!==state.renderVersion||$('#company-directory'))return;
  $('#content').insertAdjacentHTML('beforeend',`<section id="company-directory" class="panel settings-form compact-form"><div class="panel-header"><div><h2>企业名录</h2><p class="muted">导入垃圾焚烧厂等目标企业名录。导入会整体替换现有名录，并重新计算已入库公告的名录匹配、采购主体与区域推断。</p></div></div><p id="company-summary">${companySummaryText(summary)}</p><div class="form-grid"><div class="form-field full"><label for="company-file">名录文件（.xlsx / .csv / .json，最大10MB）</label><input id="company-file" type="file" accept=".xlsx,.csv,.json"><p class="muted">表头需包含“企业名称”（或公司名称、焚烧厂名称），可选：所属/隶属集团、省份、城市、地址、焚烧炉数/规模、投产/投运日期、联系人/信息公开负责人、联系电话；其他列忽略。联系人信息不会发送给模型。</p></div></div><button type="button" id="company-import" class="primary">导入并替换名录</button><span id="company-feedback" class="result-note" role="status"></span></section>`);
  $('#company-import').onclick=async()=>{
    const file=$('#company-file').files[0],button=$('#company-import'),feedback=$('#company-feedback');
    if(!file){feedback.textContent='请先选择名录文件';return;}
    if(file.size>10_000_000){feedback.textContent='文件超过10MB';return;}
    if(summary.total&&!confirm(`将用「${file.name}」替换现有 ${summary.total} 家企业名录？`))return;
    button.disabled=true;feedback.textContent='正在导入并重新计算全部公告的名录匹配，约需1～3分钟，请勿关闭页面…';
    try{const result=await api('/api/companies/import',{method:'POST',body:JSON.stringify({filename:file.name,content_base64:await fileBase64(file)})});
      Object.assign(summary,result);$('#company-summary').innerHTML=companySummaryText(result);feedback.textContent=`已导入 ${result.imported} 家企业`;toast('企业名录已导入');}
    catch(error){feedback.textContent=error.message;toast(error.message,true);}
    finally{button.disabled=false;}
  };
}
