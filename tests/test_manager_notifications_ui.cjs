const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/ops.js','utf8');
const a=source.indexOf('  async function openManagerNotification()'),b=source.indexOf('  function staffActions(',a);
for(const category of ['action','conversation','shift_report','daily_report'])test(`mail link opens scoped ${category} without changing the signed-in user`,async()=>{
  const calls=[],user={id:'manager',brand_id:'kps'};
  const context={URLSearchParams,state:{user},location:{search:'?manager_notification=n1&operations_brand=enbishan',pathname:'/ops'},
    history:{replaceState:(_state,_title,path)=>calls.push(['history',path])},
    api:async(path,options)=>{calls.push(['read',path,options.brand]);return {category,resource_id:category==='daily_report'?'2026-10-30':'resource',brand_id:'enbishan'};},
    conversation:async id=>calls.push(['conversation',id]),orderDetail:async(id,brand)=>calls.push(['action',id,brand]),
    reportView:async(id,brand)=>calls.push(['shift_report',id,brand]),modal:()=>{},tr:(_a,en)=>en,$:()=>({}),
    window:{KPSOperatingReports:{mount:async(_host,options)=>{calls.push(['daily_report',options.initialDay,options.initialScope,options.brand]);await options.api('/api/operating-reports');}}},
    setStatus:()=>assert.fail('Valid mail link must open its resource')};
  vm.createContext(context);vm.runInContext(source.slice(a,b),context);await context.openManagerNotification();
  assert.deepEqual(calls[0],['read','/api/manager-notifications/n1','enbishan']);
  assert.equal(context.state.user,user);
  assert.ok(calls.some(row=>row[0]===category));
  assert.deepEqual(calls.at(-1),['history','/ops?operations_brand=enbishan']);
  if(category==='daily_report')assert.ok(calls.some(row=>row[0]==='read'&&row[1]==='/api/operating-reports'&&row[2]==='enbishan'));
});
