const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const block=source.slice(source.indexOf('  function financialCasesButton('),source.indexOf('  async function channelSettlement('));
let dialog,submit,reads=[],writes=[],status;
const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const ctx={state:{user:{role:'delivery_manager'}},tr:(a,e)=>e,esc,money:n=>(n/100).toFixed(2),num:String,datetime:String,statusName:String,shiftLabel:String,badge:String,can:()=>false,notice:s=>s,empty:s=>s,
 dataItem:(k,v)=>`${k}:${esc(v)}`,table:(h,rows)=>rows.flat().join('|'),textarea:()=>'',check:()=>'',formEnd:()=>'',
 modal:(title,html,handler)=>{dialog=html;submit=handler;},closeModal:()=>{},loadPage:async()=>{},setStatus:s=>status=s,
 api:async(path,opts)=>{reads.push([path,opts]);return {order:{version:7},cases:[{id:'o1',order_no:'O-1',financial_case:{status:'pending',reason:'<script>',pending_amount_halalas:2250,invoice_number:'INV-1'}}]};},
 mutation:async(path,body,button,opts)=>{writes.push([path,body,opts]);return {financial_case:{status:'pending'}};}
};vm.createContext(ctx);vm.runInContext(block,ctx);
(async()=>{
 await ctx.cancelOrderWithJourney('o/1');
 assert.match(dialog,/does not refund/);
 await submit(new Map([['reason','Customer requested cancellation'],['physical_resolution','returned_to_branch']]));
 assert.equal(writes[0][0],'/api/order-financial-cases/orders/o%2F1/cancel');
 assert.equal(writes[0][1].version,7);assert.equal(writes[0][1].physical_resolution,'returned_to_branch');
 assert.match(status,/accounting treatment remains visible/);
 await ctx.financialCases('enbishan');assert.equal(reads.at(-1)[1].brand,'enbishan');
 assert.match(dialog,/22.50/);assert.match(dialog,/INV-1/);assert.match(dialog,/&lt;script>/);
 assert.doesNotMatch(dialog,/data-action="resolve-financial-case"|data-action="case-refund"/);
 ctx.state.user.role='owner';await ctx.financialCases('enbishan');
 assert.match(dialog,/data-action="resolve-financial-case"/);assert.match(dialog,/data-action="case-refund"/);
 await ctx.resolveFinancialCase('o1','enbishan');await submit(new Map([['reason','Actual refund matched']]));
 assert.equal(writes.at(-1)[2].brand,'enbishan');
 assert.match(source,/else if\(a==='reject'\)await cancelOrderWithJourney\(id\)/);
 console.log('PASS: retained cancellation, version/physical attestation, accounting scope and role restrictions.');
})().catch(e=>{console.error(e);process.exitCode=1;});

// Exercise the actual rendered card, including permissions and terminal/payment guards.
const cardContext={...ctx,state:{user:{role:'delivery_manager',brand_id:'kps'},branches:[]},isAdmin:()=>false,isManager:()=>true,can:p=>p==='orders_cancel',
 orderLabel:o=>o.id,orderStatusBadge:()=>'',serviceName:String,branchLabel:()=>'',orderItems:()=>'',customerCarDetails:()=>'',complimentaryName:()=>'',orderAmount:()=>'',badge:()=>''};
vm.createContext(cardContext);
vm.runInContext(source.slice(source.indexOf('  function orderBrandBadge('),source.indexOf('  function performanceBadge(')),cardContext);
const returned={id:'returned-35',brand_id:'kps',service:'delivery',preparation_status:'handed_over',delivery_status:'picked_up',payment_status:'unpaid',snapshot:{delivery_workflow:'manager_dispatch'}};
for(const role of ['owner','delivery_manager']){
 cardContext.state.user.role=role;
 for(const delivery_status of ['picked_up','problem']) assert.match(cardContext.orderCard({...returned,delivery_status}),/Record return and cancel/);
 for(const change of [{delivery_status:'delivered'},{delivery_status:'cancelled'},{preparation_status:'cancelled'},{preparation_status:'completed'},{payment_status:'paid'},{completed_at:'2026-10-26'},{test_void:true}])assert.doesNotMatch(cardContext.orderCard({...returned,...change}),/Record return and cancel/);
}
cardContext.state.user.role='driver';assert.doesNotMatch(cardContext.orderCard(returned),/Record return and cancel/);
cardContext.state.user.role='delivery_manager';cardContext.can=()=>false;assert.doesNotMatch(cardContext.orderCard(returned),/Record return and cancel/);
console.log('PASS: actual return action appears only for authorized, active, picked-up unpaid orders.');
