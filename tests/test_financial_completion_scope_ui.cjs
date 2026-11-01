const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
  const source=fs.readFileSync('public/assets/accounting.js','utf8');
  const rowSource=source.slice(source.indexOf('  function settlementDriverRow('),source.indexOf('  function cashCorrectionTickets('));
  const cardsSource=source.slice(source.indexOf('  async function loadManagerCards('),source.indexOf('  function dayLinks('));
  for(const lang of ['ar','en']){
    let html='';const fields={};
    const result={set innerHTML(v){html=v;}};
    const host={isConnected:true,querySelector:q=>q==='#ac-results'?result:fields[q]||(fields[q]={}),querySelectorAll:()=>[]};
    const tr=(ar,en)=>lang==='ar'?ar:en,esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const base={id:'settled',user_id:'driver',driver_name:'TEST DRIVER',day:'2026-10-26',balance_approved:true,cash_recorded:true,bank_halalas:10000,cash_due_halalas:10000,cash_received_halalas:10000,system_total_halalas:20000,system_bank_halalas:10000,system_cash_halalas:10000,net_difference_halalas:0,cash_variance_halalas:0,bank_difference_halalas:0,shortage_halalas:0,surplus_halalas:0,stage:'cash_received',financially_complete:true,overall_financially_complete:false,accounting_review_complete:true,financial_completion_scope:'closure_cash_and_recorded_card',bank_settlement_included:false,bank_matching:{state:'unconfigured'},history:[]};
    const rows=[base,{...base,id:'outstanding',financially_complete:false,financial_state:'review_approved_outstanding',outstanding_receivable_halalas:20000}];
    const card={id:'manager',manager_name:'TEST MANAGER',day:'2026-10-26',drivers:rows,review_completed:2,completed:1,total:2,completion_percent:100,financial_completion_percent:50,balance_completed:2,cash_completed:2,overdue_count:0,outstanding_receivable_halalas:20000,outstanding_payable_halalas:0,ready:true};
    const context={saveManagerCardView:()=>{},window:{scrollY:0,scrollTo:()=>{}},managerCardOffset:0,managerCardFocus:'',managerCardOpenIds:new Set(),URLSearchParams,host,tab:'settlements',api:async()=>({cards:[card]}),options:{can_review:false},user:{id:'accountant'},tr,esc,text:(ar,en)=>esc(tr(ar,en)),money:v=>(v/100).toFixed(2),dateTime:()=>'',cashCorrectionTickets:()=>'',settlementStates:()=>({cash_received:tr('تم استلام الكاش — مراجعة الموازنة معتمدة','Cash received — balance review approved')}),bindSettlementActions:()=>{}};
    vm.createContext(context);vm.runInContext(rowSource+cardsSource,context);await context.loadManagerCards();
    assert(html.includes('2 / 2 '+tr('مراجعات محاسبية معتمدة','accounting reviews approved')));
    assert(!html.includes('1 / 2 '+tr('مراجعات محاسبية معتمدة','accounting reviews approved')));
    assert(html.includes(tr('مستحقات التقفيلة المسواة — كاش وشبكة مسجلة','Closure obligations settled — cash and recorded card')+': 1 / 2'));
    assert(html.includes('100%')&&html.includes('200.00'));
    assert(html.includes(tr('المراجعة المحاسبية فقط','accounting review requirements only'))||html.includes('Accounting review requirements only'));
    assert(html.includes(tr('المطابقة البنكية غير موثقة','bank matching undocumented')));
    assert(!html.includes('Financially reconciled')&&!html.includes('مكتمل ماليًا'));
    assert(html.includes(tr('اعتمادها مع مستحق مثبت لا يعني سداد المستحق أو مطابقة البنك','approval with a documented outstanding amount does not settle that amount or establish bank matching')));
  }
  console.log('PASS: review100% uses2/2 reviews; scoped obligations stay1/2 with200due and bank-undocumented state');
})().catch(e=>{console.error(e);process.exitCode=1;});
