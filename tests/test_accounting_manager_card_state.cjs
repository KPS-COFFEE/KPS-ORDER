const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
 const source=fs.readFileSync('public/assets/accounting.js','utf8');
 const code=source.slice(source.indexOf('  async function loadManagerCards('),source.indexOf('  function dayLinks('));
 let html='',query='',restored=null;
 const table={scrollLeft:125};
 const existing=[{addEventListener:()=>{},open:false,dataset:{managerCard:'closed'},querySelector:()=>table},{addEventListener:()=>{},open:true,dataset:{managerCard:'opened'},querySelector:()=>table}];
 const result={set innerHTML(v){html=v;},insertAdjacentHTML:(_,v)=>{html+=v;}};
 const host={isConnected:true,querySelector:q=>q==='#ac-results'?result:{},querySelectorAll:q=>q==='[data-manager-card]'?existing:[]};
 const card=id=>({id,manager_name:id,day:'2026-10-31',drivers:[],total:0,completed:0,review_completed:0,completion_percent:0,balance_completed:0,cash_completed:0,overdue_count:0,ready:false,pending_driver_count:1,pending_drivers:[{driver_name:'TEST PENDING',reason:'driver_shift_open'}],blockers:[{reason:'driver_records_pending',responsible:'delivery_manager'}],opened_at:'2026-10-31T16:00:00+03:00',closed_at:'2026-11-01T04:00:00+03:00',branch_names:['TEST BRANCH']});
 const context={ctx:{},saveManagerCardView:()=>{},host,tab:'settlements',managerCardOffset:20,managerCardFocus:'',managerCardOpenIds:new Set(),URLSearchParams,window:{scrollY:640,scrollTo:value=>{restored=value;}},api:async url=>{query=url;return {cards:[card('closed'),card('opened')],has_more:true};},options:{can_review:true},user:{id:'accountant'},tr:(ar,en)=>en,esc:String,text:(ar,en)=>en,money:String,dateTime:String,settlementStates:()=>({}),bindSettlementActions:()=>{}};
 vm.createContext(context);vm.runInContext(code,context);await context.loadManagerCards();
 assert.match(query,/offset=20/);assert.match(query,/page_size=20/);
 assert(!html.includes('data-manager-card="closed" open'));
 assert(html.includes('data-manager-card="opened" open'));
 assert.equal(restored.top,640);assert.equal(table.scrollLeft,125);
 assert(html.includes('TEST PENDING')&&html.includes('End the driver shift and send its closure'));
 assert(html.includes('TEST BRANCH')&&html.includes('Shift time'));
 assert(html.includes('data-manager-page="previous"')&&html.includes('data-manager-page="next"'));
 const mixed=context.managerCardBlockers({blockers:[{reason:'manager_allocation_pending',responsible:'accounting'}],pending_drivers:[{driver_name:'RELEASED',reason:'driver_accounting_pending',responsible:'accounting'},{driver_name:'OPEN',reason:'driver_shift_open',responsible:'delivery_manager'}]});
 assert.match(mixed,/Complete bag counts and accounting allocation — Responsible: accounting/);
 assert.match(mixed,/RELEASED: Driver duty ended;[^<]+Responsible: accounting/);
 assert.match(mixed,/OPEN: End the driver shift and send its closure — Responsible: delivery manager/);

 assert.match(context.managerCardNext({financial_ledger_closed:false}),/collect and count the bag/);
 assert.match(context.managerCardNext({operationally_closed:false,financial_ledger_closed:false}),/delivery manager completes end-of-duty/);
 assert.match(context.managerCardBlockers({blockers:[{reason:'manager_shift_open',responsible:'delivery_manager'}]}),/Complete the overdue manager shift[^<]+Responsible: delivery manager/);
 const pendingDeadline=context.managerCardDrivers({id:'missing-report',drivers:[],pending_drivers:[{id:'driver-old',driver_name:'OVERDUE DRIVER',responsible:'accounting',due_at:'2026-01-02T16:00:00+03:00',overdue:true}]});
 assert.match(pendingDeadline,/OVERDUE DRIVER/);assert.match(pendingDeadline,/Due: 2026-01-02T16:00:00\+03:00 · Overdue/);
 // An overdue open parent exposes child evidence without offering settlement
 // mutations that still require the manager to end its duty.
 context.cashCorrectionTickets=()=>'';
 vm.runInContext(source.slice(source.indexOf('  function settlementDriverRow('),source.indexOf('  function cashCorrectionTickets(')),context);
 const blocked=context.settlementDriverRow({id:'report-open-parent',user_id:'driver',accounting_actions_available:false,stage:'pending_balance',history:[],bank_halalas:5000,cash_due_halalas:10000,can_extend:true,due_at:'2026-01-02T16:00:00+03:00'},{});
 assert(!blocked.includes('data-driver-balance=')&&!blocked.includes('data-driver-receipt=')&&!blocked.includes('data-deadline-extend=')&&!blocked.includes('/print'));
 assert(blocked.includes('data-driver-history='));
 assert.match(context.managerCardNext({financial_ledger_closed:true,total:1,cash_completed:0,balance_completed:1}),/actually received cash/);
 assert.match(context.managerCardNext({financial_ledger_closed:true,total:1,cash_completed:1,balance_completed:1,review_needed_count:1}),/independent approval/);
 assert.match(context.managerCardNext({financial_ledger_closed:true,total:1,cash_completed:1,balance_completed:1,ready:true}),/issue the report/);
 assert.match(context.managerCardNext({approved_day:{id:'saved'}}),/outstanding amounts and bank matching/);
 console.log('PASS: accounting card reload preserves opened cards, scroll, scope and pending-driver reasons; archive pagination stays selected');
})().catch(error=>{console.error(error);process.exitCode=1;});
