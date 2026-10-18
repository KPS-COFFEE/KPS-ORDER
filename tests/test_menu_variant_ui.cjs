const assert=require('node:assert/strict');
const test=require('node:test');
const pricing=require('../public/assets/menu-variants.js');
test('selection uses the exact size/flavour price and before price',()=>{
  const product={brand_id:'enbishan',price_halalas:1300,option_groups:[{code:'SIZE'},{code:'FLAVOUR'}],variant_price_matrix:[
    {options:{SIZE:'S',FLAVOUR:'B'},price_halalas:1300,compare_at_halalas:1500},
    {options:{SIZE:'L',FLAVOUR:'B'},price_halalas:1700,compare_at_halalas:1900}]};
  assert.equal(pricing.selected(product,{SIZE:'L',FLAVOUR:'B'}).price_halalas,1700);
  assert.equal(pricing.selected(product,{SIZE:'S',FLAVOUR:'B'}).compare_at_halalas,1500);
  assert.equal(pricing.starting(product).compare_at_halalas,1500);
  assert.equal(pricing.selected(product,{SIZE:'S'}),null);
  assert.equal(pricing.selected(product,{SIZE:'L',FLAVOUR:'UNLISTED'}),null);
  assert.equal(pricing.selected({...product,brand_id:'kps'},{SIZE:'L',FLAVOUR:'B'}),null);
});
test('basket and reorder estimates use the supplied current catalog price',()=>{
  const source=require('node:fs').readFileSync(require('node:path').join(__dirname,'../public/customer.js'),'utf8');
  const body=source.slice(source.indexOf('  function linePrice('),source.indexOf('  function draftSubtotal('));
  const stale={brand_id:'enbishan',price_halalas:1300,option_groups:[{code:'SIZE'}],
    variant_price_matrix:[{options:{SIZE:'L'},price_halalas:1700}]};
  const current={...stale,variant_price_matrix:[{options:{SIZE:'L'},price_halalas:1850}]};
  const price=require('node:vm').runInNewContext(body+';linePrice',{
    KPSMenuVariants:pricing,productFor:()=>stale,groups:p=>p.option_groups||[]});
  const line={options:{SIZE:'L'}};
  assert.equal(price(line),1700);
  assert.equal(price(line,current),1850);
  assert.equal(price(line,{brand_id:'kps',price_halalas:1000,option_groups:[
    {code:'SIZE',values:[{code:'L',price_delta_halalas:250}]}]}),1250);
});
