const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const fn=source.slice(source.indexOf('  async function collection('),source.indexOf('  function reasonModal'));
(async()=>{
for(const lang of ['ar','en']){
 let submit,payload,add;
 const fields={'select[name=method]':{value:'cash'},'input[name=cash_part]':{value:'5'},'#split-fields':{},'#card-preview':{},'#change-preview':{},'#card-payments':{},'#shortfall-fields':{querySelectorAll:()=>[]},'#card-payment-rows':{querySelectorAll:()=>[],insertAdjacentHTML:()=>{}},'#add-card-payment':{addEventListener:(name,cb)=>add=cb},'input[name=shortfall]':{checked:false},'input[name=actual_total]':{value:'19.5'}};
 const form={addEventListener:()=>{}};
 const ctx={state:{user:{role:'driver'}},tr:(a,b)=>lang==='ar'?a:b,esc:String,money:x=>String(x/100),cents:x=>Math.round(Number(x)*100),api:async()=>({due_halalas:2500}),modal:(title,h,cb)=>submit=cb,amountField:(label,name)=>`<input name="${name}">`,field:(label,name)=>`<input name="${name}">`,textarea:(label,name)=>`<textarea name="${name}"></textarea>`,check:(label,name)=>`<input name="${name}">`,notice:String,formEnd:String,$:s=>s==='#modal-body form'?form:fields[s],mutation:async(path,p)=>payload=p,closeModal:()=>{},loadPage:async()=>{}};
 vm.createContext(ctx);vm.runInContext(fn,ctx);
 await ctx.collection('test',2500,'card');add();
 let values={method:'card',card_payment_0:'10',card_payment_1:'15'};
 const fd={get:k=>values[k],has:k=>k==='shortfall'&&fields['input[name=shortfall]'].checked};
 await submit(fd);
 assert.deepEqual(JSON.parse(JSON.stringify(payload.card_payments)),[{amount_halalas:1000},{amount_halalas:1500}]);
 values.card_payment_1='14.99';payload=null;await assert.rejects(()=>submit(fd));assert.equal(payload,null);
 await ctx.collection('test',2500,'cash');fields['input[name=shortfall]'].checked=true;
 values={method:'cash',actual_total:'19.5',shortfall_reason:'Returned free cookie; wrong deduction'};
 await submit(fd);assert.equal(payload.amount_halalas,1950);assert.equal(payload.tendered_halalas,1950);assert.equal(payload.shortfall_reason,values.shortfall_reason);
 values.shortfall_reason=' ';payload=null;await assert.rejects(()=>submit(fd));assert.equal(payload,null);
 values.shortfall_reason='Reason';values.actual_total='26';await assert.rejects(()=>submit(fd));assert.equal(payload,null);
 values.actual_total='25';await assert.rejects(()=>submit(fd));assert.equal(payload,null);
}
console.log('PASS: AR/EN multiple-card payloads, mismatched sums, 25/19.50 actual collection, mandatory reason and overpayment rejection.');
})().catch(e=>{console.error(e);process.exit(1)});
