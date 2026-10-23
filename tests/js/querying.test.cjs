const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
function env(){const context={URLSearchParams,JSON,esc:x=>String(x).replaceAll('<','&lt;'),tag:x=>x,FormData:class{constructor(form){this.form=form;}get(key){return this.form.values[key]??null;}}};vm.createContext(context);const filename=path.join(__dirname,'../../app/static/querying.js');if(fs.existsSync(filename))vm.runInContext(fs.readFileSync(filename,'utf8'),context);return context;}
test('active terms replace q in list and export parameters without losing dates',()=>{
 const context=env();const params=context.queryToParams({q:'燃烧',terms:'["消防","烟气"]',published_from:'2026-10-14',term_mode:'all'});
 assert.equal(params.has('q'),false);assert.equal(params.get('terms'),'["消防","烟气"]');assert.equal(params.get('published_from'),'2026-10-14');assert.equal(params.get('term_mode'),'all');
});
test('manual keyword edits clear only old terms and keep exact date facets',()=>{
 const context=env(),terms={value:'["燃烧"]'},form={values:{q:'消防',terms:'["燃烧"]',published_from:'2026-10-14'},dataset:{},addEventListener(type,handler){this.handler=handler;},querySelector(){return terms;}};
 context.bindQueryFacets(form);form.handler({target:{name:'q'}});form.values.terms=terms.value;
 const fields=context.readQueryFacets(form);
 assert.equal(fields.terms,'');assert.equal(fields.published_from,'2026-10-14');assert.equal(fields.q,'消防');
});
