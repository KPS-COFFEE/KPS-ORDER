const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const helper=source.slice(source.indexOf('  let driverAlertSignature='),source.indexOf('  function fixedFloatMarkup('));
(async()=>{
 let tasks=[],tones=[],now=1000;
 const host={dataset:{},innerHTML:'',hidden:true};
 const ctx={state:{user:{id:'driver'}},alertBusy:false,can:()=>true,
  api:async()=>({tasks}),tr:(a,b)=>b,esc:String,num:String,
  alertAudio:{state:'suspended',currentTime:0},alertTone:(...a)=>tones.push(a),Date:{now:()=>now}};
 vm.createContext(ctx);vm.runInContext(helper,ctx);
 tasks=[{id:'one',driver_id:'driver',status:'assigned'}];
 await ctx.refreshDriverAlerts(host);
 assert.equal(tones.length,0);assert.match(host.innerHTML,/waiting for your acceptance/);
 ctx.alertAudio.state='running';now=16000;await ctx.refreshDriverAlerts(host);assert.equal(tones.length,6);
 now+=1000;await ctx.refreshDriverAlerts(host);assert.equal(tones.length,6);
 now+=15000;await ctx.refreshDriverAlerts(host);assert.equal(tones.length,12);
 tasks[0].status='accepted';now+=15000;await ctx.refreshDriverAlerts(host);
 assert.equal(tones.length,12);assert.match(host.innerHTML,/My active orders.*\(1\)/);assert.ok(!host.innerHTML.includes('waiting for your acceptance'));
 tasks.push({id:'two',driver_id:'driver',status:'assigned'});
 now+=1;await ctx.refreshDriverAlerts(host);assert.equal(tones.length,18);
 // Another assignment with identical count still rings immediately.
 tasks[1]={id:'three',driver_id:'driver',status:'assigned'};
 now+=1;await ctx.refreshDriverAlerts(host);assert.equal(tones.length,24);
 tasks=tasks.filter(t=>t.id!=='one');await ctx.refreshDriverAlerts(host);
 assert.match(host.innerHTML,/My active orders.*\(1\)/);
 tasks=[];now+=15000;await ctx.refreshDriverAlerts(host);assert.equal(tones.length,24);
 assert.match(host.innerHTML,/My active orders.*\(0\)/);
 console.log('PASS: persistent active task notification, audio activation, repeating bell, acceptance stops bell, new assignment retriggers.');
})().catch(e=>{console.error(e);process.exitCode=1});
