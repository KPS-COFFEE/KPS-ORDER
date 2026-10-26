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
    const api=async path=>{calls.push(path);return {orders:[{id:'<script>bad</script>',total_halalas:2500,payment_status:'unpaid',financial_case:{pending_amount_halalas:550,reason:'Receipt missing'}}],collections:[{id:'p1',amount_halalas:1950,method:'cash'}],closures:[],audit_events:[{id:'event',actor_name:'Accountant',action:'financial.treatment',details:{evidence_reference:'R<01>',outstanding_receivable_halalas:550}}]};};
    ctx.window.KPSOwnerAudit.mount({host,reports:[{id:'report<&',kind:'driver',driver_name:'Driver <one>'}],api,tr,esc,money:v=>(v/100).toFixed(2),user:{role:'driver'},lang});
    assert.equal(inserted,'');
    ctx.window.KPSOwnerAudit.mount({host,reports:[{id:'report<&',kind:'driver',driver_name:'Driver <one>'}],api,tr,esc,money:v=>(v/100).toFixed(2),user:{role:'owner'},lang});
    assert(inserted.includes('Driver &lt;one&gt;')&&!inserted.includes('Driver <one>'));
    await form.onsubmit({preventDefault(){}});
    assert.equal(calls.length,1);assert(calls[0].includes('report_ids=report%3C%26'));
    assert(rendered.includes('25.00')&&rendered.includes('19.50')&&rendered.includes('5.50'));
    assert(rendered.includes('&lt;script&gt;bad&lt;/script&gt;')&&!rendered.includes('<script>bad'));
    assert(rendered.includes('R&lt;01&gt;')&&rendered.includes('PDF')&&rendered.includes('XLSX')&&rendered.includes('JSON'));
    assert(!button.disabled);
    form.elements.report_ids.selectedOptions=Array.from({length:21},(_,i)=>({value:'r'+i}));
    await form.onsubmit({preventDefault(){}});assert.equal(calls.length,1);assert(error.textContent.includes('20'));
  }
  console.log('PASS: owner-only AR/EN audit form, scoped selection, escaped evidence, actual amounts, exports and bounded requests');
})().catch(e=>{console.error(e);process.exitCode=1;});
