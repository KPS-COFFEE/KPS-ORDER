const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const helper=source.slice(source.indexOf('  let alertAudio='),source.indexOf('  async function enableAlertSound'));
let now=0,tones=[],oscillators=[];
const ctx={Date:{now:()=>now},Map,Set,document:{addEventListener(){}},state:{user:{id:'one'}},setInterval:()=>{},
 audio:{state:'running',currentTime:0,destination:{},createOscillator(){const o={frequency:{},connect(){},disconnect(){},start(){this.started=true},stop(){this.stopped=true;this.onended?.()}};oscillators.push(o);return o},createGain(){return {gain:{value:0},connect(){},disconnect(){}}}}};
vm.createContext(ctx);vm.runInContext(helper+'\nalertAudio=audio;alertTone=(...args)=>{tones.push(args);};',Object.assign(ctx,{tones}));
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
// A failed/offline poll is not an acknowledgment and must not mute pending work.
now=166001;ctx.tickAlertSounds();assert.ok(!oscillators[2].stopped);
ctx.syncAlertChannel('orders',['order2']);assert.equal(oscillators.length,3);
ctx.state.user=null;ctx.tickAlertSounds();assert.ok(oscillators[2].stopped);
ctx.state.user={id:'another'};ctx.tickAlertSounds();assert.equal(oscillators.length,3);
// Owner-configured delays and pulse lengths, not a local employee preference.
vm.runInContext('alertPolicy={first_after_seconds:5,second_after_seconds:20,continuous_after_seconds:40,first_ring_seconds:2,second_ring_seconds:3}',ctx);
now=200000;ctx.syncAlertChannel('orders',['configured']);assert.equal(tones.length,12);
now=205000;ctx.tickAlertSounds();assert.equal(tones.length,14);assert.equal(tones.at(-1)[2],2);
now=220000;ctx.syncAlertChannel('orders',['configured']);assert.equal(tones.length,16);assert.equal(tones.at(-1)[2],3);
now=240000;ctx.syncAlertChannel('orders',['configured']);assert.equal(oscillators.length,4);
ctx.syncAlertChannel('orders',[]);assert.ok(oscillators[3].stopped);
console.log('PASS: distinct bells, owner-configured delays/durations, continuous until confirmed action, independent stop, network loss does not mute, session safety.');
