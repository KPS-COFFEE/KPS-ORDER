const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/customer.js','utf8');
const begin=source.indexOf('  function restoreDraft() {');
const end=source.indexOf('  function syncMobileBasket()',begin);
assert.ok(begin>=0&&end>begin);
function restore(cart){
  let sequence=0;
  const notices=[];
  const ctx={state:{brand:{id:'kps'},phoneRequired:false,cart:[],products:[{id:'kps-05'}],
    legacyItemMappings:{'kps-02':{product_id:'kps-05',options:{},original_name:'قهوة اليوم بارد',kind:'placement_alias'}}},
    localStorage:{getItem:()=>JSON.stringify({brandId:'kps',cart})},basketStorageKey:'test',basketGeneration:'',
    key:()=>String(++sequence),toast:value=>notices.push(value),t:value=>value};
  vm.createContext(ctx);vm.runInContext(source.slice(begin,end),ctx);ctx.restoreDraft();
  return {cart:JSON.parse(JSON.stringify(ctx.state.cart)),notices};
}
test('saved promotional and ordinary placements restore as one line without losing quantity',()=>{
  const result=restore([{productId:'kps-02',quantity:2,options:{},note:''},
    {productId:'kps-05',quantity:3,options:{},note:''}]);
  assert.equal(result.cart.length,1);assert.equal(result.cart[0].productId,'kps-05');
  assert.equal(result.cart[0].quantity,5);assert.deepEqual(result.notices,[]);
});
test('restoration preserves different instructions and per-line limits',()=>{
  const result=restore([{productId:'kps-02',quantity:20,options:{},note:''},
    {productId:'kps-05',quantity:20,options:{},note:''},
    {productId:'kps-02',quantity:1,options:{},note:'بدون ثلج'}]);
  assert.equal(result.cart.length,3);assert.equal(result.cart.reduce((n,row)=>n+row.quantity,0),41);
  assert.equal(result.cart[2].note,'بدون ثلج');
});
