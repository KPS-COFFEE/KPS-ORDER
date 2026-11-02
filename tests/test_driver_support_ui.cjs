const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const s=fs.readFileSync('public/ops.js','utf8');
const block=s.slice(s.indexOf('  function driverSupportButton('),s.indexOf('  async function managerHandover('));
let html,submit,writes=[];
const pending={id:'request1',group_id:'g1',driver_shift_id:'driver1',driver_name:'Yasin',status:'pending',from_user_id:'source',to_user_id:'target',can_approve:false,can_withdraw:true,reason:'Rush hour',operational_snapshot:{order_ids:['o1'],source_manager_cash_halalas:9900,drivers:[{shift_id:'driver1',cash_halalas:17550,expected_card_halalas:48550}]}};
const ctx={state:{user:{id:'target'}},tr:(a,e)=>e,esc:String,num:String,money:n=>(n/100).toFixed(2),datetime:String,shiftLabel:String,can:()=>true,notice:String,empty:String,check:()=>'',textarea:()=>'',formEnd:()=>'',dataItem:(k,v)=>`${k}:${v}`,table:(h,r)=>r.flat().join('|'),
 ownManagerShift:rows=>rows.find(x=>x.user_id===ctx.state.user.id),api:async path=>path.includes('shifts')?{shifts:[{id:'target-duty',user_id:'target',status:'open'}]}:{requests:[pending]},
 modal:(t,h,f)=>{html=h;submit=f;},mutation:async(p,b)=>{writes.push([p,b]);},closeModal:()=>{},loadPage:async()=>{}};
vm.createContext(ctx);vm.runInContext(block,ctx);
(async()=>{
 await ctx.requestDriverSupport('driver1');await submit(new Map([['reason','Rush hour']]));
 assert.equal(writes[0][1].target_manager_shift_id,'target-duty');assert.equal(writes[0][1].driver_shift_id,'driver1');
 assert.match(html,/Withdraw request/);assert.doesNotMatch(html,/data-action="accept-driver-support"/);
 ctx.state.user.id='approver';pending.can_approve=true;pending.can_withdraw=false;await ctx.driverSupportInbox();assert.match(html,/Review and transfer driver/);assert.doesNotMatch(html,/Withdraw request/);
 await ctx.acceptDriverSupport('request1');assert.match(html,/175.50/);assert.match(html,/485.50/);assert.match(html,/does not transfer/);
 await assert.rejects(()=>submit(new Map()),/Confirm/);assert.equal(writes.length,1);
 await submit(new Map([['accepted','on']]));assert.equal(writes[1][0],'/api/driver-support/request1/accept');
 await ctx.resolveDriverSupport('request1','reject');await submit(new Map([['reason','Current workload']]));assert.equal(writes[2][1].action,'reject');
 console.log('PASS: support requester/delegated approver controls, explicit acceptance, balances preview and rejection.');
})().catch(e=>{console.error(e);process.exitCode=1;});
