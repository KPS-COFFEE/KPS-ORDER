const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
  for(const lang of ['ar','en']){
    let inserted='',rendered='',calls=[];
    const error={textContent:''},button={disabled:false},output={set innerHTML(v){rendered=v;},get innerHTML(){return rendered;}};
    const form={elements:{report_ids:{selectedOptions:[{value:'report<&'}]},order_query:{value:'O-25'}},querySelector:s=>s==='button'?button:error};
    const panel={querySelector:s=>s==='form'?form:s==='[data-audit-results]'?output:null};
    const host={insertAdjacentHTML:(_where,html)=>{inserted=html;},querySelector:()=>panel};
    const ctx={window:{KPSAdministrativeGuidance:{render:()=>'<p>Guidance</p>'}},URLSearchParams};vm.createContext(ctx);
    vm.runInContext(fs.readFileSync('public/assets/owner-audit.js','utf8'),ctx);
    const tr=(ar,en)=>lang==='ar'?ar:en,esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const api=async path=>{calls.push(path);return {prepared_features:{bank_matching:false,source_price_corrections:false,admin_assignment:false},people:[{id:'accountant1',name:'Accountant <current>',active:true},{id:'manager1',name:'Manager <next>',active:true}],financial_reviews:['pending_source_correction','corrected_by_approved_document','settled_by_actual_recovery','review_approved_outstanding'].map((state,i)=>({id:'state'+i,financial_state:state,financially_complete:true,overall_financially_complete:false,accounting_review_complete:true,financial_completion_scope:'closure_cash_and_recorded_card',bank_settlement_included:false,bank_matching:{state:'unconfigured'},responsible_role:'accounting',responsible_user_id:'accountant1',next_action:'accounting_review',next_recipient_id:'manager1'})),bank_reconciliations:[{id:'bank1',state:'unconfigured'}],orders:[{id:'<script>bad</script>',total_halalas:2500,payment_status:'unpaid',financial_case:{pending_amount_halalas:550,reason:'Receipt missing'}}],collections:[{id:'p1',amount_halalas:1950,method:'cash'}],closures:[],audit_events:[{id:'event',actor_name:'Accountant',action:'financial.treatment',details:{evidence_reference:'R<01>',outstanding_receivable_halalas:550}}]};};
    ctx.window.KPSOwnerAudit.mount({host,reports:[{id:'report<&',kind:'driver',driver_name:'Driver <one>'}],api,tr,esc,money:v=>(v/100).toFixed(2),user:{role:'driver'},lang});
    assert.equal(inserted,'');
    ctx.window.KPSOwnerAudit.mount({host,reports:[{id:'report<&',kind:'driver',driver_name:'Driver <one>'}],api,tr,esc,money:v=>(v/100).toFixed(2),user:{role:'owner'},lang});
    assert(inserted.includes('Driver &lt;one&gt;')&&!inserted.includes('Driver <one>'));
    await form.onsubmit({preventDefault(){}});
    assert.equal(calls.length,1);assert(calls[0].includes('report_ids=report%3C%26'));
    assert(rendered.includes('25.00')&&rendered.includes('19.50')&&rendered.includes('5.50'));
    assert(rendered.includes('&lt;script&gt;bad&lt;/script&gt;')&&!rendered.includes('<script>bad'));
    assert(rendered.includes('R&lt;01&gt;')&&rendered.includes('PDF')&&rendered.includes('XLSX')&&rendered.includes('JSON'));
    assert(rendered.includes(lang==='ar'?'المستحق مسوى في نطاق السجل — لا يعني مطابقة البنك':'Obligation settled within record scope — no implied bank match'));
    assert(rendered.includes(lang==='ar'?'الاكتمال شامل دليل البنك والمستحقات':'Completion includes bank evidence and obligations'));
    assert(!rendered.includes('closure_cash_and_recorded_card'));
    assert(rendered.includes(lang==='ar'?'الكاش والشبكة المسجلة فقط — مطابقة البنك مستقلة':'Cash and recorded card only — bank matching is separate'));
    assert(!button.disabled);
    for(const code of ['pending_source_correction','corrected_by_approved_document','settled_by_actual_recovery','review_approved_outstanding','unconfigured','accounting_review'])assert(!rendered.includes(code),code+' must be translated');
    assert(rendered.includes('Accountant &lt;current&gt;')&&rendered.includes('Manager &lt;next&gt;'));
    assert(rendered.includes(lang==='ar'?'مطابقة البنك غير مفعلة':'Bank matching is not enabled'));
    assert(rendered.includes(lang==='ar'?'لا يشهد هذا التقرير بمطابقة البنك':'does not certify bank matching'));
    assert(rendered.includes(lang==='ar'?'تجهيزات تطويرية غير مفعلة':'development preparations, not activated'));
    form.elements.report_ids.selectedOptions=Array.from({length:21},(_,i)=>({value:'r'+i}));
    await form.onsubmit({preventDefault(){}});assert.equal(calls.length,1);assert(error.textContent.includes('20'));
  }
  console.log('PASS: owner-only AR/EN audit form, scoped selection, escaped evidence, actual amounts, exports and bounded requests');
})().catch(e=>{console.error(e);process.exitCode=1;});
