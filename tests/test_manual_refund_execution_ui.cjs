const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
  const source=fs.readFileSync('public/ops.js','utf8');
  const fn=source.split('\n').find(line=>line.startsWith('  async function refundOrder('));
  assert(fn,'refund form function must exist');
  for(const lang of ['ar','en']){
    let submit,html='',writes=[];
    const fields=[];
    const context={tr:(ar,en)=>lang==='ar'?ar:en,esc:String,
      api:async path=>path==='/api/shifts/current'?{shifts:[]}:{order:{items:[],payment_method:'card'}},
      normalizeOrder:x=>x,modal:(_title,markup,handler)=>{html=markup;submit=handler;},
      amountField:()=>'',field:(label,name,value,options)=>{fields.push({name,options});return name;},
      textarea:()=>'',check:()=>'',formEnd:()=>'',notice:()=>'',selectOptions:()=>'',
      cents:x=>Math.round(Number(x)*100),mutation:async(path,body)=>writes.push({path,body}),
      closeModal(){},async loadPage(){},async financialCases(){}};
    vm.createContext(context);vm.runInContext(fn,context);
    await context.refundOrder('order-1');
    assert(fields.some(x=>x.name==='evidence_reference'&&x.options.required));
    const data=new Map([['amount','10'],['refund_method','card'],['reason','Actual refund'],['evidence_reference','SLIP-431']]);
    await assert.rejects(()=>submit(data));assert.equal(writes.length,0);
    data.set('confirmed','on');data.set('evidence_reference','  ');
    await assert.rejects(()=>submit(data));assert.equal(writes.length,0);
    data.set('evidence_reference',' SLIP-431 ');await submit(data);
    assert.equal(writes.length,1);assert.equal(writes[0].body.evidence_reference,'SLIP-431');
    assert.equal(writes[0].body.executed_confirmed,true);
  }
  const amendments=fs.readFileSync('public/assets/order-amendments.js','utf8');
  assert(amendments.includes("'evidence_reference'"));
  assert(amendments.includes('executed_confirmed:true'));
  console.log('PASS: AR/EN refund form rejects missing confirmation/reference and submits execution evidence; amendment path carries proof');
})().catch(error=>{console.error(error);process.exitCode=1;});
