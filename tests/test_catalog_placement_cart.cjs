const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/customer.js','utf8');
function extract(start,end){const a=source.indexOf(start),b=source.indexOf(end,a);assert.ok(a>=0&&b>a);return source.slice(a,b);}
const alias={'kps-02':{product_id:'kps-05',options:{},original_name:'قهوة اليوم بارد',kind:'placement_alias'}};
const product={id:'kps-05',name:'قهوة اليوم بارد',price_halalas:550,available:true,enabled:true,source_verified:true,option_groups:[]};
function context(cart){
  let sequence=0;
  const notices=[];
  const ctx={state:{brand:{id:'kps'},phoneRequired:false,cart:[],products:[product],legacyItemMappings:alias,
    branchId:'branch-a',branchCatalogRevision:0,service:'pickup',branches:[{id:'branch-a',name:'Branch A'}],session:{user:{id:'customer'}}},
    localStorage:{getItem:()=>JSON.stringify({brandId:'kps',cart})},basketStorageKey:'test',basketGeneration:'',
    key:()=>String(++sequence),toast:value=>notices.push(value),t:value=>value,groups:p=>p.option_groups||[],
    requestedBrandId:'kps',URLSearchParams,document:{querySelector:()=>({value:'cash'})},window:{},$:()=>({value:''}),
    renderProducts(){},refreshProductDialog(){},renderCart(){},queueQuote(){},renderQuote(){},
    renderBranches(){},renderService(){},cartChanged(){},closeDialog(){},showBasket(){},
    errorMessage:String,showError(_id,error){throw Error(error);},sourceText:p=>p.name,money:String,
    node:(_tag,_class,text)=>({text,append(){}})};
  vm.createContext(ctx);
  vm.runInContext(extract('  function restoreDraft() {','  function syncMobileBasket()'),ctx);
  vm.runInContext(extract('  function productFor(','  function linePrice('),ctx);
  vm.runInContext(source.split('\n').find(line=>line.startsWith('  function orderBody(')),ctx);
  vm.runInContext(extract('  async function loadBranchCatalog() {','  const motionPreference='),ctx);
  vm.runInContext(extract('  async function prepareReorder(id){','  function bindEvents() {'),ctx);
  ctx.restoreDraft();ctx.notices=notices;return ctx;
}
test('saved promotional and ordinary rows retain independent server quote identities',()=>{
  const ctx=context([{productId:'kps-02',quantity:2,options:{},note:''},
    {productId:'kps-05',quantity:3,options:{},note:''}]);
  assert.deepEqual(Array.from(ctx.state.cart,row=>[row.productId,row.quantity]),[['kps-02',2],['kps-05',3]]);
  assert.equal(ctx.productFor(ctx.state.cart[0]).enabled,true);
  assert.deepEqual(Array.from(ctx.orderBody().items,row=>row.product_id),['kps-02','kps-05']);
  assert.deepEqual(ctx.notices,[]);
});
test('same-identity restoration combines exact selections without losing quantity or instructions',()=>{
  const ctx=context([{productId:'kps-02',quantity:2,options:{},note:''},
    {productId:'kps-02',quantity:3,options:{},note:''},
    {productId:'kps-02',quantity:1,options:{},note:'بدون ثلج'}]);
  assert.equal(ctx.state.cart.length,2);assert.equal(ctx.state.cart[0].quantity,5);
  assert.equal(ctx.state.cart[1].note,'بدون ثلج');
  const limited=context([{productId:'kps-02',quantity:20,options:{},note:''},
    {productId:'kps-02',quantity:20,options:{},note:''}]);
  assert.equal(limited.state.cart.length,2);assert.equal(limited.state.cart.reduce((n,row)=>n+row.quantity,0),40);
});
test('branch transition replaces alias metadata and keeps a stopped source stopped in the quote',async()=>{
  const ctx=context([{productId:'kps-02',quantity:2,options:{},note:'keep'}]);
  ctx.api=async()=>({products:[product],legacy_item_mappings:alias,source_verified:true});
  await ctx.loadBranchCatalog();assert.equal(ctx.productFor(ctx.state.cart[0]).available,true);
  ctx.state.branchId='branch-b';
  ctx.api=async()=>({products:[product,{...product,id:'kps-02',available:false,unavailable_reason:'Stopped source'}],legacy_item_mappings:{},source_verified:true});
  await ctx.loadBranchCatalog();
  assert.equal(Object.keys(ctx.state.legacyItemMappings).length,0);
  assert.equal(ctx.productFor(ctx.state.cart[0]).available,false);
  assert.equal(ctx.orderBody().items[0].product_id,'kps-02');
  assert.equal(ctx.orderBody().items[0].quantity,2);
});
for(const equivalent of [true,false])test(`reorder uses fresh catalog and preserves source identity (${equivalent?'equivalent':'divergent stock'})`,async()=>{
  const ctx=context([]),messages=[];
  ctx.state.legacyItemMappings=alias; // Deliberately stale bootstrap metadata.
  ctx.node=(_tag,_class,text)=>{messages.push(text);return {append(){}};};
  ctx.linePrice=(_line,p)=>p.price_halalas||0;ctx.cartOptionsAvailable=()=>true;
  ctx.api=async path=>path.startsWith('/api/orders/')?{order:{branch_id:'branch-a',service:'pickup',snapshot:{items:[{product_id:'kps-02',quantity:2,name:'Original cold daily',note:'keep'}]}}}:
    {products:equivalent?[product]:[product,{...product,id:'kps-02',available:false}],legacy_item_mappings:equivalent?alias:{}};
  ctx.confirmAction=value=>{ctx.confirmation=value;};
  await ctx.prepareReorder('order');
  assert.equal(messages.includes('reorder_unavailable'),!equivalent);
  await ctx.confirmation.action();
  assert.equal(ctx.state.cart[0].productId,'kps-02');assert.equal(ctx.state.cart[0].quantity,2);
  assert.equal(ctx.orderBody().items[0].product_id,'kps-02');
  assert.equal(ctx.productFor(ctx.state.cart[0]).available,equivalent);
  assert.equal(Object.keys(ctx.state.legacyItemMappings).length,equivalent?1:0);
});
