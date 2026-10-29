const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
 const source=fs.readFileSync('public/assets/accounting.js','utf8');
 const code=source.slice(source.indexOf('  async function loadManagerCards('),source.indexOf('  function dayLinks('));
 for(const lang of ['ar','en'])for(const tab of ['settlements','matching']){
  let html='';const fields={},result={set innerHTML(value){html=value;}};
  const host={isConnected:true,querySelector:q=>q==='#ac-results'?result:fields[q]||(fields[q]={}),querySelectorAll:()=>[]};
  const tr=(ar,en)=>lang==='ar'?ar:en,esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const base={day:'2026-10-28',drivers:[],total:0,review_completed:0,completed:0,completion_percent:0,balance_completed:0,cash_completed:0,overdue_count:0,ready:false,pending_driver_count:1};
  const cards=[{...base,id:'ali',manager_name:'علي',period:'evening',shift_no:'M-440',financial_ledger_closed:false},
    {...base,id:'ahmed',manager_name:'أحمد عبدالناصر',period:'morning',financial_ledger_closed:true,manager_report_id:'saved-report'}];
  const context={host,tab,api:async()=>({cards}),options:{can_review:true},user:{id:'accountant'},tr,esc,text:(ar,en)=>esc(tr(ar,en)),money:v=>(v/100).toFixed(2),dateTime:()=>'',settlementStates:()=>({}),bindSettlementActions:()=>{}};
  vm.createContext(context);vm.runInContext(code,context);await context.loadManagerCards();
  assert(html.includes('علي')&&html.includes('أحمد عبدالناصر'));
  assert(html.includes(tr('صباحي','Morning'))&&html.includes(tr('مسائي','Evening')));
  assert(html.includes('M-440')&&html.includes('2026-10-28'));
  assert(html.includes(tr('لم تعتمد ماليًا.','Not financially approved.')));
  assert(html.includes('/api/accounting/driver-settlements/saved-report/print'));
  assert.equal((html.match(/data-day-approve="[^"]+" disabled/g)||[]).length,2);
  assert(!html.includes('NaN')&&!html.includes('100%')&&!html.includes('undefined'));
 }
 console.log('PASS: Ali/Ahmed visible in both accounting tabs, AR/EN, morning/evening, missing evidence and no premature approval');
})().catch(e=>{console.error(e);process.exitCode=1;});
