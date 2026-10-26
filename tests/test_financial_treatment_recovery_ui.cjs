const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
  for(const lang of ['ar','en']){
    let rendered='',submit,mutations=[],newButton,approveButtons=[],rejectButtons=[],query=[];
    const form={isConnected:true,elements:{direction:{value:'receivable_recovery'},cash_amount:{value:'0'},card_amount:{value:'0'},cash_custody_event_id:{innerHTML:''},bank_reference:{},occurred_on:{}},querySelector:()=>({textContent:''})};
    const document={querySelector:s=>s==='[data-recovery-new]'?newButton:s==='[data-recovery-form]'?form:null,querySelectorAll:s=>s==='[data-recovery-approve]'?approveButtons:rejectButtons};
    const context={window:{},document};vm.createContext(context);vm.runInContext(fs.readFileSync('public/assets/financial-treatment-recovery.js','utf8'),context);
    const module=context.window.KPSFinancialTreatmentRecovery;
    assert.equal(module.amount('19.50'),1950);assert.equal(module.amount('0.01'),1);
    for(const bad of ['-1','1.234','1e3','','foo'])assert.throws(()=>module.amount(bad));
    const treatment={id:'t1',status:'approved',requested_by:'accountant',payload:{kind:'outstanding',liable_party_name:'Customer <X>',cash_variance_halalas:-550,card_variance_halalas:0,followup_user_id:'follow'},remaining:{cash:550,card:0},recoveries:[{id:'r1',status:'pending',requested_by:'other',requester_name:'Other accountant',payload:{cash_amount_halalas:100,card_amount_halalas:0,cash_source_actor_id:'cashier',direction:'receivable_recovery',occurred_on:'2026-10-26',evidence_reference:'<receipt>',note:'Actual funds received'}}]};
    const data={treatments:[treatment],followup_users:[{id:'follow',name:'Follow-up <owner>'}],can_approve:true};
    const tr=(ar,en)=>lang==='ar'?ar:en,esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    newButton={addEventListener:(_type,fn)=>{newButton.onclick=fn;}};approveButtons=[{dataset:{recoveryApprove:'r1'}}];rejectButtons=[{dataset:{recoveryReject:'r1'}}];
    const api=async path=>{query.push(path);return path.endsWith('/bank-lines')?{bank_lines:[{id:'bank1',amount_halalas:550,unallocated_halalas:550,received_on:'2026-10-26',statement_reference:'Statement <one>',bank_account_reference:'Account',line_reference:'Line1'}]}:path.includes('/recovery-sources')?{sources:[{id:'cash1',amount_halalas:550,created_at:'2026-10-26',custodian_name:'Treasury <X>',reference_id:'R001'}]}:data;};
    await module.open({rid:'report',tid:'t1',api,mutation:async(path,body)=>{mutations.push({path,body:JSON.parse(JSON.stringify(body))});},tr,esc,money:x=>(x/100).toFixed(2),modal:(_title,html,handler)=>{rendered=html;submit=handler;},closeModal(){},user:{id:'reviewer',manual_bank_matching_enabled:true}});
    assert(rendered.includes('Customer &lt;X&gt;')&&rendered.includes('&lt;receipt&gt;')&&!rendered.includes('<receipt>'));
    assert(rendered.includes('5.50')&&rendered.includes(lang==='ar'?'لم يحتسب كسداد':'not counted as settlement'));
    approveButtons[0].onclick();await submit(new Map([['note','Independently checked actual receipt'],['evidence_reference','BANK-001'],['confirmed','on']]));
    assert.equal(mutations[0].path,'/api/accounting/driver-settlements/report/treatments/t1/recoveries/r1/approve');
    assert.equal(mutations[0].body.confirmed,true);
    await newButton.onclick();
    assert(form.elements.cash_custody_event_id.innerHTML.includes('Treasury &lt;X&gt;'));
    assert(form.elements.bank_reference.innerHTML.includes('Statement &lt;one&gt;'));
    form.elements.bank_reference.value='bank1';form.elements.bank_reference.onchange();assert.equal(form.elements.occurred_on.value,'2026-10-26');
    assert.equal(form.elements.card_amount.disabled,true);
    const fields=new Map([['direction','receivable_recovery'],['cash_amount','5.50'],['cash_custody_event_id','cash1'],['occurred_on','2026-10-26'],['evidence_reference','CASH-001'],['note','Actual cash received and independently checked']]);
    await submit(fields);
    assert.equal(mutations[1].body.cash_amount_halalas,550);assert.equal(mutations[1].body.card_amount_halalas,0);assert.equal(mutations[1].body.bank_reference,null);
    await newButton.onclick();
    await assert.rejects(()=>submit(new Map([...fields].map(([k,v])=>[k,k==='cash_amount'?'6':v]))));
    assert.equal(mutations.length,2);
    treatment.payload.kind='documented_channel_correction';treatment.remaining_by_direction={receivable_recovery:{cash:0,card:0},payable_settlement:{cash:100,card:0}};
    await module.open({rid:'report',tid:'t1',api,mutation:async(path,body)=>mutations.push({path,body:JSON.parse(JSON.stringify(body))}),tr,esc,money:x=>(x/100).toFixed(2),modal:(_title,html,handler)=>{rendered=html;submit=handler;},closeModal(){},user:{id:'reviewer',manual_bank_matching_enabled:true}});
    form.elements.direction.value='payable_settlement';await newButton.onclick();
    const reversal=new Map([...fields].map(([k,v])=>[k,k==='direction'?'payable_settlement':k==='cash_amount'?'1':v]));await submit(reversal);
    assert.equal(mutations[2].body.direction,'payable_settlement');assert.equal(mutations[2].body.cash_amount_halalas,100);
    const beforeCashOnly=query.length;
    await module.open({rid:'report',tid:'t1',api,mutation:async()=>{throw new Error('must not mutate in this check');},tr,esc,money:x=>(x/100).toFixed(2),modal:(_title,html,handler)=>{rendered=html;submit=handler;},closeModal(){},user:{id:'reviewer',manual_bank_matching_enabled:false}});
    form.elements.direction.value='payable_settlement';await newButton.onclick();
    assert(!query.slice(beforeCashOnly).some(path=>path.endsWith('/bank-lines')));
    treatment.remaining_by_direction={receivable_recovery:{cash:0,card:100},payable_settlement:{cash:0,card:0}};
    await module.open({rid:'report',tid:'t1',api,mutation:async()=>{throw new Error('must not post a held card recovery');},tr,esc,money:x=>(x/100).toFixed(2),modal:(_title,html,handler)=>{rendered=html;submit=handler;},closeModal(){},user:{id:'reviewer',manual_bank_matching_enabled:false}});
    assert(rendered.includes(lang==='ar'?'قيد التطوير':'under development'));
    assert(!rendered.includes('data-recovery-new'));

  }
  console.log('PASS: AR/EN recovery evidence, precise money, independent approval, actual cash source picker and remaining-amount limits');
})().catch(e=>{console.error(e);process.exitCode=1;});
