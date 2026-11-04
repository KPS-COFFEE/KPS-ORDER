const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const helper=source.slice(source.indexOf('  async function renderDriverTripPanel'),source.indexOf('  async function driverContact'));
async function scenario(trip,language){
 let host,submit,body,path,reloaded=false;
 const orders={innerHTML:''},button={disabled:false},error={textContent:''};
 const branch={value:'a',addEventListener(){}};
 const form={elements:{branch_id:branch},querySelector(s){return s==='.driver-trip-orders'?orders:s==='.form-error'?error:button;},addEventListener(event,fn){if(event==='submit')submit=fn;}};
 const ctx={state:{user:{id:'d',branch_ids:['a','b']},branches:[{id:'a'},{id:'b'},{id:'forbidden'}]},
  document:{createElement(){host={innerHTML:'',className:'',setAttribute(){},remove(){},querySelector(){return host.innerHTML.includes('<form')?form:null;}};return host;}},
  $(){return {prepend(){}};},tr:(a,b)=>language==='ar'?a:b,esc:String,branchLabel:b=>typeof b==='string'?b:b.id,datetime:String,
  notice:s=>s,orderLabel:o=>o.order_no,api:async()=>({trip}),mutation:async(p,b)=>{path=p;body=b;},setStatus(){},loadPage:async()=>{reloaded=true;},navigator:{},
  FormData:class{get(){return branch.value;}getAll(){return [...orders.innerHTML.matchAll(/name="order_id" value="([^"]+)"/g)].map(m=>m[1]);}}
 };
 vm.createContext(ctx);vm.runInContext(helper,ctx);
 await ctx.renderDriverTripPanel({id:'s',status:'open',branch_ids:['a','b']},trip?[]:[
  {driver_id:'d',shift_id:'s',status:'picked_up',branch_id:'a',order_id:'order-a',order_no:'101'},
  {driver_id:'other',shift_id:'s',status:'picked_up',branch_id:'a',order_id:'foreign',order_no:'SECRET'},
  {driver_id:'d',shift_id:'old',status:'picked_up',branch_id:'a',order_id:'old',order_no:'OLD'},
  {driver_id:'d',shift_id:'s',status:'accepted',branch_id:'a',order_id:'not-picked',order_no:'NOT-PICKED'}]);
 assert.ok(!host.innerHTML.includes('forbidden'));
 if(trip){assert.ok(host.innerHTML.includes(language==='ar'?'بانتظار تأكيد العودة':'Awaiting return confirmation'));branch.value='b';}
 else {assert.ok(orders.innerHTML.includes('101'));assert.ok(!orders.innerHTML.includes('SECRET'));}
 await submit({preventDefault(){}});
 assert.ok(reloaded);assert.equal(error.textContent,'');assert.equal(button.disabled,false);
 if(trip){assert.equal(path,'/api/driver-work-hours/trip-1/return');assert.equal(body.branch_id,'b');assert.equal(body.order_ids,undefined);}
 else {assert.equal(path,'/api/driver-work-hours/depart');assert.equal(body.shift_id,'s');assert.equal(body.order_ids.join(','),'order-a');}
}
async function automaticScenario(language,status){
 let host,submit,path,body;const branch={value:'b',addEventListener(){}};
 const error={textContent:''},button={disabled:false};
 const form={elements:{branch_id:branch},querySelector:q=>q==='.form-error'?error:button,addEventListener:(event,fn)=>{if(event==='submit')submit=fn;}};
 const trip={id:'auto-trip',departure_branch_id:'a',departed_at:'2026-10-29T13:00:00Z',planned_return_branch_id:'b',auto_return:{enabled:true,status,return_branch_id:'b',return_branch_name:'Selected Return Branch',return_location:{lat:28.4,lng:45.9}}};
 const ctx={state:{user:{id:'d',branch_ids:['a','b']},branches:[{id:'a'},{id:'b'},{id:'forbidden'}]},document:{createElement(){host={innerHTML:'',setAttribute(){},remove(){},querySelector(){return host.innerHTML.includes('<form')?form:null;}};return host;}},$(){return {prepend(){}};},tr:(a,b)=>language==='ar'?a:b,esc:String,branchLabel:b=>typeof b==='string'?b:b.id,datetime:String,notice:s=>s,api:async()=>({trip}),mutation:async(p,b)=>{path=p;body=b;},setStatus(){},loadPage:async()=>{},navigator:{},FormData:class{get(){return branch.value;}}};
 vm.createContext(ctx);vm.runInContext(helper,ctx);await ctx.renderDriverTripPanel({id:'s',status:'open',branch_ids:['a','b']},[]);
 assert.ok(host.innerHTML.includes('Selected Return Branch'));
 assert.ok(host.innerHTML.includes('https://www.google.com/maps/dir/?api=1&destination=28.4%2C45.9'));
 assert.ok(host.innerHTML.includes('value="b"'));assert.ok(!host.innerHTML.includes('value="a"'));assert.ok(!host.innerHTML.includes('forbidden'));
 const expected=status==='awaiting_deliveries'?(language==='ar'?'أكمل الطلبات أولًا':'Complete your deliveries first'):status==='confirming_return'?(language==='ar'?'لم تُسجل العودة بعد':'return is not recorded yet'):status==='awaiting_return'?(language==='ar'?'تُسجل العودة تلقائيًا':'Return is recorded automatically'):(language==='ar'?'تعذر تأكيد العودة تلقائيًا':'Automatic return cannot be confirmed');
 assert.ok(host.innerHTML.includes(expected));assert.ok(host.innerHTML.includes('driver-trip-manual'));
 await submit({preventDefault(){}});assert.equal(path,'/api/driver-work-hours/auto-trip/return');assert.equal(body.branch_id,'b');
}
(async()=>{for(const lang of ['ar','en']){for(const status of ['awaiting_deliveries','confirming_return','awaiting_location','awaiting_return','needs_review'])await automaticScenario(lang,status);await scenario(null,lang);await scenario({id:'trip-1',departure_branch_id:'a',departed_at:'2026-10-18T13:00:00Z'},lang);}console.log('PASS: 10 AR/EN automatic return destination, navigation, pending and poor-tracking cases; picked-up own-order departure, scoped actual return, pending-return persistence, canonical mutation payloads.');})().catch(e=>{console.error(e);process.exitCode=1;});
