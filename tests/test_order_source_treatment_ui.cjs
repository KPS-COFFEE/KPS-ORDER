const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
  for(const lang of ['ar','en']){
    let rendered='',submit,calls=[],mutations=[],newButton={addEventListener:(_type,fn)=>{newButton.onclick=fn;}},approve=[{dataset:{sourceTreatmentApprove:'t1'}}],returns=[{dataset:{sourceTreatmentReturn:'t1'}}],rejects=[{dataset:{sourceTreatmentReject:'t1'}}];
    const form={elements:{kind:{value:'price_correction'},corrected_total:{},liable_party_type:{},liable_party_name:{},followup_user_id:{},due_on:{}},querySelector:()=>({hidden:false})};
    const context={window:{},document:{querySelector:s=>s==='[data-source-treatment-form]'?form:newButton,querySelectorAll:s=>s==='[data-source-treatment-approve]'?approve:s==='[data-source-treatment-return]'?returns:s==='[data-source-treatment-reject]'?rejects:[]}};vm.createContext(context);vm.runInContext(fs.readFileSync('public/assets/order-source-treatment.js','utf8'),context);
    const module=context.window.KPSOrderSourceTreatment,tr=(ar,en)=>lang==='ar'?ar:en,esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const data={order_id:'o1',version:4,case_id:'case1',can_propose:true,can_approve:true,allowed_kinds:['price_correction','outstanding'],original_total_halalas:2500,effective_total_halalas:2500,effective_claim_halalas:2500,price_credit_halalas:0,refund_credit_halalas:0,actual_collected_halalas:1950,pending_amount_halalas:550,financial_state:'pending_review',amounts_matched:false,financially_complete:false,followup_users:[{id:'accountant',name:'Accountant <one>'}],treatments:[{id:'t1',status:'pending',requested_by:'other',requester_name:'Other accountant',payload:{kind:'price_correction',corrected_total_halalas:1950,credit_halalas:550,evidence_reference:'<receipt>',note:'Actual approved historical price was 19.50'}}]};
    const ctx={oid:'o1',api:async path=>{calls.push(path);return data;},mutation:async(path,body)=>mutations.push({path,body:JSON.parse(JSON.stringify(body))}),tr,esc,money:x=>(x/100).toFixed(2),modal:(_title,html,handler)=>{rendered=html;submit=handler;},closeModal(){},user:{id:'owner',role:'owner',order_source_treatments_enabled:true},enabled:true};
    await assert.rejects(()=>module.open({...ctx,enabled:false}));assert.equal(calls.length,0);
    await assert.rejects(()=>module.open({...ctx,user:{id:'driver',role:'driver',order_source_treatments_enabled:true}}));assert.equal(calls.length,0);
    await module.open(ctx);assert(rendered.includes('25.00')&&rendered.includes('19.50')&&rendered.includes('5.50'));assert(rendered.includes('&lt;receipt&gt;')&&!rendered.includes('<receipt>'));
    newButton.onclick();assert.equal(form.elements.corrected_total.required,true);assert.equal(form.elements.followup_user_id.required,false);
    await submit(new Map([['kind','price_correction'],['corrected_total','19.50'],['evidence_reference','ORIGINAL-PRICE-001'],['note','Actual original price was 19.50, confirmed by source']]));
    assert.deepEqual(mutations[0],{path:'/api/accounting/order-financial-treatments/orders/o1',body:{expected_version:4,case_id:'case1',kind:'price_correction',expected_collected_halalas:1950,evidence_reference:'ORIGINAL-PRICE-001',note:'Actual original price was 19.50, confirmed by source',corrected_total_halalas:1950}});
    newButton.onclick();await assert.rejects(()=>submit(new Map([['kind','price_correction'],['corrected_total','19'],['evidence_reference','Source 001'],['note','This must not reduce below actual collection']])));assert.equal(mutations.length,1);
    form.elements.kind.value='outstanding';form.elements.kind.onchange();assert.equal(form.elements.corrected_total.required,false);assert.equal(form.elements.followup_user_id.required,true);
    await submit(new Map([['kind','outstanding'],['liable_party_type','customer'],['liable_party_name','Documented customer'],['followup_user_id','accountant'],['due_on','2026-10-27'],['evidence_reference','DEBT-001'],['note','Remaining 5.50 is documented and assigned for collection']]));
    assert.equal(mutations[1].body.kind,'outstanding');assert(!('corrected_total_halalas' in mutations[1].body));
    data.treatments[0].payload.kind='outstanding';approve[0].onclick();await submit(new Map([['confirmed','on'],['allow_period_approval_with_outstanding','on'],['evidence_reference','INDEPENDENT-001'],['note','Reviewed independently; amount remains outstanding']]));
    assert.equal(mutations[2].body.allow_period_approval_with_outstanding,true);assert.equal(mutations[2].body.confirmed,true);
    assert.equal(data.original_total_halalas,2500);assert.equal(data.actual_collected_halalas,1950);
    data.allowed_kinds=['price_correction'];data.treatments[0].payload.kind='price_correction';
    await module.open({...ctx,user:{id:'accountant',role:'admin',invoice_price_corrections_enabled:true}});
    assert(!rendered.includes('data-source-treatment-approve='));assert(!rendered.includes('data-source-treatment-reject='));assert(!rendered.includes('data-source-treatment-return='));
    newButton.onclick();assert(!rendered.includes('<option value="outstanding">'));
    await assert.rejects(()=>submit(new Map([['kind','outstanding']])));
    data.treatments[0].status='returned';data.treatments[0].payload.case_id='case1';
    await module.open(ctx);newButton.onclick();
    await submit(new Map([['kind','price_correction'],['corrected_total','19.50'],['evidence_reference','REVISED-PRICE-001'],['note','Additional supporting evidence attached as requested']]));
    assert.equal(mutations[3].body.resubmits_treatment_id,'t1');
    data.treatments[0].status='pending';await module.open(ctx);
    returns[0].onclick();await submit(new Map([['note','Please attach the original approved menu document']]));
    assert(mutations[4].path.endsWith('/t1/return'));assert.equal(mutations[4].body.note,'Please attach the original approved menu document');
    rejects[0].onclick();await submit(new Map([['note','Evidence does not support a change in the original price']]));
    assert(mutations[5].path.endsWith('/t1/reject'));
    data.treatments[0].requested_by='owner';
    await module.open(ctx);assert(!rendered.includes('data-source-treatment-approve='));assert(!rendered.includes('data-source-treatment-return='));

  }
  console.log('PASS: AR/EN invoice correction gating, owner-only decisions, linked resubmission and preserved original claim');
})().catch(e=>{console.error(e);process.exitCode=1;});
