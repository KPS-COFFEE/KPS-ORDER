'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('public/ops.js','utf8');
const section=source.slice(source.indexOf('  let permissionRefreshBusy=false;'),source.indexOf('  new MutationObserver(hideUnavailableActions)'));
function harness(){
  const nodes=new Map(),attrs=new Set(),events={},intervals=[],timers=[],requests=[],ownerDashboardCalls=[],productSalesCalls=[];
  const ownerDashboardController={pause(){ownerDashboardCalls.push('pause');},reset(){ownerDashboardCalls.push('reset');}};
  const productSalesController={pause(){productSalesCalls.push('pause');},reset(){productSalesCalls.push('reset');}};
  class Node{constructor(tag){this.tag=tag;this.children=[];this.hidden=false;this.inert=false;this.listeners={};this.isConnected=true;this.textContent='';}append(...xs){this.children.push(...xs);for(const x of xs)if(x.id)nodes.set('#'+x.id,x);}setAttribute(k,v){this[k]=v;}addEventListener(k,v){this.listeners[k]=v;}focus(){this.focused=true;}showModal(){this.open=true;}close(){this.open=false;}replaceChildren(){this.children=[];}}
  const shell=new Node('main');shell.children=[{value:'unsaved input',scrollTop:137}];nodes.set('#app-shell',shell);nodes.set('#modal-body',new Node('div'));nodes.set('#modal',new Node('dialog'));
  const document={hidden:false,createElement:t=>new Node(t),body:new Node('body'),querySelectorAll:()=>[],documentElement:{setAttribute:k=>attrs.add(k),removeAttribute:k=>attrs.delete(k),hasAttribute:k=>attrs.has(k)},addEventListener:(k,v)=>events[k]=v};
  const state={user:{id:'employee',effective_permissions:['dispatch'],central_operations:false},csrf:'original',requestKeys:new Map(),chatHistory:new Map()};
  const context={document,state,ownerDashboardController,productSalesController,productSalesController,trialMode:false,window:{addEventListener:(k,v)=>events[k]=v},AbortController,JSON,tr:(_,en)=>en,$:(s,root)=>s==='button'?root.children[0].children.find(x=>x.tag==='button'):nodes.get(s),setTimeout:(fn,ms)=>{const t={fn,ms};timers.push(t);return t;},clearTimeout(){},setInterval:(fn,ms)=>intervals.push({fn,ms}),api:(path,options)=>new Promise((resolve,reject)=>{requests.push({path,options,resolve,reject});options.signal.addEventListener('abort',()=>reject(new Error('timeout')));}),showLogin:()=>{state.user=null;context.hooks.pending(false);shell.hidden=true;},setStatus(){},closeModal(){},renderBrandSwitch(){},navigation(){},loadPage:async()=>true,hideUnavailableActions(){}};
  vm.createContext(context);vm.runInContext(section+'\nglobalThis.hooks={check:refreshEmployeeAccess,pending:setEmployeeAccessPending};',context);
  const success=()=>requests.at(-1).resolve({user:{...state.user},csrf_token:'verified'});
  return {context,document,state,nodes,attrs,events,intervals,timers,requests,ownerDashboardCalls,productSalesCalls,shell,hooks:context.hooks,success};
}
test('timeout masks work with visible recovery, preserves DOM, and automatically restores it',async()=>{
 const h=harness(),original=h.shell.children;
 let p=h.hooks.check();assert.equal(h.requests.length,1);h.timers.at(-1).fn();await p;
 assert.deepEqual(h.ownerDashboardCalls,['pause'],'Unverified access must stop owner reads without clearing recoverable data');
  assert.deepEqual(h.productSalesCalls,h.ownerDashboardCalls,'Product report must follow the same verified access lifecycle');
 assert(h.attrs.has('data-work-access-pending'));assert.equal(h.shell.inert,true);assert.equal(h.nodes.get('#work-access-recovery').hidden,false);assert.equal(h.nodes.get('#work-access-recovery-message').textContent.includes('return automatically'),true);assert.equal(h.state.user.id,'employee');assert.equal(h.shell.children,original);
 p=h.hooks.check();h.success();await p;assert.equal(h.attrs.size,0);assert.equal(h.shell.inert,false);assert.equal(h.nodes.get('#work-access-recovery').hidden,true);assert.equal(h.shell.children,original);assert.equal(h.shell.children[0].value,'unsaved input');assert.equal(h.shell.children[0].scrollTop,137);
 assert.deepEqual(h.ownerDashboardCalls,['pause'],'An unchanged verified scope must preserve owner state');
  assert.deepEqual(h.productSalesCalls,h.ownerDashboardCalls,'Product report must follow the same verified access lifecycle');
});
for(const status of [undefined,500,503])test(`transient ${status||'network'} failure retains recoverable account`,async()=>{const h=harness();const p=h.hooks.check();h.requests[0].reject(Object.assign(new Error('temporary'),{status}));await p;assert(h.attrs.has('data-work-access-pending'));assert.equal(h.state.user.id,'employee');});
for(const status of [401,403])test(`confirmed ${status} revokes rather than displaying a recovery state`,async()=>{const h=harness();const p=h.hooks.check();h.requests[0].reject(Object.assign(new Error('denied'),{status}));await p;assert.equal(h.state.user,null);assert.equal(h.attrs.size,0);assert.equal(h.shell.hidden,true);});
test('reauthentication and mismatched identity revoke',async()=>{for(const data of [{user:null},{user:{id:'other'},csrf_token:'x'},{user:{id:'employee'},csrf_token:'x',reauthentication_required:true}]){const h=harness(),p=h.hooks.check();h.requests[0].resolve(data);await p;assert.equal(h.state.user,null);}});
test('an old response cannot reveal work after a background resume',async()=>{const h=harness();let p=h.hooks.check();h.document.hidden=true;h.events.visibilitychange();h.document.hidden=false;h.events.visibilitychange();assert.equal(h.requests.length,1);h.success();await p;assert(h.attrs.has('data-work-access-pending'));assert.equal(h.requests.length,2);h.success();await new Promise(resolve=>setImmediate(resolve));assert.equal(h.attrs.size,0);});
test('focus does not blank confirmed screen and checks do not overlap',async()=>{const h=harness();h.events.focus();h.events.focus();await h.hooks.check();assert.equal(h.requests.length,1);assert.equal(h.attrs.size,0);h.success();await new Promise(resolve=>setImmediate(resolve));assert(h.intervals.some(x=>x.ms===2000));});
test('every open tab independently revokes on its next access check',async()=>{const tabs=[harness(),harness()];await Promise.all(tabs.map(async h=>{const p=h.hooks.check();h.requests[0].resolve({user:null});await p;}));for(const h of tabs)assert.equal(h.state.user,null);});

test('branch-only scope change keeps work masked until confirmed scoped render',async()=>{
 const h=harness();h.state.user.branch_ids=['b1'];h.state.branches=[{id:'b1'}];h.state.filters={branch_id:'b1'};h.state.orders=[{id:'old-private-order'}];h.nodes.get('#modal').open=true;h.state.closureDirty=true;
 let finish;h.context.loadPage=()=>new Promise(resolve=>{finish=resolve;});
 const p=h.hooks.check();h.requests[0].resolve({user:{...h.state.user,branch_ids:['b2']},csrf_token:'scope-token'});
 await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(h.ownerDashboardCalls,['reset','pause'],'Revoked scope must clear owner data before the scoped rebuild completes');
  assert.deepEqual(h.productSalesCalls,h.ownerDashboardCalls,'Product report must follow the same verified access lifecycle');
 assert(h.attrs.has('data-work-access-pending'));assert.equal(h.state.orders.length,0);assert.equal(h.state.branches.length,0);assert.equal(h.state.filters.branch_id,'');assert.equal(h.nodes.get('#modal').open,false);assert.equal(h.state.closureDirty,false);
 finish(true);await p;assert.equal(h.attrs.size,0);
});
test('failed or queued scope rebuild is never cleared by the next unchanged session',async()=>{
 const h=harness();let reloads=0;h.context.loadPage=async()=>{reloads++;return reloads>1;};
 let p=h.hooks.check();h.requests.at(-1).resolve({user:{...h.state.user,effective_permissions:[]},csrf_token:'changed'});await p;
 assert(h.attrs.has('data-work-access-pending'));
 p=h.hooks.check();h.success();await p;
 assert.equal(reloads,2);assert.equal(h.attrs.size,0);
});
test('background generation change during scoped rebuild cannot unmask a stale render',async()=>{
 const h=harness();let finish;h.context.loadPage=()=>new Promise(resolve=>{finish=resolve;});
 const p=h.hooks.check();h.requests[0].resolve({user:{...h.state.user,role:'driver'},csrf_token:'new-role'});await new Promise(resolve=>setImmediate(resolve));
 h.document.hidden=true;h.events.visibilitychange();finish(true);await p;assert(h.attrs.has('data-work-access-pending'));
});
