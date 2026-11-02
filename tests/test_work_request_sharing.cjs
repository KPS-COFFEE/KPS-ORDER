'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {test}=require('node:test');
const {webcrypto}=require('node:crypto');
const source=fs.readFileSync('public/ops.js','utf8');
const apiSource=source.slice(source.indexOf('  const centralOperations='),source.indexOf('  async function mutation('));
function setup({recorded=false}={}){
  const calls=[],pending=[],storage=new Map();let sequence=0;
  const state={user:{id:'SYNTHETIC-driver',brand_id:'kps',role:'driver',branch_ids:['b1'],effective_permissions:['tasks_view']},csrf:'synthetic-csrf',requestKeys:new Map()};
  const ctx={state,activePageLoad:null,window:{},morningMode:false,operationsBrand:null,TextEncoder,
    pageLoadScope:()=>JSON.stringify([state.user?.id,state.user?.branch_ids,state.user?.effective_permissions]),
    crypto:{subtle:webcrypto.subtle,randomUUID:()=>`SYNTHETIC-replay-${++sequence}`},
    sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    tr:(_ar,en)=>en,apiError:()=>'',location:{},
    fetch:(path,options)=>{calls.push({path,options});if(path.startsWith('/api/operations/replay-status?'))return Promise.resolve({ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>({recorded})});return new Promise((resolve,reject)=>{pending.push({resolve:data=>resolve({ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>data}),reject});});}};
  vm.createContext(ctx);vm.runInContext(apiSource,ctx);
  return {ctx,state,calls,pending,storage};
}
const settle=()=>new Promise(r=>setTimeout(r,15));
test('simultaneous task reads use one HTTP request and independent response rows',async()=>{
  const t=setup(),one=t.ctx.api('/api/tasks'),two=t.ctx.api('/api/tasks');
  assert.equal(t.calls.length,1);
  t.pending[0].resolve({tasks:[{id:'synthetic',status:'assigned'}]});
  const [a,b]=await Promise.all([one,two]);a.tasks[0].status='local-only';
  assert.equal(b.tasks[0].status,'assigned');
  const fresh=t.ctx.api('/api/tasks');assert.equal(t.calls.length,2,'Completed responses are never cached');
  t.pending[1].resolve({tasks:[]});await fresh;
});
test('double submission shares one transport; lost response retries with original durable key',async()=>{
  const t=setup(),options={method:'POST',body:{cash_halalas:20000,reference:'SYNTHETIC'}};
  const one=t.ctx.api('/api/shifts/synthetic/approve',options),two=t.ctx.api('/api/shifts/synthetic/approve',options);
  await settle();assert.equal(t.calls.length,1);
  const key=t.calls[0].options.headers['Idempotency-Key'];
  t.pending[0].reject(Error('Synthetic lost response'));
  const failed=await Promise.allSettled([one,two]);assert(failed.every(x=>x.status==='rejected'));
  assert.equal(t.storage.size,2);
  const retry=t.ctx.api('/api/shifts/synthetic/approve',options);await settle();
  assert.equal(t.calls[1].options.headers['Idempotency-Key'],key);
  t.pending[1].resolve({approved:true,replayed:true});assert.equal((await retry).approved,true);
  assert.equal(t.storage.size,0);
});
test('brands and replacement sessions never share requests',async()=>{
  const t=setup(),one=t.ctx.api('/api/tasks',{brand:'kps'}),two=t.ctx.api('/api/tasks',{brand:'enbishan'});
  assert.equal(t.calls.length,2);t.state.csrf='replacement-session';
  const three=t.ctx.api('/api/tasks',{brand:'kps'});assert.equal(t.calls.length,3);
  t.pending.forEach(p=>p.resolve({tasks:[]}));await Promise.all([one,two,three]);
});
test('page-owned cancellation never cancels an independent read',async()=>{
  const t=setup(),one=t.ctx.api('/api/tasks',{signal:{synthetic:'one'}}),two=t.ctx.api('/api/tasks');
  assert.equal(t.calls.length,2);t.pending.forEach(p=>p.resolve({tasks:[]}));await Promise.all([one,two]);
});
for(const mode of ['hidden','offline','pending'])test(`operational alerts stop before reads when work is ${mode}`,async()=>{
  const start=source.indexOf('  async function refreshOperationalAlerts(){'),end=source.indexOf('  async function refreshDriverAlerts(',start);
  const ctx={document:{hidden:mode==='hidden',documentElement:{hasAttribute:()=>mode==='pending'}},navigator:{onLine:mode!=='offline'}};
  vm.createContext(ctx);vm.runInContext(source.slice(start,end),ctx);
  // Any further work depends on private state absent from this context, so a
  // passing call proves the actual guard exits before even reading its scope.
  await ctx.refreshOperationalAlerts();
});


test('unsafe credential bodies are never persisted or recovered after reload',async()=>{
  const t=setup(),path='/api/shifts/synthetic/approve';
  const one=t.ctx.api(path,{method:'POST',body:{cash_halalas:10000,password:'synthetic-never-save'}});await settle();
  t.pending[0].reject(Error('Lost after commit'));await assert.rejects(one);
  await assert.rejects(t.ctx.api(path,{method:'POST',body:{cash_halalas:9000,password:'synthetic-never-save'}}),e=>e.code==='UNCONFIRMED_OPERATION');
  assert.equal(t.calls.filter(c=>c.path===path).length,1);
  t.state.requestKeys.clear(); // A reload loses memory, but keeps sessionStorage.
  await assert.rejects(t.ctx.api(path,{method:'POST',body:{cash_halalas:9000,password:'synthetic-never-save'}}),e=>e.code==='UNCONFIRMED_OPERATION');
  assert.equal(t.calls.filter(c=>c.path===path).length,1);
  const retry=t.ctx.api(path,{method:'POST',body:{cash_halalas:10000,password:'synthetic-never-save'}});await settle();
  assert.equal(t.calls.filter(c=>c.path===path)[1].options.headers['Idempotency-Key'],t.calls[0].options.headers['Idempotency-Key']);
  t.pending[1].resolve({saved:true});await retry;
});


test('recorded status recovers a lost reply after reload without sending edited cash',async()=>{
  const t=setup({recorded:true}),path='/api/shifts/synthetic/approve';
  const one=t.ctx.api(path,{method:'POST',body:{cash_halalas:10000}});await settle();
  t.pending[0].reject(Error('Lost after commit'));await assert.rejects(one);t.state.requestKeys.clear();
  await assert.rejects(t.ctx.api(path,{method:'POST',body:{cash_halalas:9000}}),e=>e.code==='OPERATION_PREVIOUSLY_RECORDED');
  assert.equal(t.calls.filter(c=>c.path===path).length,1);
  assert.equal(t.calls.filter(c=>c.path.startsWith('/api/operations/replay-status?')).length,1);
  assert.equal(t.storage.size,0);
});


test('unrecorded status resumes only original draft/key after OS reload, then stops edited action',async()=>{
  const t=setup(),path='/api/shifts/synthetic/cash-event',original={amount_halalas:12345,reference:'SYNTHETIC'};
  const one=t.ctx.api(path,{method:'POST',body:original});await settle();
  t.pending[0].reject(Error('Request lost before server'));await assert.rejects(one);t.state.requestKeys.clear();
  const recovery=t.ctx.api(path,{method:'POST',body:{amount_halalas:12000,reference:'SYNTHETIC'}});await settle();
  const writes=t.calls.filter(c=>c.path===path);assert.equal(writes.length,2);
  assert.equal(writes[1].options.body,JSON.stringify(original));
  assert.equal(writes[1].options.headers['Idempotency-Key'],writes[0].options.headers['Idempotency-Key']);
  t.pending[1].resolve({saved:true});
  await assert.rejects(recovery,e=>e.code==='OPERATION_PREVIOUSLY_RECORDED');
  assert.equal(t.storage.size,0);
});
