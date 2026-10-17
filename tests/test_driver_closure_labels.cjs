const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const helpers=source.slice(source.indexOf('  function shiftLabel('),source.indexOf('  function orderLabel('));
for(const lang of ['ar','en']) {
 const ctx={state:{user:{id:'driver',display_name:'Ahmed'},shifts:[]},tr:(ar,en)=>lang==='ar'?ar:en,esc:v=>String(v).replaceAll('<','&lt;'),datetime:v=>v?new Date(v).toLocaleString('en-GB',{timeZone:'Asia/Riyadh'}):'—',dataItem:(k,v)=>`<dt>${k}</dt><dd dir="auto">${v}</dd>`,statusName:String};
 vm.createContext(ctx);vm.runInContext(helpers,ctx);
 const shift={id:'shift-unchanged',kind:'driver',user_id:'driver',shift_no:1,status:'open',delivery_operational_date:'2026-10-16',opened_at:'2026-10-16T22:00:00Z',closure_authorization:{status:'requested',requested_at:'2026-10-16T23:00:00Z'}};
 const before=JSON.stringify(shift),pending=ctx.driverClosureIdentity(shift);
 assert.ok(pending.includes('D-000001')&&pending.includes('2026-10-16')&&pending.includes('Ahmed'));
 assert.ok(pending.includes(lang==='ar'?'بانتظار موافقة الإنهاء':'Awaiting end approval'));
 assert.ok(pending.includes(lang==='ar'?'لم تسجل':'Not recorded'));
 assert.ok(!pending.includes('23:00')&&!pending.includes('02:00'));
 assert.equal(JSON.stringify(shift),before);assert.ok(pending.includes('17/10/2026, 01:00:00'));
 shift.status='submitted';shift.submitted_at='2026-10-17T01:15:00Z';shift.closed_at='2026-10-17T02:30:00Z';
 const submitted=ctx.driverClosureIdentity(shift);assert.ok(submitted.includes('04:15:00')&&!submitted.includes('05:30:00'));
 assert.ok(submitted.includes(lang==='ar'?'بانتظار الاعتماد':'Awaiting approval'));
 shift.vehicle_log={end:{recorded_at:'2026-10-17T00:30:00Z'}};assert.ok(ctx.driverClosureIdentity(shift).includes('03:30:00'));
 assert.ok(pending.includes('<bdi dir="ltr">D-000001</bdi>'));
 assert.equal(ctx.driverClosureIdentity({...shift,kind:'manager'}),'');
}
console.log('PASS: AR/EN persisted operational day, Riyadh midnight crossing, pending permission and financial approval distinct, unchanged D reference.');
