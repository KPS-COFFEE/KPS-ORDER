const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const fn=source.slice(source.indexOf('  async function assign('),source.indexOf('  async function recordNonreceipt('));
(async()=>{
for(const lang of ['ar','en']){
 let captured,write,reads=[];
 const ctx={state:{user:{role:'owner'}},tr:(a,b)=>lang==='ar'?a:b,esc:String,num:String,loadBranches:async()=>{},notice:s=>s,textarea:(l,n)=>`<textarea name="${n}" required></textarea>`,selectOptions:(rows)=>rows.map(x=>x.id).join(),formEnd:s=>s,window:{},api:async path=>{reads.push(path);if(path.includes('/candidates'))return{shifts:[{id:'paused-duty',kind:'driver',status:'open',available:false}]};if(path.includes('recommendations'))return{impact_enabled:false};return{order:{preparation_status:'accepted',snapshot:{}}}},modal:(title,html,submit)=>{captured={html,submit}},mutation:async(path,body)=>{write={path,body};return{}},closeModal:()=>{},loadPage:async()=>{},refreshOperationalAlerts:async()=>{},shiftLabel:s=>s.id};
 vm.createContext(ctx);vm.runInContext(fn+';this.run=assign;',ctx);
 await ctx.run('order-1',null,true);
 assert(reads.includes('/api/owner-driver-interventions/orders/order-1/candidates'));
 assert(captured.html.includes('paused-duty'));assert(captured.html.includes('owner_reason'));
 await captured.submit(new Map([['driver_shift_id','paused-duty'],['owner_reason','Owner approves this single task']]));
 assert.equal(write.path,'/api/owner-driver-interventions/orders/order-1/assign');assert.equal(write.body.resume_for_task,true);assert.equal(write.body.reason,'Owner approves this single task');assert.equal(write.body.driver_shift_id,'paused-duty');
 ctx.state.user.role='delivery_manager';await assert.rejects(()=>ctx.run('order-1',null,true));
}
console.log('PASS: AR/EN owner-only one-task exception uses audited endpoint and retains reason/target; manager denied.');
})().catch(e=>{console.error(e);process.exit(1)});
