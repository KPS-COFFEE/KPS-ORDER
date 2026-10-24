const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const helper=source.slice(source.indexOf('  function driverOfferCards'),source.indexOf('  async function tasks()'));
for(const language of ['ar','en']){
 const ctx={state:{},tr:(a,b)=>language==='ar'?a:b,esc:String,num:String,dataItem:(k,v)=>`${k}: ${v}`};
 vm.createContext(ctx);vm.runInContext(helper,ctx);
 const d={today:'2026-10-15',eligible:8,rows:[{day:'2026-10-15',eligible:3},{day:'2026-10-14',eligible:5}],expected_halalas:99999};
 const page=ctx.driverHomeSummary({status:'open',available:true},[],d);
 assert.ok(page.includes('3')&&page.includes('8'));assert.ok(!page.includes('99999'));
 assert.ok(ctx.driverHomeSummary(null,[],d).includes(language==='ar'?'غير متاح':'Unavailable'));
 assert.ok(ctx.driverHomeSummary({status:'open'},[{}],d).includes(language==='ar'?'مشغول':'Busy'));
 assert.equal(ctx.driverOfferCards([]),'');
 const offer=ctx.driverOfferCards([{id:'one',order_no:2,branch:'B',customer_phone:'SECRET'}]);
 assert.ok(!offer.includes('SECRET'));assert.ok(offer.includes('dispatch-accept')&&offer.includes('dispatch-decline'));
}
console.log('PASS: AR/EN driver availability, canonical day/month counts, private offer projection, disabled empty inbox.');
