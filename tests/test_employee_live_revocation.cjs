'use strict';
// Execute the actual access monitor; the companion Chromium tests verify DOM,
// real authentication, two open tabs and server revocation together.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {test}=require('node:test');
const source=fs.readFileSync('public/ops.js','utf8');
const start=source.indexOf('  let permissionRefreshBusy=false;');
const end=source.indexOf('  new MutationObserver(hideUnavailableActions)',start);
assert.ok(start>0&&end>start);
const monitor=source.slice(start,end);

function setup({role='driver',api}={}) {
  const attrs=new Map(),events=new Map(),intervals=[];
  const calls=[];
  const previous={id:'employee-a',role,effective_permissions:['orders_view']};
  const state={user:previous,csrf:'initial',staff:['private'],orders:['private'],shifts:['private'],conversations:['private'],requestKeys:new Map([['pending','private']]),chatHistory:new Map()};
  // The monitor now renders a non-private recovery dialog and makes work inert.
  // Model those real DOM capabilities while retaining every revocation assertion.
  const nodes=new Map();
  class Node {
    constructor(tag){this.tag=tag;this.children=[];this.hidden=false;this.open=false;this.inert=false;this.listeners={};}
    append(...children){this.children.push(...children);for(const child of children)if(child.id)nodes.set('#'+child.id,child);}
    setAttribute(name,value){this[name]=value;}
    addEventListener(name,handler){this.listeners[name]=handler;}
    focus(){this.focused=true;}
    showModal(){this.open=true;}
    close(){this.open=false;}
    replaceChildren(){this.children=[];}
  }
  const modal=new Node('div');modal.children=['private'];
  nodes.set('#modal-body',modal);nodes.set('#app-shell',new Node('main'));
  const dialog={closed:false,close(){this.closed=true}};
  const root={hasAttribute:name=>attrs.has(name),setAttribute:(name,value)=>attrs.set(name,value),removeAttribute:name=>attrs.delete(name)};
  const document={hidden:false,documentElement:root,createElement:tag=>new Node(tag),body:new Node('body'),querySelectorAll:()=>[dialog],addEventListener:(name,cb)=>events.set(name,cb)};
  const context={state,document,trialMode:false,AbortController,
    window:{addEventListener:(name,cb)=>events.set(name,cb)},
    $:(selector,parent)=>selector==='button'?parent.children[0].children.find(node=>node.tag==='button'):nodes.get(selector),
    api:async(...args)=>{calls.push(args);return api?api(...args):{user:{...previous},csrf_token:'fresh'}},
    setTimeout:()=>1,clearTimeout:()=>{},setInterval:(cb,ms)=>intervals.push({cb,ms}),
    showLogin:()=>{state.user=null;state.csrf='';calls.push(['showLogin'])},
    closeModal:()=>calls.push(['closeModal']),renderBrandSwitch(){},navigation(){},loadPage:async()=>true,hideUnavailableActions(){},
    setStatus:(message,error)=>calls.push(['status',message,error]),tr:(_ar,en)=>en};
  vm.createContext(context);vm.runInContext(monitor,context);
  return {context,state,attrs,dialog,modal,calls,events,intervals};
}

for(const role of ['driver','owner']) {
  test(`idle ${role} poll erases work and closes every dialog after access ends`,async()=>{
    const t=setup({role,api:async()=>({user:null,csrf_token:null})});
    assert.equal(t.intervals[0].ms,2000);
    await t.intervals[0].cb();
    assert.equal(t.state.user,null);
    for(const key of ['orders','shifts','conversations'])assert.equal(t.state[key].length,0);
    assert.equal(t.state.staff,null);
    assert.equal(t.state.requestKeys.size,0);
    assert.equal(t.dialog.closed,true);
    assert.equal(t.modal.children.length,0);
  });
}

test('a forbidden access check also clears existing work rather than preserving it',async()=>{
  const t=setup({api:async()=>{throw Object.assign(Error('Revoked'),{status:403})}});
  await t.context.refreshEmployeeAccess();
  assert.equal(t.state.user,null);
  assert.equal(t.dialog.closed,true);
  assert.equal(t.modal.children.length,0);
});

test('offline work stays masked until a successful live access response',async()=>{
  let online=false;
  const t=setup({api:async()=>{if(!online)throw Error('Network unavailable');return {user:{...t.state.user},csrf_token:'fresh'}}});
  t.events.get('offline')();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(t.attrs.has('data-work-access-pending'),true);
  assert.ok(t.state.user);
  online=true;
  t.events.get('online')();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(t.attrs.has('data-work-access-pending'),false);
  assert.equal(t.state.csrf,'fresh');
});

test('a background page is masked before returning and cannot display old work during recheck',async()=>{
  let resolve;
  const t=setup({api:()=>new Promise(r=>{resolve=r})});
  t.context.document.hidden=true;
  t.events.get('visibilitychange')();
  assert.equal(t.calls.length,0);
  assert.equal(t.attrs.has('data-work-access-pending'),true);
  t.context.document.hidden=false;
  t.events.get('visibilitychange')();
  assert.equal(t.attrs.has('data-work-access-pending'),true);
  resolve({user:null,csrf_token:null});
  await new Promise(r=>setImmediate(r));
  assert.equal(t.state.user,null);
  assert.equal(t.dialog.closed,true);
});

test('a response from a previous employee cannot replace a new login',async()=>{
  let resolve;
  const t=setup({api:()=>new Promise(r=>{resolve=r})});
  const pending=t.context.refreshEmployeeAccess();
  const newer={id:'employee-b',role:'driver',effective_permissions:[]};
  t.state.user=newer;
  resolve({user:{id:'employee-a'},csrf_token:'old'});
  await pending;
  assert.equal(t.state.user,newer);
  assert.equal(t.state.csrf,'initial');
  assert.equal(t.dialog.closed,false);
});

const hoursSource=fs.readFileSync('public/assets/driver-hours.js','utf8');
const hoursMonitor=hoursSource.slice(hoursSource.indexOf(' function maskWork()'),hoursSource.indexOf(' async function load(automatic=false)'));
assert.ok(hoursMonitor.includes('async function checkAccess()'));
function hoursSetup({fetch,user}={}) {
  const fields=new Map(),attrs=new Map(),calls=[];
  const element=id=>{
    if(!fields.has(id))fields.set(id,{children:['private'],textContent:'private',hidden:false,href:'private',replaceChildren(){this.children=[]},removeAttribute(name){delete this[name]}});
    return fields.get(id);
  };
  const principal=user||{id:'hours-employee',role:'delivery_manager',brand_id:'kps',branch_ids:['b1'],effective_permissions:['driver_hours_reports']};
  const context={destroyed:false,requestVersion:0,requestController:null,accessController:null,accessPromise:null,accessGeneration:0,accessUserId:null,accessFingerprint:null,
    report:{private:true},AbortController,timer:1,accessTimer:2,
    document:{visibilityState:'visible',documentElement:{setAttribute:(n,v)=>attrs.set(n,v),removeAttribute:n=>attrs.delete(n)},body:{replaceChildren:()=>calls.push('body-cleared')}},
    navigator:{onLine:true},form:{elements:{driver_id:{},branch_id:{}}},
    $:element,setTimeout:()=>1,clearTimeout(){},clearInterval:n=>calls.push(['clearInterval',n]),
    location:{replace:uri=>calls.push(['navigate',uri])},
    options:(select,rows)=>calls.push(['options',rows.length]),load:async()=>calls.push('report-refresh'),
    fetch:async(...args)=>{calls.push(['fetch',...args]);return fetch?fetch(...args):{ok:true,status:200,json:async()=>({user:principal,csrf_token:'verified-session-csrf'})}}};
  vm.createContext(context);vm.runInContext(hoursMonitor,context);
  return {context,fields,attrs,calls,principal};
}

test('hours heartbeat clears cached report and all DOM before returning a revoked employee to sign-in',async()=>{
  const t=hoursSetup({fetch:async()=>({ok:true,status:200,json:async()=>({user:null,csrf_token:null})})});
  assert.equal(await t.context.checkAccess(),false);
  assert.equal(t.context.destroyed,true);
  assert.equal(t.context.report,null);
  assert.equal(t.fields.get('#hours-kpis').children.length,0);
  assert.equal(t.fields.get('#hours-drivers').children.length,0);
  assert.equal(t.fields.get('#hours-xlsx').hidden,true);
  assert.equal(t.fields.get('#hours-xlsx').href,undefined);
  assert.ok(t.calls.includes('body-cleared'));
  assert.deepEqual(t.calls.at(-1),['navigate','/ops']);
});

test('hours heartbeat makes no request in hidden or offline pages',async()=>{
  const t=hoursSetup();t.context.document.visibilityState='hidden';
  assert.equal(await t.context.checkAccess(),false);
  t.context.document.visibilityState='visible';t.context.navigator.onLine=false;
  assert.equal(await t.context.checkAccess(),false);
  assert.equal(t.calls.length,0);
});

test('hours heartbeat cannot expose data on a failed check or stale pre-hide response',async()=>{
  const unavailable=hoursSetup({fetch:async()=>{throw Error('offline')}});
  assert.equal(await unavailable.context.checkAccess(),false);
  assert.equal(unavailable.attrs.has('data-hours-access-pending'),true);
  let release;
  const t=hoursSetup({fetch:()=>new Promise(resolve=>{release=resolve})});
  const pending=t.context.checkAccess();
  t.context.pauseAccessChecks();
  release({ok:true,status:200,json:async()=>({user:t.principal,csrf_token:'old'})});
  assert.equal(await pending,false);
  assert.equal(t.attrs.has('data-hours-access-pending'),true);
  assert.equal(t.context.accessUserId,null);
});

test('hours concurrent checks share one request and a valid result unmasks work',async()=>{
  let release;
  const t=hoursSetup({fetch:()=>new Promise(resolve=>{release=resolve})});
  t.context.maskWork();
  const one=t.context.checkAccess(),two=t.context.checkAccess();
  assert.equal(t.calls.filter(c=>Array.isArray(c)&&c[0]==='fetch').length,1);
  release({ok:true,status:200,json:async()=>({user:t.principal,csrf_token:'valid'})});
  assert.equal(await one,true);assert.equal(await two,true);
  assert.equal(t.attrs.has('data-hours-access-pending'),false);
});

test('hours heartbeat revokes a different account or a removed report permission',async()=>{
  for(const changed of ['identity','permission']){
    const t=hoursSetup();
    assert.equal(await t.context.checkAccess(),true);
    if(changed==='identity')t.principal.id='different-account';
    else t.principal.effective_permissions=[];
    assert.equal(await t.context.checkAccess(),false);
    assert.equal(t.context.destroyed,true);
    assert.ok(t.calls.includes('body-cleared'));
  }
});

test('the actual login transition scrubs already-open dialogs even when a background 401 arrives first',()=>{
  const loginSource=source.slice(source.indexOf('  function showLogin()'),source.indexOf('  async function driverBrandDedication()'));
  assert.ok(loginSource.includes('function showLogin()'));
  const nodes=new Map(),dialogs=[{closed:false,close(){this.closed=true}}];
  const node=selector=>{
    if(!nodes.has(selector))nodes.set(selector,{hidden:false,innerHTML:'private',children:['private'],href:'',textContent:'private',replaceChildren(){this.children=[]},reset(){this.resetCalled=true}});
    return nodes.get(selector);
  };
  const state={user:{id:'revoked'},requestKeys:new Map([['pending','private']]),chatHistory:new Map([['chat','private']]),csrf:'private'};
  const attrs=new Map([['data-work-access-pending','']]);
  const context={state,$:node,employeeScopeRefreshRequired:false,setEmployeeAccessPending:pending=>{assert.equal(pending,false);attrs.delete('data-work-access-pending');node('#app-shell').inert=false;},trackingController:null,driverLocation:{reset(){}},setStatus(){},tr:(_ar,en)=>en,
    document:{querySelectorAll:()=>dialogs,documentElement:{removeAttribute:name=>attrs.delete(name)},body:{classList:{remove(){}}}}};
  vm.createContext(context);vm.runInContext(loginSource,context);context.showLogin();
  assert.equal(dialogs[0].closed,true);
  assert.equal(node('#modal-body').children.length,0);
  assert.equal(node('#content').innerHTML,'');
  assert.equal(node('#app-shell').hidden,true);
  assert.equal(node('#login-shell').hidden,false);
  assert.equal(state.user,null);
  assert.equal(state.csrf,'');
  assert.equal(state.chatHistory.size,0);
  assert.equal(state.requestKeys.size,0);
  assert.equal(attrs.has('data-work-access-pending'),false);
});

test('ordinary focus checks do not blank the visible interface while checking',async()=>{
  let resolve;
  const t=setup({api:()=>new Promise(r=>{resolve=r})});
  t.events.get('focus')();
  assert.equal(t.attrs.has('data-work-access-pending'),false);
  assert.equal(t.calls.length,1);
  // Repeated focus shares the in-flight request rather than firing duplicates.
  t.events.get('focus')();
  assert.equal(t.calls.length,1);
  resolve({user:{...t.state.user},csrf_token:'fresh'});
  await new Promise(r=>setImmediate(r));
  assert.equal(t.attrs.has('data-work-access-pending'),false);
});

test('focus cannot unmask a suspended page before server verification',async()=>{
  let resolve;
  const t=setup({api:()=>new Promise(r=>{resolve=r})});
  t.context.document.hidden=true;t.events.get('visibilitychange')();
  t.context.document.hidden=false;t.events.get('focus')();
  assert.equal(t.attrs.has('data-work-access-pending'),true);
  resolve({user:null,csrf_token:null});
  await new Promise(r=>setImmediate(r));
  assert.equal(t.state.user,null);
  assert.equal(t.dialog.closed,true);
});

test('network failure on focus still masks private work',async()=>{
  const t=setup({api:async()=>{throw Error('Network down')}});
  t.events.get('focus')();
  await new Promise(r=>setImmediate(r));
  assert.equal(t.attrs.has('data-work-access-pending'),true);
});

test('a response begun before hiding cannot unmask a later resume',async()=>{
  const pending=[];
  const t=setup({api:()=>new Promise(resolve=>pending.push(resolve))});
  const first=t.context.refreshEmployeeAccess();
  t.context.document.hidden=true;t.events.get('visibilitychange')();
  t.context.document.hidden=false;t.events.get('visibilitychange')();
  pending[0]({user:{...t.state.user},csrf_token:'pre-hide'});
  await first;
  assert.equal(t.attrs.has('data-work-access-pending'),true);
  assert.equal(t.state.csrf,'initial');
  assert.equal(pending.length,2);
  pending[1]({user:null,csrf_token:null});
  await new Promise(r=>setImmediate(r));
  assert.equal(t.state.user,null);
});

test('hidden polls cannot clear the suspended-page mask',async()=>{
  const t=setup();t.context.document.hidden=true;t.events.get('visibilitychange')();
  await t.context.refreshEmployeeAccess();
  assert.equal(t.attrs.has('data-work-access-pending'),true);
  assert.equal(t.calls.length,0);
});

test('a session response for another principal revokes instead of replacing identity',async()=>{
  const t=setup({api:async()=>({user:{id:'different-account'},csrf_token:'foreign'})});
  await t.context.refreshEmployeeAccess();
  assert.equal(t.state.user,null);
  assert.equal(t.modal.children.length,0);
});
