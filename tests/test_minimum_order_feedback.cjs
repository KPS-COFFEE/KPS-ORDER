const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/customer.js','utf8');
const translations=source.slice(source.indexOf('  const uiStrings='),source.indexOf('  let bindingCleanupPending='));
const money=source.slice(source.indexOf('  const locale='),source.indexOf('  const key ='));
const helpers=source.slice(source.indexOf('  function knownMessage('),source.indexOf('  function errorMessage('));
const api=source.slice(source.indexOf('  function minimumOrderMessage('),source.indexOf('  async function busy('));
const detail={code:'MINIMUM_ORDER_NOT_MET',message:'السلة أقل من الحد الأدنى المعتمد للتوصيل.',
  minimum_order_halalas:3707,subtotal_halalas:1950,remaining_halalas:1757,currency:'SAR'};
async function main(){
  const context={Intl,Number,Map,Error,console,state:{session:{user:null,csrf_token:null}},key:()=> 'synthetic-only',renderAccount(){}};
  vm.createContext(context);vm.runInContext("let language='en';\n"+translations+money+helpers+api,context);
  async function request(errorDetail){context.fetch=async()=>({ok:false,status:409,json:async()=>({detail:errorDetail})});
    try{await context.api('/api/quote');assert.fail('Quote must stay rejected');}catch(error){assert.equal(error.code,'MINIMUM_ORDER_NOT_MET');return error;}}
  for(const language of ['ar','en']){
    vm.runInContext('language='+JSON.stringify(language),context);
    const error=await request(detail),text=String(error.localizedMessage);
    const format=n=>new Intl.NumberFormat(language==='ar'?'ar-SA':'en-SA',{minimumFractionDigits:2,maximumFractionDigits:2}).format(n);
    assert.ok(text.includes(format(37.07))&&text.includes(format(17.57)),text);
    assert.match(text,language==='ar'?/قبل رسوم التوصيل/:/before delivery fees/);
    assert.equal(error.status,409);assert.equal(error.details,detail);
    context.savedMessage=error.localizedMessage;vm.runInContext("language=language==='ar'?'en':'ar'",context);
    assert.notEqual(String(context.savedMessage),text,'visible feedback can change language without requoting');
    vm.runInContext('language='+JSON.stringify(language),context);
    for(const invalid of [{}, {...detail,remaining_halalas:1}, {...detail,minimum_order_halalas:'3707'},
      {...detail,subtotal_halalas:-1}, {...detail,currency:'USD'}, {...detail,remaining_halalas:NaN}]){
      const fallback=await request({code:'MINIMUM_ORDER_NOT_MET',message:detail.message,...invalid});
      assert.equal(String(fallback.localizedMessage),language==='ar'?detail.message:'Your basket is below the delivery minimum.');
    }
  }
  console.log('PASS: actual customer API minimum feedback, authoritative amounts, AR/EN formatting, language switching, invalid/legacy fallback and unchanged rejection.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
