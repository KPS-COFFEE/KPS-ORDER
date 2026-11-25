const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const helper=source.slice(source.indexOf('  let driverAlertSignature='),source.indexOf('  function fixedFloatMarkup('));
(async()=>{
 let tasks=[],tones=[],now=1000,signals=[];
 const host={dataset:{},innerHTML:'',hidden:true};
 const ctx={state:{user:{id:'driver'}},alertBusy:false,can:()=>true,
  api:async()=>({tasks}),tr:(a,b)=>b,esc:String,num:String,
  concealAlertControls:()=>{},
  syncAlertChannel:(kind,ids)=>signals.push({kind,ids:[...ids]}),
  alertAudio:{state:'suspended',currentTime:0},alertTone:(...a)=>tones.push(a),Date:{now:()=>now}};
 vm.createContext(ctx);vm.runInContext(helper,ctx);
 tasks=[{id:'one',driver_id:'driver',status:'assigned'}];
 await ctx.refreshDriverAlerts(host);
 assert.equal(tones.length,0);assert.match(host.innerHTML,/waiting for your acceptance/);
 assert.deepEqual(signals.at(-1),{kind:'driver-orders',ids:['one']});
 ctx.alertAudio.state='running';now=16000;await ctx.refreshDriverAlerts(host);assert.deepEqual(signals.at(-1).ids,['one']);
 now+=1000;await ctx.refreshDriverAlerts(host);assert.deepEqual(signals.at(-1).ids,['one']);
 now+=15000;await ctx.refreshDriverAlerts(host);assert.deepEqual(signals.at(-1).ids,['one']);
 tasks[0].status='accepted';now+=15000;await ctx.refreshDriverAlerts(host);
 assert.deepEqual(signals.at(-1).ids,[]);assert.match(host.innerHTML,/My active orders.*\(1\)/);assert.ok(!host.innerHTML.includes('waiting for your acceptance'));
 tasks.push({id:'two',driver_id:'driver',status:'assigned'});
 now+=1;await ctx.refreshDriverAlerts(host);assert.deepEqual(signals.at(-1).ids,['two']);
 // Another assignment with identical count still rings immediately.
 tasks[1]={id:'three',driver_id:'driver',status:'assigned'};
 now+=1;await ctx.refreshDriverAlerts(host);assert.deepEqual(signals.at(-1).ids,['three']);
 tasks=tasks.filter(t=>t.id!=='one');await ctx.refreshDriverAlerts(host);
 assert.match(host.innerHTML,/My active orders.*\(1\)/);
 tasks=[];now+=15000;await ctx.refreshDriverAlerts(host);assert.deepEqual(signals.at(-1).ids,[]);
 assert.match(host.innerHTML,/My active orders.*\(0\)/);
 console.log('PASS: active driver tasks feed the mandatory shared bell scheduler; acceptance clears only pending alerts; equal-count replacements retained. Timing is certified by test_alert_schedule_ui.cjs.');
})().catch(e=>{console.error(e);process.exitCode=1});
