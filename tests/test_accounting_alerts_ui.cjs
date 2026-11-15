const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/assets/accounting-alerts.js','utf8');
function setup(){const context={window:{}};vm.createContext(context);vm.runInContext(source,context);return context.window.KPSAccountingAlerts;}
const tr=(_a,en)=>en,esc=s=>String(s).replaceAll('<','&lt;');
test('only accounting staff get the financial inbox; managers keep their decision alerts',()=>{
 const {accountingOnly}=setup();const perms=['accounting_reports','accounting_review','chat_view','orders_view'];
 assert.equal(accountingOnly({role:'admin'},p=>perms.includes(p)),true);
 for(const role of ['owner','delivery_manager','driver'])assert.equal(accountingOnly({role},p=>perms.includes(p)),false);
 for(const decision of ['orders_cancel_after_print','orders_cancel_collected','delivery_assign','orders_print','shifts_approve','support_manage'])assert.equal(accountingOnly({role:'admin'},p=>[...perms,decision].includes(p)),false);
 assert.equal(accountingOnly({role:'admin'},p=>p==='accounting_cases_followup'),false);
});
test('financial tasks stay actionable without operational/chat endpoints or any mutations',async()=>{
 const {refresh}=setup(),calls=[],host={innerHTML:'',hidden:true};
 const data={
  '/api/accounting/manager-cards?page_size=20&offset=0':{cards:[{approved_day:null},{approved_day:{id:'closed'}}],has_more:true},
  '/api/order-financial-cases':{cases:[{financial_case:{status:'pending'}},{financial_case:{status:'resolved'}}]},
  '/api/driver-floats':{requests:[{approved:null},{approved:true},{approved:false}]},
  '/api/cash-bags':{bags:[{ready_to_count:true,count_id:null},{ready_to_count:false,count_id:null},{ready_to_count:true,count_id:'counted'}]}
 };
 await refresh(host,{api:async p=>{calls.push(p);assert.ok(p in data);return data[p];},can:()=>true,user:{central_operations:{brands:[]}},tr,esc});
 assert.equal(calls.length,4);assert.equal(host.hidden,false);
 assert.match(host.innerHTML,/Closures needing completion or approval \(1\)/);
 assert.match(host.innerHTML,/financial treatment or refund \(1\)/);
 assert.match(host.innerHTML,/floats awaiting accounting decision \(1\)/);
 assert.match(host.innerHTML,/physical collection and count \(1\)/);
 for(const action of ['financial-cases','float-register','cash-bags'])assert.ok(host.innerHTML.includes(`data-action="${action}"`));
 assert.match(host.innerHTML,/data-page="accounting"/);assert.match(host.innerHTML,/More closures/);
});
test('report reader does not request restricted review, bag or float endpoints',async()=>{
 const calls=[],host={};await setup().refresh(host,{api:async p=>{calls.push(p);return {cards:[]};},can:p=>p==='accounting_reports',user:{central_operations:{}},tr,esc});
 assert.deepEqual(calls,['/api/accounting/manager-cards?page_size=20&offset=0']);
 assert.match(host.innerHTML,/No pending accounting actions in the displayed sources/);
});
test('one failed source does not hide other tasks or assert financial completion',async()=>{
 const host={};await setup().refresh(host,{api:async p=>{if(p.includes('manager-cards'))throw Error('offline');if(p.includes('financial-cases'))return {cases:[{financial_case:{status:'pending'}}]};return {requests:[]};},can:()=>true,user:{},tr,esc});
 assert.match(host.innerHTML,/financial treatment or refund \(1\)/);assert.match(host.innerHTML,/Some actions could not refresh/);assert.doesNotMatch(host.innerHTML,/No pending/);
});
test('stale account or brand read cannot render data after access changes',async()=>{
 const host={innerHTML:'prior'},calls=[];await setup().refresh(host,{api:async p=>{calls.push(p);return {cards:[{}]};},can:p=>p==='accounting_reports',user:{},tr,esc,isCurrent:()=>false});
 assert.equal(host.innerHTML,'prior');assert.equal(calls.length,1);
});


test('accounting-only staff never initialize order sound or poll its timing policy',async()=>{
 const ops=fs.readFileSync('public/ops.js','utf8'),a=ops.indexOf('  function accountingNotificationsOnly('),b=ops.indexOf("  document.addEventListener('pointerdown'",a);
 assert.ok(a>=0&&b>a);
 const context={window:{KPSAccountingAlerts:setup()},state:{user:{role:'admin'}},can:p=>['accounting_reports','accounting_review'].includes(p),api:()=>assert.fail('No operational policy read'),enableAlertSound:()=>assert.fail('No order audio initialization')};
 vm.createContext(context);vm.runInContext(ops.slice(a,b),context);
 await context.refreshAlertPolicy();await context.ensureMandatoryAlertSound();
});

test('counted bags retain allocation work and cannot request a second cash collection',()=>{
 const {closureTasks,renderTasks}=setup();
 const card={id:'m1',manager_name:'Ali',day:'2026-11-13',financial_ledger_closed:false,pending_drivers:[{id:'s1',driver_name:'Driver',responsible:'accounting',overdue:true,due_at:'2026-11-14T16:00:00+03:00'}],drivers:[]};
 const bag={id:'b1',manager_shift_ids:['m1'],shift_ids:['s1'],count_id:'n1',received_cash_halalas:0,declared_cash_halalas:0,declared_card_halalas:5500};
 const tasks=closureTasks([card],[bag]);assert.equal(tasks.length,1);assert.equal(tasks[0].kind,'allocate');assert.equal(tasks[0].received_cash_halalas,0);
 const html=renderTasks(tasks,{tr,esc,local:true,lang:'en'});
 assert.match(html,/Bag counted; complete attribution/);assert.match(html,/Recorded receipt.*0.00/);assert.match(html,/Overdue/);assert.match(html,/data-ac-work-card="m1"/);
 assert.doesNotMatch(html,/Physically receive and count/);
 assert.equal(closureTasks([{...card,approved_day:{id:'done'}}],[bag]).length,0);
});
test('bags cannot link by driver/date alone or by only one side of the shift relationship',()=>{
 const {closureTasks}=setup();
 const card={id:'m1',pending_drivers:[{id:'s1',responsible:'accounting'}],drivers:[]};
 for(const bag of [{id:'wrong-manager',manager_shift_ids:['m2'],shift_ids:['s1'],count_id:'n'}, {id:'wrong-shift',manager_shift_ids:['m1'],shift_ids:['s2'],count_id:'n'}]){
   const tasks=closureTasks([card],[bag]);assert.equal(tasks[0].kind,'source_review');assert.equal(tasks[0].bag_id,'');
 }
});
test('custody differences and manager handover have explicit owners; shared physical bags do not double count',()=>{
 const {closureTasks}=setup();const card={id:'m1',drivers:[],pending_drivers:[{id:'s1',responsible:'accounting'},{id:'s2',responsible:'accounting'}]};
 const bag={id:'b',manager_shift_ids:['m1'],shift_ids:['s1','s2'],ready_to_count:true};
 assert.equal(closureTasks([card],[bag]).length,1);
 assert.equal(closureTasks([card],[bag])[0].kind,'bag_count');
 assert.equal(closureTasks([card],[{...bag,ready_to_count:false}])[0].responsible,'delivery_manager');
 assert.equal(closureTasks([card],[{...bag,count_id:'n',accounting_custody_difference_halalas:1}])[0].kind,'custody');
 assert.equal(closureTasks([card],[{...bag,count_id:'n',accounting_custody_difference_halalas:1,custody_review:{financially_complete:true}}])[0].kind,'allocate');
});
test('recorded cash never creates a receipt task, but pending balances and independent variance review stay visible',()=>{
 const {closureTasks}=setup();const row={id:'r',shift_id:'s',cash_recorded:true,cash_received_halalas:1200,balance_approved:false,variance_pending:true};
 const card={id:'m',drivers:[row],pending_drivers:[]};const tasks=closureTasks([card]);
 assert.deepEqual(Array.from(tasks,t=>t.kind),['balance','variance']);
 assert.equal(tasks[0].received_cash_halalas,1200);
 assert.equal(closureTasks([{...card,drivers:[{...row,period_approval_allowed:true}],ready:true}])[0].kind,'approve');
});
test('a counted bag with missing settlement receipt exposes linkage review, never another collection',()=>{
 const {closureTasks}=setup();const tasks=closureTasks([{id:'m',drivers:[{id:'r',shift_id:'s',balance_approved:true,cash_recorded:false}],pending_drivers:[]}],[{id:'b',manager_shift_ids:['m'],shift_ids:['s'],count_id:'n'}]);
 assert.equal(tasks[0].kind,'receipt_link');
});
