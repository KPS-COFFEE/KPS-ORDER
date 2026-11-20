const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/customer.js','utf8');
const queue=source.split('\n').find(line=>line.includes('function queueQuote()'));
function setup(service,neighborhood){const fields={neighborhood:{value:neighborhood},'customer-name':{value:'Synthetic customer'}};
  const calls=[];const ctx={state:{service,location:{lat:28,lng:45},branchId:'b1',cart:[{}],session:{user:null},quoteRevision:0},
    mapLink:{pending:false},$:(id)=>fields[id]||(fields[id]={value:''}),t:s=>s,persistLocationDraft(){},
    renderQuote(){},refreshQuote(){},clearTimeout(){},setTimeout(){calls.push('quote');return 1;}};
  vm.createContext(ctx);vm.runInContext(queue,ctx);ctx.queueQuote();return {ctx,calls};}
for(const service of ['delivery','pickup','car']){
  const empty=setup(service,'');
  if(service==='delivery'){
    assert.equal(empty.ctx.state.quoteError,'enter_a_clear_delivery_neighborhood_name');assert.equal(empty.calls.length,0);
    const valid=setup(service,'حي اختبار');assert.equal(valid.calls.length,1,'blank optional description must not block quotation');
  }else assert.notEqual(empty.ctx.state.quoteError,'enter_a_clear_delivery_neighborhood_name');
}
const html=fs.readFileSync('public/index.html','utf8');
assert.match(html, /data-i18n="required">مطلوب<\/span><input id="neighborhood"/);
assert.match(html, /data-i18n="optional">اختياري<\/span><input id="address"/);
console.log('PASS: actual customer quote guard requires neighborhood only for delivery and optional description stays blank.');
