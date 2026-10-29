'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/customer.js','utf8');
function harness(){
 const requests=[],confirmations=[],errors=[],dialogs=[],timers=new Map();let timerId=0;
 const fields=new Map();const field=id=>{if(!fields.has(id))fields.set(id,{value:'Synthetic',append(){}});return fields.get(id);};
 const state={quote:null,quoteRevision:0,quoteError:'',quotePending:false,session:{user:{id:'verified',brand_id:'enbishan',role:'customer',display_name:'Synthetic'},customer_verified:true,csrf_token:'csrf'},brand:{},cart:[{productId:'p',quantity:1,options:{}}],branchId:'b',service:'pickup',location:{lat:28.4,lng:45.9}};
 let payload={brand_id:'enbishan',branch_id:'b',service:'pickup',payment_method:'cash',customer:{name:'Synthetic',location:{lat:28.4,lng:45.9}},items:[{product_id:'p',quantity:1}],notes:''};
 const context={state,requestedBrandId:'enbishan',JSON,Number,clearTimeout:id=>timers.delete(id),setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},persistLocationDraft(){},renderQuote(){},entryVerified:()=>true,orderBody:()=>structuredClone(payload),api:(path,options)=>new Promise((resolve,reject)=>requests.push({path,options,resolve,reject})),recoverCheckout:async()=>false,catalogReady:()=>true,requiresPhoneVerification:()=>true,renderAccount(){},openDialog:id=>dialogs.push(id),showError:(id,message)=>errors.push({id,message}),t:key=>key,errorMessage:e=>e.message,$:field,node:()=>({append(){}}),branch:()=>({name:'Branch'}),serviceName:()=>'',entryText:(a,b)=>b,productFor:()=>({}),cartLineName:()=>'',number:{format:String},optionLabel:()=>'',renderFirstGift(){},money:String,confirmAction:value=>confirmations.push(value),submitCheckout(){}};
 vm.createContext(context);
 const queue=source.split('\n').find(l=>l.startsWith('  function queueQuote()'));
 const refresh=source.slice(source.indexOf('  let quoteInFlight='),source.indexOf('  function openProduct(',source.indexOf('  let quoteInFlight=')));
 const checkout=source.split('\n').find(l=>l.startsWith('  async function beginCheckout()'));
 vm.runInContext(queue+'\n'+refresh+'\n'+checkout+'\nthis.begin=beginCheckout;this.refresh=refreshQuote;this.queue=queueQuote;',context);
 return {context,state,requests,confirmations,errors,dialogs,timers,setPayload:p=>{payload=p;},payload:()=>structuredClone(payload),settle:()=>new Promise(r=>setImmediate(r))};
}
const quote=()=>({quote:{subtotal_halalas:1200,delivery_fee_halalas:500,total_halalas:1700}});
test('post-SMS checkout cancels pending debounce and joins identical background quote',async()=>{
 const h=harness();h.context.queue();assert.equal(h.timers.size,1);const pending=h.context.begin();await h.settle();assert.equal(h.timers.size,0);const background=h.context.refresh();assert.equal(h.requests.length,1);h.requests[0].resolve(quote());await Promise.all([pending,background]);assert.equal(h.confirmations.length,1);assert.equal(h.state.quote.total_halalas,1700);assert.equal(h.requests[0].path,'/api/gifts/quote');
});
test('checkout joins an already pending current quote rather than invalidating it',async()=>{
 const h=harness();const background=h.context.refresh(),checkout=h.context.begin();await h.settle();assert.equal(h.requests.length,1);h.requests[0].resolve(quote());await Promise.all([background,checkout]);assert.equal(h.confirmations.length,1);
});
for(const kind of ['cart','location','branch','payment'])test(kind+' mutation during quote never confirms the previous payload',async()=>{
 const h=harness(),pending=h.context.begin();await h.settle();const changed=h.payload();if(kind==='cart')changed.items[0].quantity=2;if(kind==='location')changed.customer.location.lat=29;if(kind==='branch')changed.branch_id='different';if(kind==='payment')changed.payment_method='card';h.setPayload(changed);h.context.queue();h.requests[0].resolve(quote());await pending;assert.equal(h.confirmations.length,0);assert.equal(h.state.quote,null);assert.equal(h.errors.at(-1).id,'basket-error');assert.ok(h.errors.at(-1).message);
});
test('payload mutation without a queued revision also cannot confirm stale prices',async()=>{
 const h=harness(),pending=h.context.begin();await h.settle();const changed=h.payload();changed.items[0].quantity=3;h.setPayload(changed);h.requests[0].resolve(quote());await pending;assert.equal(h.confirmations.length,0);assert.equal(h.errors.at(-1).id,'basket-error');
});
test('changed cart starts a new quote and late previous response cannot replace it',async()=>{
 const h=harness(),old=h.context.refresh(),changed=h.payload();changed.items[0].quantity=2;h.setPayload(changed);h.context.queue();const current=h.context.refresh();assert.equal(h.requests.length,2);h.requests[1].resolve({quote:{total_halalas:2900}});await current;h.requests[0].resolve(quote());assert.equal(await old,null);assert.equal(h.state.quote.total_halalas,2900);
});
test('quote failure is visible and never opens confirmation',async()=>{
 const h=harness(),pending=h.context.begin();await h.settle();h.requests[0].reject(new Error('Coverage changed'));await pending;assert.equal(h.confirmations.length,0);assert.equal(h.errors.at(-1).message,'Coverage changed');assert.equal(h.state.quote,null);
});
test('unverified customer still requires SMS and does not request a checkout quote',async()=>{
 const h=harness();h.state.session.customer_verified=false;await h.context.begin();assert.equal(h.requests.length,0);assert.deepEqual(h.dialogs,['account-dialog']);assert.equal(h.confirmations.length,0);assert.equal(typeof h.state.afterLogin,'function');
});

test('SMS verification scope transition never reuses the unverified quote endpoint',async()=>{
 const h=harness();h.context.entryVerified=()=>false;const old=h.context.refresh();h.context.entryVerified=()=>true;const current=h.context.refresh();assert.equal(h.requests.length,2);assert.equal(h.requests[0].path,'/api/quote');assert.equal(h.requests[1].path,'/api/gifts/quote');h.requests[0].resolve(quote());assert.equal(await old,null);h.requests[1].resolve(quote());await current;assert.equal(h.state.quote.total_halalas,1700);
});
