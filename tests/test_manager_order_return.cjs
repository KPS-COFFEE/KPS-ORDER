const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','public','ops.js'),'utf8');
const fn=source.slice(source.indexOf('  async function customerOrder('),source.indexOf('  async function channelSettlement('));
for(const brand of ['kps','enbishan'])for(const page of ['dashboard','orders']){
  test(`${brand}/${page}: successful order refreshes current view and restores position`,async()=>{
    const calls=[];let opened;
    const context=vm.createContext({state:{page,user:{id:'manager',brand_id:brand},branches:[]},
      api:()=>{},mutation:()=>{},tr:(ar,en)=>en,esc:String,money:String,modal:()=>{},closeModal:()=>{},
      loadBranches:async()=>{},loadPage:async silent=>calls.push(['refresh',silent]),
      orderDetail:()=>assert.fail('Do not open another dialog after saving'),
      setStatus:message=>calls.push(['status',message]),
      $:()=>({focus:opts=>calls.push(['focus',opts.preventScroll])}),
      window:{scrollX:0,scrollY:480,requestAnimationFrame:cb=>cb(),scrollTo:opts=>calls.push(['scroll',opts.top]),
        KPSCustomerOrder:{open:async ctx=>{opened=ctx;}}}});
    vm.runInContext(fn,context);await context.customerOrder();
    assert.equal(opened.user.brand_id,brand);assert.equal(calls.length,0);
    await opened.onCreated('order-example');
    assert.deepEqual(calls,[['refresh',true],['status','Order confirmed: order-example'],['focus',true],['scroll',480]]);
    assert.equal(context.state.page,page);
  });
}
test('order callback cannot refresh or scroll a replacement user session',async()=>{
  let opened;
  const fail=()=>assert.fail('Must not touch another session');
  const context=vm.createContext({state:{page:'dashboard',user:{id:'manager'},branches:[]},
    api:fail,mutation:fail,tr:fail,esc:fail,money:fail,modal:fail,closeModal:fail,
    loadBranches:async()=>{},loadPage:fail,setStatus:fail,$:fail,
    window:{scrollX:0,scrollY:200,KPSCustomerOrder:{open:async ctx=>{opened=ctx;}}}});
  vm.runInContext(fn,context);await context.customerOrder();
  context.state.user={id:'another-user'};await opened.onCreated('saved-order');
});
