const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
  for(const lang of ['ar','en']){
    let rendered='',submit,mutations=[],newButton,approveButtons=[],rejectButtons=[],query=[];
    const form={isConnected:true,elements:{direction:{value:'receivable_recovery'},cash_amount:{value:'0'},card_amount:{value:'0'},cash_custody_event_id:{innerHTML:''},bank_reference:{}},querySelector:()=>({textContent:''})};
    const document={querySelector:s=>s==='[data-recovery-new]'?newButton:s==='[data-recovery-form]'?form:null,querySelectorAll:s=>s==='[data-recovery-approve]'?approveButtons:rejectButtons};
    const context={window:{},document};vm.createContext(context);vm.runInContext(fs.readFileSync('public/assets/financial-treatment-recovery.js','utf8'),context);
    const module=context.window.KPSFinancialTreatmentRecovery;
    assert.equal(module.amount('19.50'),1950);assert.equal(module.amount('0.01'),1);
    for(const bad of ['-1','1.234','1e3','','foo'])assert.throws(()=>module.amount(bad));
    const treatment={id:'t1',status:'approved',requested_by:'accountant',payload:{kind:'outstanding',liable_party_name:'Customer <X>',cash_variance_halalas:-550,card_variance_halalas:0,followup_user_id:'follow'},remaining:{cash:550,card:0},recoveries:[{id:'r1',status:'pending',requested_by:'other',requester_name:'Other accountant',payload:{cash_amount_halalas:100,card_amount_halalas:0,cash_source_actor_id:'cashier',direction:'receivable_recovery',occurred_on:'2026-10-26',evidence_reference:'<receipt>',note:'Actual funds received'}}]};
    const data={treatments:[treatment],followup_users:[{id:'follow',name:'Follow-up <owner>'}],can_approve:true};
    const tr=(ar,en)=>lang==='ar'?ar:en,esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    newButton={addEventListener:(_type,fn)=>{newButton.onclick=fn;}};approveButtons=[{dataset:{recoveryApprove:'r1'}}];rejectButtons=[{dataset:{recoveryReject:'r1'}}];
    const api=async path=>{query.push(path);return path.includes('/recovery-sources')?{sources:[{id:'cash1',amount_halalas:550,created_at:'2026-10-26',custodian_name:'Treasury <X>',reference_id:'R001'}]}:data;};
    await module.open({rid:'report',tid:'t1',api,mutation:async(path,body)=>{mutations.push({path,body:JSON.parse(JSON.stringify(body))});},tr,esc,money:x=>(x/100).toFixed(2),modal:(_title,html,handler)=>{rendered=html;submit=handler;},closeModal(){},user:{id:'reviewer'}});
    assert(rendered.includes('Customer &lt;X&gt;')&&rendered.includes('&lt;receipt&gt;')&&!rendered.includes('<receipt>'));
    assert(rendered.includes('5.50')&&rendered.includes(lang==='ar'?'لم يحتسب كسداد':'not counted as settlement'));
    approveButtons[0].onclick();await submit(new Map([['note','Independently checked actual receipt'],['evidence_reference','BANK-001'],['confirmed','on']]));
    assert.equal(mutations[0].path,'/api/accounting/driver-settlements/report/treatments/t1/recoveries/r1/approve');
    assert.equal(mutations[0].body.confirmed,true);
    await newButton.onclick();
    assert(form.elements.cash_custody_event_id.innerHTML.includes('Treasury &lt;X&gt;'));
    assert.equal(form.elements.card_amount.disabled,true);
    const fields=new Map([['direction','receivable_recovery'],['cash_amount','5.50'],['cash_custody_event_id','cash1'],['occurred_on','2026-10-26'],['evidence_reference','CASH-001'],['note','Actual cash received and independently checked']]);
    await submit(fields);
    assert.equal(mutations[1].body.cash_amount_halalas,550);assert.equal(mutations[1].body.card_amount_halalas,0);assert.equal(mutations[1].body.bank_reference,null);
    await newButton.onclick();
    await assert.rejects(()=>submit(new Map([...fields].map(([k,v])=>[k,k==='cash_amount'?'6':v]))));
    assert.equal(mutations.length,2);
  }
  console.log('PASS: AR/EN recovery evidence, precise money, independent approval, actual cash source picker and remaining-amount limits');
})().catch(e=>{console.error(e);process.exitCode=1;});
