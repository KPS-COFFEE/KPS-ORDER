const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const fn=source.slice(source.indexOf('  async function collection('),source.indexOf('  function reasonModal'));
(async()=>{
for(const language of ['ar','en'])for(const role of ['driver','branch_manager'])for(const method of ['cash','card','split']){
 let html,submit,payload;
 const fields={'select[name=method]':{value:'cash'},'input[name=cash_part]':{value:'4'},'#split-fields':{},'#card-preview':{},'#change-preview':{}};
 if(role!=='driver'){fields['input[name=tendered]']={value:'12'};fields['input[name=reference]']={};}
 const form={addEventListener:()=>{}};
 const ctx={state:{user:{role}},tr:(a,b)=>language==='ar'?a:b,esc:String,money:x=>(x/100).toFixed(2),cents:x=>Math.round(Number(x)*100),api:async()=>({due_halalas:1200}),modal:(title,h,cb)=>{html=h;submit=cb},amountField:(label,name)=>`<input name="${name}">`,field:(label,name)=>`<input name="${name}">`,check:(label,name)=>`<input name="${name}">`,notice:String,formEnd:label=>`<button>${label}</button>`,$:s=>s==='#modal-body form'?form:fields[s],mutation:async(path,p)=>{payload=p},closeModal:()=>{},loadPage:async()=>{}};
 vm.createContext(ctx);vm.runInContext(fn,ctx);await ctx.collection('order',999,method);
 assert.equal(fields['select[name=method]'].value,method);
 for(const name of ['reference','tendered','confirmed'])assert.equal(html.includes(`name="${name}"`),role!=='driver');
 if(role!=='driver')assert.equal(fields['input[name=reference]'].required,method!=='cash');
 const values={method,cash_part:'4',tendered:'12',reference:role==='driver'?'':'TEST'};
 const fd={get:k=>values[k],has:k=>role!=='driver'&&k==='confirmed'};
 await submit(fd);
 assert.equal(payload.amount_halalas,1200);assert.equal(payload.method,method);
 assert.equal(payload.tendered_halalas,method==='card'?null:role==='driver'?(method==='split'?400:1200):1200);
 if(method==='split'){assert.equal(payload.cash_halalas,400);assert.equal(payload.card_halalas,800);values.cash_part='13';await assert.rejects(()=>submit(fd));}
}
console.log('PASS: AR/EN driver cash/card/split confirmation, exact payloads, default method, split boundaries; branch controls preserved.');
})().catch(e=>{console.error(e);process.exit(1)});
