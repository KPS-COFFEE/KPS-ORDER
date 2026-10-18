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
