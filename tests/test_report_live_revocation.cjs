'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync('public/report.js','utf8').split('// A previously opened online report')[1];
function setup({protocol='https:',fetch}={}) {
 const attrs=new Map(),events=new Map(),intervals=[];
 const body={children:['private'],replaceChildren(...nodes){this.children=nodes}};
 const context={location:{protocol},AbortController,setTimeout:()=>1,clearTimeout(){},setInterval:fn=>intervals.push(fn),
  document:{hidden:false,head:{append(){}},body,createElement:()=>({}),documentElement:{setAttribute:(k,v)=>attrs.set(k,v),removeAttribute:k=>attrs.delete(k)},addEventListener:(k,fn)=>events.set(k,fn)},
  window:{addEventListener:(k,fn)=>events.set(k,fn)},fetch:async(...args)=>fetch(...args)};
 vm.createContext(context);vm.runInContext('// A previously opened online report'+code,context);
 return {attrs,events,intervals,body,context};
}
const settle=()=>new Promise(r=>setImmediate(r));
test('a revoked report clears displayed work without an interaction',async()=>{
 let user={id:'staff',role:'driver'};
 const t=setup({fetch:async()=>({ok:true,status:200,json:async()=>({user})})});
 await settle();assert.deepEqual(t.body.children,['private']);
 user=null;await t.intervals[0]();
 assert.equal(t.body.children.length,2);
 assert.equal(t.body.children[1].href,'/ops');
 assert.equal(t.attrs.size,0);
});
test('offline report is masked and cannot reappear under a different employee',async()=>{
 let user={id:'staff',role:'owner'},offline=false;
 const t=setup({fetch:async()=>{if(offline)throw Error('offline');return {ok:true,status:200,json:async()=>({user})}}});
 await settle();offline=true;t.events.get('offline')();await settle();
 assert.ok(t.attrs.has('data-report-access-pending'));
 offline=false;user={id:'other',role:'owner'};t.events.get('online')();await settle();
 assert.equal(t.body.children[1].href,'/ops');
});
test('forbidden live report response erases the original report',async()=>{
 const t=setup({fetch:async()=>({ok:false,status:403})});await settle();
 assert.equal(t.body.children[1].href,'/ops');
});
test('offline document rendering never requests a work session',()=>{
 let calls=0;const t=setup({protocol:'about:',fetch:async()=>{calls++;}});
 assert.equal(calls,0);assert.equal(t.intervals.length,0);
 assert.deepEqual(t.body.children,['private']);
});
