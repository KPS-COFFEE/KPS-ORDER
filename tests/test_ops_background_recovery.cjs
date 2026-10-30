'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('public/ops.js','utf8');
function setup(){
  const nodes={'#content':{innerHTML:'Existing work',setAttribute(){},querySelectorAll(){return [];},contains(){return false;}},'#modal':{open:false}};
  const events={};const state={user:{id:'driver-1',role:'driver',brand_id:'kps',branch_ids:['b1'],effective_permissions:['tasks_view']},page:'tasks',busy:new Set()};
  const context={state,window:{scrollX:0,scrollY:240,scrollTo(x,y){this.scrollX=x;this.scrollY=y;}},document:{documentElement:{hasAttribute(){return false;}},body:{classList:{toggle(){}}},addEventListener(name,cb){events[name]=cb;}},$:(selector)=>nodes[selector],trialMode:false,trackingController:null,navigation(){},driverLocation:{refresh(){}},hideUnavailableActions(){},tr:(ar,en)=>en,esc:String,notice:String,filterForm:()=>'',dispatchWorkspaceStatus:()=>false,setStatus(message,error){context.status={message,error};},Promise,JSON,Error,console};
  const names=['interventionInbox','employeeTickets','recovery','tracking','timing','fleet','accounting','support','printers','dashboard','orders','customers','tasks','shifts','custody','chat','reports','drivers','admin','maintenance','restricted'];
  for(const name of names)context[name]=async()=>{nodes['#content'].innerHTML=name;};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  let activePageLoad=null'),source.indexOf('  function filterForm()')),context);
  context.centralOperations={request:async(path)=>{if(path==='/api/session')return {user:state.user};return context.response;}};context.scopedApi=()=>{};
  vm.runInContext(source.slice(source.indexOf('  async function api('),source.indexOf('  async function scopedApi(')),context);
  return {context,nodes,state,events,run:(code)=>vm.runInContext(code,context)};
}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
test('reproduces original failure: silent refresh overwrites existing work',async()=>{
  const t=setup();t.context.tasks=async()=>{throw Error('temporary 503');};
  // Captured verbatim from production 1b101d1 before the repair.
  t.run("  async function loadPage(silent=false) { if(trialMode){await loadMorningTrial();return;} if(state.page!=='tracking'){trackingController?.destroy();trackingController=null;}document.body.classList.toggle('tracking-tv',state.page==='tracking'); document.body.classList.toggle('delivery-room',state.page==='dashboard'&&!state.showOperationsReport&&['owner','delivery_manager'].includes(state.user?.role));state.loading=true;$('#content').setAttribute('aria-busy','true');if(!silent)navigation(); if(!silent)$('#content').innerHTML = `<div class=\"loading\">${esc(tr('\u062c\u0627\u0631\u064a \u062a\u062d\u0645\u064a\u0644 \u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a\u2026', 'Loading data\u2026'))}</div>`; try { const handlers = { interventionInbox, employeeTickets, recovery, tracking, timing, fleet, accounting, support, printers, dashboard, orders, customers, tasks, shifts, custody, chat, reports, drivers, admin, maintenance, restricted }; await handlers[state.page]();if(state.page==='dashboard'&&state.showOperationsReport&&['owner','delivery_manager'].includes(state.user.role)){const back=document.createElement('button');back.type='button';back.className='quiet';back.dataset.action='delivery-workspace';back.textContent=tr('\u0627\u0644\u0639\u0648\u062f\u0629 \u0644\u0645\u0631\u0643\u0632 \u0627\u0644\u062a\u0648\u0635\u064a\u0644','Back to delivery control');$('#content').prepend(back);} setStatus(tr('\u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a \u0645\u062d\u062f\u062b\u0629', 'Data refreshed')); } catch (e) { $('#content').innerHTML = (['orders','dashboard'].includes(state.page)?filterForm():'') + notice(e.message, 'error') + `<div class=\"actions\"><button data-action=\"refresh\">${esc(tr('\u0625\u0639\u0627\u062f\u0629 \u0627\u0644\u0645\u062d\u0627\u0648\u0644\u0629', 'Retry'))}</button></div>`; setStatus(e.message, true); }finally{void driverLocation.refresh();state.loading=false;$('#content').setAttribute('aria-busy','false');hideUnavailableActions();const next=state.pendingPage;state.pendingPage=null;if(next&&state.user&&next!==state.page){state.page=next;await loadPage();}} }\n");
  await t.context.loadPage(true);assert.notEqual(t.nodes['#content'].innerHTML,'Existing work');
});
test('silent failure preserves work DOM and retry succeeds automatically',async()=>{
  const t=setup();t.context.tasks=async()=>{throw Error('temporary 503');};
  await t.context.loadPage(true);assert.equal(t.nodes['#content'].innerHTML,'Existing work');assert.match(t.context.status.message,/previous data remains/);
  t.context.tasks=async()=>{t.nodes['#content'].innerHTML='Recovered tasks';};await t.context.loadPage(true);
  assert.equal(t.nodes['#content'].innerHTML,'Recovered tasks');assert.equal(t.context.status.message,'Data refreshed');assert.equal(t.context.window.scrollY,240);
});
test('overlapping silent refresh is skipped; foreground refresh is queued once',async()=>{
  const t=setup(),d=deferred();let calls=0;t.context.tasks=async()=>{calls++;if(calls===1)await d.promise;};
  const first=t.context.loadPage(true);await t.context.loadPage(true);assert.equal(calls,1);
  const queued1=t.context.loadPage(),queued2=t.context.loadPage();assert.equal(calls,1);d.resolve();await first;assert.equal(await queued1,true);assert.equal(await queued2,true);assert.equal(calls,2);
});
test('delayed result cannot render after queued navigation',async()=>{
  const t=setup(),d=deferred();t.context.response=d.promise;
  t.context.tasks=async()=>{await t.context.api('/api/tasks');t.nodes['#content'].innerHTML='STALE TASKS';};
  const first=t.context.loadPage(true);t.state.pendingPage='orders';d.resolve({tasks:[]});await first;
  assert.equal(t.state.page,'orders');assert.equal(t.nodes['#content'].innerHTML,'orders');
});
test('editing while request is outstanding discards its data without overwriting input',async()=>{
  const t=setup(),d=deferred();t.context.response=d.promise;
  t.context.tasks=async()=>{await t.context.api('/api/tasks');t.nodes['#content'].innerHTML='BAD replacement';};
  const first=t.context.loadPage(true);t.events.input();d.resolve({tasks:[]});await first;
  assert.equal(t.nodes['#content'].innerHTML,'Existing work');
});
test('session checks remain independent from background rendering cancellation',async()=>{
  const t=setup(),d=deferred();t.context.tasks=async()=>{await d.promise;};const first=t.context.loadPage(true);t.events.input();
  const session=await t.context.api('/api/session');assert.equal(session.user.id,'driver-1');d.resolve();await first;
});
test('scope revocation discards delayed work data',async()=>{
  const t=setup(),d=deferred();t.context.response=d.promise;t.context.tasks=async()=>{await t.context.api('/api/tasks');t.nodes['#content'].innerHTML='EXPOSED';};
  const first=t.context.loadPage(true);t.state.user.effective_permissions=[];d.resolve({tasks:[]});await first;assert.equal(t.nodes['#content'].innerHTML,'Existing work');
});
test('successful refresh preserves filter input, checked state, expanded details and inner scrolling',()=>{
  const t=setup();const control={id:'filter',name:'search',type:'text',tagName:'INPUT',value:'0139',checked:false},check={id:'late',name:'late',type:'checkbox',tagName:'INPUT',value:'on',checked:true},details={open:true},pane={id:'rows',scrollTop:88,scrollLeft:0};
  const root=t.nodes['#content'];root.querySelectorAll=selector=>selector==='input,textarea,select'?[control,check]:selector==='details'?[details]:[pane];
  const view=t.run('capturePageView()');control.value='';check.checked=false;details.open=false;pane.scrollTop=0;t.context.view=view;t.run('restorePageView(view)');
  assert.equal(control.value,'0139');assert.equal(check.checked,true);assert.equal(details.open,true);assert.equal(pane.scrollTop,88);
});

test('optional driver panels may resolve after completed load but are discarded after a newer load',async()=>{
  const t=setup(),d=deferred();t.context.response=d.promise;let pending;
  t.context.tasks=async()=>{pending=t.context.api('/api/operations/driver-destination');};
  assert.equal(await t.context.loadPage(),true);d.resolve({destination:'branch'});assert.equal((await pending).destination,'branch');
  const late=deferred();t.context.response=late.promise;t.context.tasks=async()=>{pending=t.context.api('/api/operations/driver-route-plan');};
  await t.context.loadPage();t.state.page='orders';await t.context.loadPage();late.resolve({next_stop:'obsolete'});
  await assert.rejects(pending,error=>error.code==='PAGE_REFRESH_SUPERSEDED');
});
test('queued authorization rebuild awaits the new scope and reports successful render',async()=>{
  const t=setup(),d=deferred();t.context.response=d.promise;let calls=0;
  t.context.tasks=async()=>{calls++;if(calls===1)await t.context.api('/api/tasks');t.nodes['#content'].innerHTML='verified '+t.state.user.effective_permissions.join();};
  const first=t.context.loadPage();t.state.user.effective_permissions=['updated'];
  const rebuilt=t.context.loadPage();d.resolve({tasks:[]});await first;assert.equal(await rebuilt,true);assert.equal(t.nodes['#content'].innerHTML,'verified updated');
});
test('staff identity reads remain guarded work data when navigating away',async()=>{
  const t=setup(),d=deferred();t.context.centralOperations.request=()=>d.promise;
  t.context.tasks=async()=>{await t.context.api('/api/identity/staff');t.nodes['#content'].innerHTML='STAFF DATA';};
  const first=t.context.loadPage();t.state.pendingPage='orders';d.resolve({users:[]});await first;
  assert.equal(t.nodes['#content'].innerHTML,'orders');
});
test('checkbox reorder preserves selections by order ID without rewriting server IDs',()=>{
  const t=setup(),a={id:'',name:'order_id',type:'checkbox',tagName:'INPUT',value:'order-A',checked:false},b={id:'',name:'order_id',type:'checkbox',tagName:'INPUT',value:'order-B',checked:true};
  let controls=[a,b];t.nodes['#content'].querySelectorAll=selector=>selector==='input,textarea,select'?controls:[];
  const view=t.run('capturePageView()');controls=[b,a];a.checked=true;b.checked=false;t.context.view=view;t.run('restorePageView(view)');
  assert.equal(a.value,'order-A');assert.equal(b.value,'order-B');assert.equal(a.checked,false);assert.equal(b.checked,true);
});
