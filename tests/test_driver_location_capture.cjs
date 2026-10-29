const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/assets/driver-location.js','utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function setup(){
  const writes=[],elements=[],events={},watches=[],intervals=[];
  let now=Date.parse('2026-10-29T12:00:00Z'),user={id:'driver1',role:'driver'},nextShift='shift1',failure=null;
  class Clock extends Date{static now(){return now;}}
  const document={hidden:false,createElement(){const e={listeners:{},setAttribute(){},addEventListener(k,fn){this.listeners[k]=fn;}};elements.push(e);return e;},addEventListener(k,fn){events[k]=fn;}};
  const navigator={onLine:true,geolocation:{watchPosition(success,error){watches.push({success,error});return watches.length;},clearWatch(){},getCurrentPosition(){}}};
  const window={addEventListener(k,fn){events[k]=fn;}};
  const context={window,document,navigator,Date:Clock,Number,crypto:{randomUUID:()=>`capture-${writes.length+1}`},setInterval:fn=>{intervals.push(fn);return intervals.length;},clearInterval(){}};
  vm.runInNewContext(source,context);
  const controller=window.KPSDriverLocation.init({host:{append(){}},getUser:()=>user,tr:(ar,en)=>en,api:async(path,options)=>{
    if(path==='/api/shifts/current')return {shifts:nextShift?[{id:nextShift,user_id:user.id,kind:'driver',status:'open'}]:[]};
    writes.push({path,options});if(failure)throw failure;
    return {accepted_ids:options.body.points?.map(p=>p.id)||[],received_at:new Date(now+5000).toISOString()};
  }});
  return {controller,elements,writes,events,document,navigator,watches,advance:ms=>{now+=ms;},changeUser:()=>{user={id:'driver2',role:'driver'};},setShift:id=>{nextShift=id;},fail:e=>{failure=e;},position:age=>({timestamp:now-age,coords:{latitude:28.4,longitude:45.9,accuracy:8}})};
}
(async()=>{
  const h=setup();await h.controller.refresh();assert.equal(h.watches.length,0,'Never enable consent automatically');
  await h.elements[0].listeners.click();assert.equal(h.watches.length,1);
  const first=h.position(15000);h.watches[0].success(first);await tick();
  const sent=h.writes[0];assert.equal(sent.path,'/api/operations/driver-location-batch');assert.equal(sent.options.body.shift_id,'shift1');
  const point=sent.options.body.points[0];assert.equal(point.captured_at,new Date(first.timestamp).toISOString());assert.equal(point.accuracy_m,8);assert(point.id);
  assert(h.elements[1].textContent.includes('Location shared'));
  h.watches[0].success(h.position(0));await tick();assert.equal(h.writes.length,1,'Preserve 20 second rate bound');
  h.advance(25000);h.watches[0].success(h.position(31000));await tick();assert.equal(h.writes.length,1,'Reject stale fixes');
  h.watches[0].success(h.position(-1));await tick();assert.equal(h.writes.length,1,'Reject future clocks');
  const bad=h.position(0);bad.coords.accuracy=-1;h.watches[0].success(bad);await tick();assert.equal(h.writes.length,1,'Reject negative accuracy');
  h.document.hidden=true;h.events.visibilitychange();h.watches[0].success(h.position(0));await tick();assert.equal(h.writes.length,1,'Old watch cannot send while hidden');
  h.document.hidden=false;h.events.visibilitychange();await tick();assert.equal(h.watches.length,2);
  h.watches[0].success(h.position(0));await tick();assert.equal(h.writes.length,1,'Generation prevents stale callbacks');
  h.watches[1].success(h.position(0));await tick();assert.equal(h.writes.length,2);
  await h.elements[0].listeners.click();assert.equal(h.writes.at(-1).options.method,'DELETE');assert.equal(h.writes.at(-1).path,'/api/operations/driver-location');
  h.advance(25000);h.watches[1].success(h.position(0));await tick();assert.equal(h.writes.length,3,'Explicit stop prevents more samples');
  const revoked=setup();await revoked.controller.refresh();await revoked.elements[0].listeners.click();revoked.fail({status:401});revoked.watches[0].success(revoked.position(0));await tick();
  revoked.advance(25000);revoked.watches[0].success(revoked.position(0));await tick();assert.equal(revoked.writes.length,1,'Revocation halts sharing');
  const shifted=setup();await shifted.controller.refresh();await shifted.elements[0].listeners.click();shifted.setShift('shift2');await shifted.controller.refresh();shifted.watches[0].success(shifted.position(0));await tick();assert.equal(shifted.writes.length,0,'Shift change requires fresh opt-in');
  console.log('PASS: capture clock and shift/session batch contract; consent, rate, stale/future, hidden, generation, stop and revocation');
})().catch(error=>{console.error(error);process.exitCode=1;});
