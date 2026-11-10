const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
  for(const lang of ['ar','en']){
    let rendered='',submit,mutations=[],newButton={addEventListener:(_type,fn)=>{newButton.onclick=fn;}},approveButtons=[{dataset:{refundAllocationApprove:'a1'}}],rejectButtons=[];
    const document={querySelector:()=>newButton,querySelectorAll:s=>s==='[data-refund-allocation-approve]'?approveButtons:rejectButtons};
    const context={window:{},document};vm.createContext(context);vm.runInContext(fs.readFileSync('public/assets/refund-item-allocation.js','utf8'),context);
    const tr=(ar,en)=>lang==='ar'?ar:en,esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const data={can_propose:true,can_approve:true,unallocated_refunds:[{id:'refund1',amount_halalas:500,method:'cash',reason:'Earlier payment returned'}],items:[{product_id:'paid',name:'Paid <coffee>',quantity:2,line_total_halalas:1000},{product_id:'free',name:'Free cookie',quantity:1,line_total_halalas:0}],previous_quantities:{paid:1},allocations:[{id:'a1',status:'pending',requested_by:'other',requester_name:'Accountant',payload:{refund_ids:['refund1'],line_quantities:{paid:1},amount_halalas:500,evidence_reference:'<receipt>',note:'Original paid item returned'}}]};
    await context.window.KPSRefundItemAllocation.open({oid:'order1',api:async()=>data,mutation:async(path,body)=>mutations.push({path,body:JSON.parse(JSON.stringify(body))}),tr,esc,money:x=>(x/100).toFixed(2),modal:(_title,html,handler)=>{rendered=html;submit=handler;},closeModal(){},user:{id:'reviewer'}});
    assert(rendered.includes('&lt;receipt&gt;')&&!rendered.includes('<receipt>'));
    approveButtons[0].onclick();await submit(new Map([['confirmed','on'],['evidence_reference','Evidence reviewed'],['note','Independently reviewed actual earlier refund']]));
    assert.equal(mutations[0].path,'/api/accounting/refund-item-allocations/orders/order1/a1/approve');
    assert.equal(mutations[0].body.confirmed,true);
    newButton.onclick();assert(rendered.includes('Paid &lt;coffee&gt;')&&rendered.includes('name="quantity_1" min="0" max="1" step="1" value="0" disabled'));
    const body=new Map([['refund_ids','refund1'],['quantity_0','1'],['quantity_1','0'],['evidence_reference','Actual item receipt'],['note','Link earlier refund to returned paid coffee']]);body.getAll=key=>key==='refund_ids'?['refund1']:[];
    await submit(body);assert.deepEqual(mutations[1].body.line_quantities,{paid:1});assert.equal(mutations[1].path,'/api/accounting/refund-item-allocations/orders/order1');
    newButton.onclick();body.set('quantity_0','2');await assert.rejects(()=>submit(body));assert.equal(mutations.length,2);
    body.set('quantity_0','0');body.set('quantity_1','1');await assert.rejects(()=>submit(body));assert.equal(mutations.length,2);
  }
  console.log('PASS: AR/EN historical refund evidence, independent approval, free-item zero value and remaining paid quantities');
})().catch(e=>{console.error(e);process.exitCode=1;});
