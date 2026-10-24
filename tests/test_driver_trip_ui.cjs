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
(async()=>{for(const lang of ['ar','en']){await scenario(null,lang);await scenario({id:'trip-1',departure_branch_id:'a',departed_at:'2026-10-18T13:00:00Z'},lang);}console.log('PASS: AR/EN picked-up own-order departure, scoped actual return, pending-return persistence, canonical mutation payloads.');})().catch(e=>{console.error(e);process.exitCode=1;});
