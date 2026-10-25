const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const src=fs.readFileSync('public/ops.js','utf8');
const fn=src.slice(src.indexOf('  async function driverAttributionCorrection(id){'),src.indexOf('  async function testVoidOrder(id){'));
function setup(overrides={}){
 const preview={order:{id:'o',version:7},source_task:{id:'t'},source_shift:{id:'from',display_name:'Amr'},collections:[{amount_halalas:5050}],target_shifts:[{id:'to',display_name:'Ibrahim'}],...overrides};
 const seen={calls:[]};
 const ctx={state:{user:{role:'owner'}},api:async()=>preview,modal:(title,html,submit)=>Object.assign(seen,{title,html,submit}),mutation:async(...args)=>seen.calls.push(args),closeModal:()=>{},loadPage:async()=>{},tr:(ar,en)=>en,esc:String,money:String,orderLabel:()=> 'Order',shiftLabel:()=> 'Shift',notice:String,selectOptions:()=>'',textarea:(label,name,value)=>value,check:()=>'',formEnd:()=>''};
 vm.createContext(ctx);vm.runInContext(fn+';this.run=driverAttributionCorrection;',ctx);return {ctx,seen};
}
test('correction requires actual driver confirmation and retains preview identifiers',async()=>{
 const {ctx,seen}=setup();await ctx.run('o');assert.match(seen.html,/بأمر المالك/);
 const data=new Map([['target_shift_id','to'],['reason','بأمر المالك']]);
 await assert.rejects(seen.submit(data,null,{}),/Confirm/);assert.equal(seen.calls.length,0);
 data.set('confirmed','on');await seen.submit(data,null,{});
 assert.deepEqual(JSON.parse(JSON.stringify(seen.calls[0][1])),{source_task_id:'t',source_shift_id:'from',target_shift_id:'to',expected_version:7,reason:'بأمر المالك',confirmed:true});
});
test('blocked or previously corrected orders offer no mutation',async()=>{
 for(const value of [{blocked_reason:'Closed shift'},{existing_correction:{id:'done'}},{target_shifts:[]}]){const {ctx,seen}=setup(value);await ctx.run('o');assert.equal(seen.submit,undefined);assert.equal(seen.calls.length,0);}
});
test('non-owner cannot open correction action',async()=>{const {ctx,seen}=setup();ctx.state.user.role='delivery_manager';await assert.rejects(ctx.run('o'),/owner-only/);assert.equal(seen.html,undefined);});
