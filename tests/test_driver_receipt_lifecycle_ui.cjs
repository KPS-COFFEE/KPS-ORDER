/* Real modal function: a confirmed zero receipt is not a new blank cash entry;
   a stale close action cannot reopen a closed workday as delegated submission. */
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const start=source.indexOf('  async function simpleDriverClose(id)');
const end=source.indexOf('  async function centralCustody(',start);
assert.ok(start>=0&&end>start);
function harness({language='ar',status='submitted',actual=null,role='delivery_manager'}={}){
 const calls=[],dom=new Map();
 const d={shift:{id:'driver-shift',kind:'driver',status,user_id:'driver',manager_shift_id:'manager',submission:{counted_halalas:1000,handover_halalas:1000,retained_halalas:0}},
  transfers:actual===null?[]:[{from_shift_id:'driver-shift',closure:true,status:'confirmed',amount_halalas:actual}],tasks:[]};
 const context={state:{user:{id:'manager',role},shifts:[]},tr:(ar,en)=>language==='en'?en:ar,esc:String,money:n=>String(n),num:String,
  api:async()=>d,shiftDetail:async id=>calls.push(['readonly',id]),centralCloseDialog:async()=>{throw Error('unexpected shared route');},
  incentiveReviewPanel:async()=>'',driverClosureIdentity:()=>'',driverOrderReviewControl:()=>'',externalEnbishanPanel:()=>'',terminalComparison:()=>'',
  inspectionFields:()=>'',currentOdometer:()=>0,shiftLabel:()=>'',notice:s=>`<p>${s}</p>`,
  dataItem:(name,value)=>`<div><dt>${name}</dt><dd>${value}</dd></div>`,amountField:(label,name)=>`<input name="${name}" required>`,
  textarea:()=>'',check:()=>'<input name="confirmed" type="checkbox">',formEnd:()=>'<button type="submit">Submit</button>',
  modal:(title,html,submit)=>{calls.push(['modal',title]);context.html=html;context.submit=submit;},
  $:selector=>{if(!dom.has(selector))dom.set(selector,{value:'UNTOUCHED'});return dom.get(selector);},
  cents:value=>{if(value==null||value==='')throw Error('blank actual receipt');return Number(value)*100;},
  mutation:async(path,body)=>calls.push(['mutation',path,body]),closeModal:()=>calls.push(['closed']),loadPage:async()=>{},setStatus:()=>{},
  inspectionValue:()=>({}),apiError:()=>'',isAdmin:()=>role==='admin'};
 vm.createContext(context);vm.runInContext(source.slice(start,end),context);
 return {context,calls,dom};
}
(async()=>{
 for(const language of ['ar','en'])for(const actual of [0,900,1100]){
  const {context,calls,dom}=harness({language,actual});await context.simpleDriverClose('driver-shift');
  assert.ok(context.html.includes('data-saved-actual-receipt'));
  assert.ok(!context.html.includes('name="received"'));
  assert.ok(!context.html.includes('data-action="return-shift"'));
  await context.submit(new Map([['confirmed','on']]));
  const writes=calls.filter(c=>c[0]==='mutation');assert.equal(writes.length,1);assert.equal(writes[0][1],'/api/shifts/driver-shift/driver-close');
  assert.equal(writes[0][2].received_halalas,actual);assert.equal(writes[0][2].counted_halalas,null);
  assert.equal(dom.has('#simple-driver-close-form [name=received]'),false);
 }
 for(const language of ['ar','en']){
  const {context,dom}=harness({language});await context.simpleDriverClose('driver-shift');
  assert.ok(context.html.includes('name="received"'));assert.equal(dom.get('#simple-driver-close-form [name=received]').value,'');
  await assert.rejects(()=>context.submit(new Map([['confirmed','on']])) ,/blank actual receipt/);
 }
 for(const fn of ['simpleDriverClose','approveShift'])for(const role of ['delivery_manager','admin']){
  if(fn==='simpleDriverClose'&&role==='admin')continue;
  const {context,calls}=harness({status:'closed',actual:0,role});await context[fn]('driver-shift');
  assert.equal(calls.length,1);assert.equal(calls[0][0],'readonly');assert.equal(context.submit,undefined);
 }
 console.log('PASS AR/EN: recorded actual cash including zero is reused once; new receipt stays blank; stale closed actions are read-only.');
})().catch(error=>{console.error(error);process.exitCode=1;});
