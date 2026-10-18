const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const helper=source.slice(source.indexOf('  let alertAudio='),source.indexOf('  async function enableAlertSound'));
let now=0,tones=[],oscillators=[];
const ctx={Date:{now:()=>now},Map,Set,state:{user:{id:'one'}},setInterval:()=>{},
 audio:{state:'running',currentTime:0,destination:{},createOscillator(){const o={frequency:{},connect(){},disconnect(){},start(){this.started=true},stop(){this.stopped=true;this.onended?.()}};oscillators.push(o);return o},createGain(){return {gain:{value:0},connect(){},disconnect(){}}}}};
vm.createContext(ctx);vm.runInContext(helper+'\nalertAudio=audio;alertTone=(...args)=>tones.push(args);',Object.assign(ctx,{tones}));
ctx.syncAlertChannel('orders',['order1']);assert.equal(tones.length,2);assert.equal(tones[0][0],660);
ctx.syncAlertChannel('messages',['chat1']);assert.equal(tones.length,4);assert.equal(tones[2][0],1046);
now=29999;ctx.tickAlertSounds();assert.equal(tones.length,4);
now=30000;ctx.syncAlertChannel('orders',['order1']);ctx.syncAlertChannel('messages',['chat1']);assert.equal(tones.length,8);
now=59999;ctx.syncAlertChannel('orders',['order1']);ctx.syncAlertChannel('messages',['chat1']);assert.equal(oscillators.length,0);
now=60000;ctx.tickAlertSounds();assert.equal(oscillators.length,2);assert.ok(oscillators.every(o=>o.started&&!o.stopped));
ctx.syncAlertChannel('messages',[]);assert.ok(oscillators[1].stopped);assert.ok(!oscillators[0].stopped);
ctx.syncAlertChannel('orders',[]);assert.ok(oscillators[0].stopped);
// Repeated polling never resets the unanswered item's escalation clock.
now=61000;ctx.syncAlertChannel('orders',['order2']);assert.equal(tones.length,10);
now=91000;ctx.syncAlertChannel('orders',['order2']);assert.equal(tones.length,12);
now=121000;ctx.syncAlertChannel('orders',['order2']);assert.equal(oscillators.length,3);
// Stop an unverified stale alert rather than sound forever after a lost connection.
now=166001;ctx.tickAlertSounds();assert.ok(oscillators[2].stopped);
ctx.syncAlertChannel('orders',['order2']);assert.equal(oscillators.length,4);
ctx.state.user=null;ctx.tickAlertSounds();assert.ok(oscillators[3].stopped);
ctx.state.user={id:'another'};ctx.tickAlertSounds();assert.equal(oscillators.length,4);
console.log('PASS: distinct bells, initial pair, 30s pair, continuous at 60s, independent reply/acceptance stop, stale/session safety.');
