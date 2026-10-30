const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/ops.js','utf8');
const between=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
const esc=v=>String(v??'').replaceAll('"','&quot;');
function base(){return {state:{user:{id:'driver',role:'driver'},tasks:[{id:'t/1',order_id:'o/1',status:'picked_up'}],branches:[]},tr:(_a,en)=>en,esc,can:()=>false,isManager:()=>false,isAdmin:()=>false,orderLabel:o=>o.id,orderStatusBadge:()=>'',serviceName:String,branchLabel:()=>'',orderItems:()=>'',customerCarDetails:()=>'',complimentaryName:()=>'',orderAmount:()=>'',badge:()=>'',notice:s=>s,dataItem:()=>'',num:String,datetime:String,statusName:String,money:String,safeHref:()=>false};}
test('controlled driver entry points open the structured widget without a task mutation or a forced call',async()=>{
 const calls=[],writes=[],dialogs=[];const ctx={...base(),window:{KPSInterventions:{open:async x=>calls.push(x)}},api:async()=>({enabled:true}),mutation:async(...x)=>writes.push(x),loadPage:async()=>{},modal:(...x)=>dialogs.push(x)};
 vm.createContext(ctx);
 vm.runInContext(between('  async function openTaskIntervention(','  async function cancelOrderWithJourney(')+between('  async function reportNoAnswer(','  setInterval(refreshOperationalAlerts,')+between('  async function driverProblem(','  async function partialReturn('),ctx);
 await ctx.reportNoAnswer('t/1');await ctx.driverProblem('t/1',null,'o/1');
 assert.equal(calls.length,2);assert.equal(calls[0].oid,'o/1');assert.equal(calls[0].initialReason,'no_answer');assert.equal(calls[1].initialReason,undefined);
 assert.equal(writes.length,0);assert.equal(dialogs.length,0);assert.equal(ctx.state.tasks[0].status,'picked_up');
 ctx.api=async()=>{throw Error('offline');};await assert.rejects(ctx.reportNoAnswer('t/1'),/offline/);
 assert.equal(writes.length,0);assert.equal(dialogs.length,0);
});
test('rendered driver report buttons carry order identity and disappear for historical tasks',()=>{
 const ctx=base();vm.createContext(ctx);vm.runInContext(between('  function taskCard(','  function ownManagerShift('),ctx);
 const task={id:'t',order_id:'o',driver_id:'driver',status:'picked_up',snapshot:{},customer:{}};
 for(const status of ['assigned','accepted','picked_up','problem']){const html=ctx.taskCard({...task,status});for(const action of ['problem','no-answer'])assert.match(html,new RegExp('data-action="'+action+'" data-order-id="o"'));}
 for(const status of ['delivered','cancelled','transferred','reassigned','completed'])assert.doesNotMatch(ctx.taskCard({...task,status}),/data-action="(?:problem|no-answer)"/);
 assert.doesNotMatch(ctx.taskCard({...task,driver_id:'other'}),/data-action="(?:problem|no-answer)"/);
});
test('manager and explicit delegate can reach pending treatment independently of cancellation availability',()=>{
 const ctx=base();vm.createContext(ctx);vm.runInContext(between('  function orderBrandBadge(','  function orderFilters('),ctx);
 const order={id:'o',service:'delivery',preparation_status:'completed',delivery_status:'delivered',completed_at:'2026-10-30',payment_status:'paid',snapshot:{},intervention_pending:{pending_role:'accounting'}};
 for(const role of ['owner','delivery_manager','admin']){ctx.state.user.role=role;ctx.can=p=>p===(role==='admin'?'orders_cancel_after_print':'orders_view');assert.match(ctx.orderCard(order),/data-action="order-intervention"/);assert.doesNotMatch(ctx.orderCard({...order,intervention_pending:null}),/data-action="order-intervention"/);}
 ctx.state.user.role='driver';assert.doesNotMatch(ctx.orderCard(order),/data-action="order-intervention"/);
});

test('actual driver widget submits only a structured problem and leaves pickup and delivery state alone',async()=>{
 const elements=[];
 class Element{
  constructor(tag){this.tag=tag;this.children=[];this.elements={};this.isConnected=true;this.alert={};elements.push(this);}
  set innerHTML(html){this.html=html;this.children=[];for(const match of html.matchAll(/<select name="([^"]+)">([\s\S]*?)<\/select>/g)){const values=[...match[2].matchAll(/<option value="([^"]*)"/g)].map(x=>x[1]);this.elements[match[1]]={value:values[0],querySelector:q=>values.includes(q.match(/value="([^"]*)"/)[1])?{}:null};}}
  get innerHTML(){return this.html;}
  append(x){this.children.push(x);}showModal(){}addEventListener(){}remove(){this.isConnected=false;}
  querySelector(q){if(q==='[data-close]')return {};if(q==='.amend-body')return this.body||(this.body=new Element('body'));if(q==='[role=alert]')return this.alert;return null;}
  querySelectorAll(){return [];}
 }
 const writes=[];const state={enabled:true,printed:false,version:4,requests:[],reasons:{customer_cancel:'Cancel',customer_change:'Change',duplicate:'Duplicate',no_answer:'No answer',damaged:'Damaged'},contact_results:{not_contacted:'Not contacted'},customer_requests:[],attachments:[],pending_roles:{},statuses:{}};
 const ctx={window:{},document:{createElement:t=>new Element(t),body:{append(){}}},FormData:class{constructor(f){this.f=f;}get(k){return this.f.elements[k]?.value??null;}}};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync('public/assets/order-interventions.js','utf8'),ctx);
 await ctx.window.KPSInterventions.open({oid:'o/1',user:{role:'driver'},api:async()=>state,mutation:async(...x)=>writes.push(x),initialReason:'no_answer'});
 const form=elements.find(e=>e.tag==='form');assert.equal(form.elements.reason_code.value,'no_answer');assert.equal(form.elements.contact_result,undefined);assert.doesNotMatch(form.html,/<textarea|name="physical_resolution"|name="contact_method"|name="evidence_attachment_id"/);
 assert.doesNotMatch(form.html,/value="customer_cancel"|value="duplicate"|value="customer_change"/);
 await form.onsubmit({preventDefault(){},target:form});assert.equal(writes[0][0],'/api/order-interventions/orders/o%2F1');assert.equal(writes[0][1].kind,'problem');assert.equal(writes[0][1].reason_code,'no_answer');assert.equal(writes[0][1].contact_result,'no_answer');assert.equal(writes[0][1].status,undefined);assert.equal(writes[0][1].version,4);
 form.elements.reason_code.value='damaged';await form.onsubmit({preventDefault(){},target:form});assert.equal(writes[1][1].contact_result,'not_contacted');
});

test('a cancellation-only admin has an actionable inbox without general orders permission',async()=>{
 const content={},reads=[],rendered=[];const ctx={...base(),heading:()=>{},$:()=>content,table:(_head,rows)=>{rendered.push(rows);return rows.flat().join('|');},empty:String,api:async path=>{reads.push(path);return {requests:[{order_id:'o"/1',order_no:'123',branch_name:'Branch',created_at:'now',reason_label:'Customer request',pending_label:'Delegate'}]};}};
 ctx.state.user={role:'admin',effective_permissions:['orders_cancel_after_print']};ctx.can=p=>ctx.state.user.effective_permissions.includes(p);
 vm.createContext(ctx);vm.runInContext(between('  const pagePermissions=','  const actionPermissions=')+'\nthis.pageAllowed=canPage;\n'+between('  async function interventionInbox(','  async function accounting('),ctx);
 assert.equal(ctx.pageAllowed('interventionInbox'),true);assert.equal(ctx.pageAllowed('orders'),false);
 await ctx.interventionInbox();assert.equal(reads[0],'/api/order-interventions/pending-cancellations');assert.match(content.innerHTML,/data-action="order-intervention" data-id="o&quot;\/1"/);assert.equal(rendered[0].length,1);
 ctx.state.user.effective_permissions=[];assert.equal(ctx.pageAllowed('interventionInbox'),false);
 ctx.state.user.role='driver';ctx.state.user.effective_permissions=['orders_cancel_after_print'];assert.equal(ctx.pageAllowed('interventionInbox'),false);
});
