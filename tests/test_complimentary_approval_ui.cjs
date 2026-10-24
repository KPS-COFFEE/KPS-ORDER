const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
(async()=>{
  let click,rendered='',created,closed=false;
  const error={textContent:''};
  const panel={addEventListener:(type,handler)=>{assert.equal(type,'click');click=handler;},querySelector:()=>error};
  const context={window:{},document:{querySelector:()=>panel},console};
  vm.runInNewContext(fs.readFileSync('public/assets/customer-order.js','utf8'),context);
  const payload={kind:'completion',branch_id:'branch',brand_id:'kps',customer:{name:'Recipient',phone:'0500000000',address:'Address'},items:[{product_id:'paid-item',quantity:1,units:[]}],original_order_id:'original',original_invoice_number:'INV-1',reason:'Missing item'};
  let submitted;
  const ctx={complimentary:true,user:{role:'delivery_manager'},branches:[{id:'branch',name:'Branch'}],tr:(ar,en)=>en,esc:String,
    api:async path=>{assert.equal(path,'/api/orders/complimentary/authorizations');return {authorizations:[{id:'approved-1',branch_id:'branch',payload}]};},
    modal:(title,body)=>{rendered=body;},closeModal:()=>{closed=true;},
    mutation:async(path,body)=>{assert.equal(path,'/api/orders/complimentary');submitted=body;return {order:{id:'new'}};},
    onCreated:async id=>{created=id;}};
  await context.window.KPSCustomerOrder.open(ctx);
  assert.match(rendered,/Order completion/);
  assert.doesNotMatch(rendered,/Missing item|INV-1/);
  const button={dataset:{courtesyIndex:'0'},disabled:false};
  await click({target:{closest:()=>button}});
  assert.deepEqual(JSON.parse(JSON.stringify(submitted)),{...payload,authorization_id:'approved-1',expected_total_halalas:0});
  assert.equal(created,'new');assert.equal(closed,true);
  console.log('Complimentary inbox executes the exact authorized payload.');
})().catch(error=>{console.error(error);process.exitCode=1;});
