const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const floatHelpers=source.slice(source.indexOf('  function fixedFloatMarkup('),source.indexOf('  async function floatRegister('));
const helper=floatHelpers+source.slice(source.indexOf('  function terminalComparison('),source.indexOf('  async function shiftDetail('));
(async()=>{
 for(const lang of ['ar','en']) for(const float of [0,20000,80000]){
  let markup='',submit,sent;const elements=new Map();
  const element=selector=>{if(!elements.has(selector))elements.set(selector,{value:'0',textContent:'',addEventListener(){}});return elements.get(selector)};
  const ctx={state:{user:{id:'driver',role:'driver'}},tr:(ar,en)=>lang==='ar'?ar:en,esc:String,num:String,money:String,shiftLabel:()=> 'D1',statusName:String,
   api:async()=>({draft:null}),people:async()=>({closure_manager_shifts:[{id:'m',user_id:'manager',display_name:'Manager'}]}),
   notice:String,amountField:(label,name)=>`<label>${label}<input name="${name}"></label>`,inspectionFields:()=>'<section>Vehicle inspection</section>',inspectionValue:()=>({odometer_km:100}),
   check:(label,name)=>`<input type="checkbox" name="${name}">${label}`,formEnd:()=>'<p class="form-error"></p><button type="submit">Submit</button>',
   modal:(title,html,callback)=>{markup=html;if(callback)submit=callback;},$:element,closeModal(){},loadPage:async()=>{},
   mutation:async(path,body)=>{sent={path,body};return{}},cents:v=>{if(!/^\d+(\.\d{1,2})?$/.test(v))throw Error('Invalid amount');return Math.round(Number(v)*100)}};
  vm.createContext(ctx);vm.runInContext(helper,ctx);
  await ctx.driverBlindClosure({shift:{id:'s',user_id:'driver',kind:'driver',status:'closing',manager_shift_id:'m',permanent_float:{amount:float}}});
  assert.equal((markup.match(/<input name=/g)||[]).length,2);
  assert.equal(element('[name=counted]').value,'');assert.equal(element('[name=terminal_total]').value,'');
  for(const label of ['المتوقع','الفرق','Net cash','Expected','Difference','Collections'])assert.ok(!markup.includes(label),label);
  const fd=new Map([['counted','750.00'],['terminal_total','110.50'],['confirm_closure','on']]);
  await submit(fd);
  assert.equal(sent.body.counted_halalas,75000);assert.equal(sent.body.handover_halalas,Math.max(75000-float,0));assert.equal(sent.body.retained_halalas,Math.min(75000,float));
  assert.equal(sent.body.terminal_total_halalas,11050);assert.equal(sent.body.handover.to_shift_id,'m');assert.ok(sent.body.vehicle_inspection);
  assert.ok(!('expected_halalas' in sent.body));assert.ok(markup.includes(lang==='ar'?'تم إرسال المبالغ':'Your amounts were sent'));
 }
 console.log('PASS: Arabic/English blind form, blank initial amounts, approved float retained, handover split, terminal input and completion screen.');
})().catch(e=>{console.error(e);process.exitCode=1});
