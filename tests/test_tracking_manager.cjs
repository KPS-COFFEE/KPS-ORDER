'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/ops.js','utf8');
function harness(){
 const from=source.indexOf('  let trackingPlanSelection='),to=source.indexOf('  function dispatchQuery()',from);
 const calls=[],state={user:{id:'manager',brand_id:'kps',branch_ids:['b']},page:'dashboard',branches:[],dispatchDrivers:[{id:'d',shift_id:'s',gps:{updated_at:new Date().toISOString()}}]};
 const context={state,Date,JSON,Number,Math,document:{hidden:false,addEventListener(){}},navigator:{onLine:true},can:()=>true,$:()=>null,mutation:async(url,body)=>{calls.push({url,body});return {status:'ready',calculated_at:new Date().toISOString(),stops:[]};},tr:(a,b)=>b,esc:String,datetime:String,num:String,notice:String,dataItem:(label,value)=>label+': '+value,branchLabel:()=>'',renderDispatchRows(){}};
 vm.createContext(context);vm.runInContext(source.slice(from,to)+'\nthis.run=selectTrackingPlan;this.refresh=refreshTrackingPlan;this.health=trackingLocationStatus;this.details=trackingDriverDetails;this.publish=publishTrackingPlan;this.setPlan=value=>trackingPlanResult=value;',context);
 return {context,calls,state};
}
test('only selected driver requests bounded automatic road ETA',async()=>{const h=harness();await h.context.refresh();assert.equal(h.calls.length,0);await h.context.run('d');assert.equal(h.calls.length,1);await h.context.refresh();assert.equal(h.calls.length,1);assert.equal(h.calls[0].url,'/api/operations/route-eta');assert.equal(h.calls[0].body.driver_id,'d');});
test('historical/stale captured point suppresses estimates despite fresh receipt',async()=>{const h=harness();h.state.dispatchDrivers[0].gps={captured_at:new Date(Date.now()-300000).toISOString(),updated_at:new Date().toISOString()};assert.equal(h.context.health(h.state.dispatchDrivers[0]).stale,true);await h.context.run('d');assert.equal(h.calls.length,0);assert.match(h.context.details(h.state.dispatchDrivers[0]),/paused until a fresh/);});
test('permission scope replacement invalidates selection and hides previous estimate',async()=>{const h=harness();await h.context.run('d');h.state.user.id='replacement';await h.context.refresh();assert.equal(h.calls.length,1);assert.doesNotMatch(h.context.details(h.state.dispatchDrivers[0]),/Deliveries complete/);});
test('hidden or offline screen never automatically requests estimate',async()=>{const h=harness();h.context.document.hidden=true;await h.context.run('d');assert.equal(h.calls.length,0);h.context.document.hidden=false;h.context.navigator.onLine=false;await h.context.refresh();assert.equal(h.calls.length,0);});

test('driver route publication is explicit and preserves selected driver identity',async()=>{const h=harness();await h.context.run('d');assert.equal(h.calls[0].body.publish,undefined);await h.context.publish('d');assert.equal(h.calls[1].body.publish,true);assert.equal(h.calls[1].body.driver_id,'d');await h.context.publish('another');assert.equal(h.calls.length,2);});

function driverHarness(current){
 const from=source.indexOf('  async function renderDriverRoutePanel('),to=source.indexOf('  async function renderDriverDestinationPanel(',from),calls=[],nodes=[];
 function node(tag){const n={tag,isConnected:true,children:[],listeners:{},append(...items){this.children.push(...items);},addEventListener(name,fn){this.listeners[name]=fn;},remove(){this.isConnected=false;}};nodes.push(n);return n;}
 const root=node('root'),stop={id:'dropoff:enb-task',kind:'dropoff',task_id:'enb-task',brand_id:'enbishan',order_no:'E-1',location:{lat:28.4,lng:46.},arrival_at:'2026-10-26T16:00:00Z'};
 const context={state:{user:{id:'driver',role:'driver'},page:'tasks'},document:{createElement:node},$:()=>root,tr:(a,b)=>b,esc:String,datetime:String,Number,encodeURIComponent,api:async path=>path.endsWith('driver-route-plan')?{status:'ready',next_stop:stop}:current,mutation:async(path,body,key,options)=>calls.push({path,body,options}),loadPage:async()=>calls.push({reload:true}),setStatus:message=>calls.push({message})};
 vm.createContext(context);vm.runInContext(source.slice(from,to)+'\nthis.render=renderDriverRoutePanel;',context);return {context,calls,nodes};
}
test('driver records arrival for published side-brand task without confirming order handover',async()=>{
 const h=driverHarness({trip:{id:'trip'},stop:null});await h.context.render({status:'open'});const action=h.nodes.find(n=>n.tag==='button');assert.equal(action.textContent,'Arrived at station');await action.listeners.click();assert.equal(h.calls[0].path,'/api/operations/map-services/trip-stops');assert.equal(h.calls[0].body.kind,'delivery');assert.deepEqual(Array.from(h.calls[0].body.task_ids),['enb-task']);assert.equal(h.calls[0].options.brand,'enbishan');assert.equal(h.calls[1].reload,true);assert.ok(h.calls.every(c=>!c.path?.includes('/tasks/')));
});
test('driver leaves exactly the open station in its canonical brand',async()=>{
 const h=driverHarness({trip:{id:'trip'},stop:{id:'station',brand_id:'enbishan'}});await h.context.render({status:'open'});const action=h.nodes.find(n=>n.tag==='button');assert.equal(action.textContent,'Leave station');await action.listeners.click();assert.equal(h.calls[0].path,'/api/operations/map-services/trip-stops/station/depart');assert.equal(h.calls[0].options.brand,'enbishan');assert.equal(h.calls.length,2);
});

function countdownPlan(now){return {status:'ready',calculated_at:new Date(now).toISOString(),next_stop:{arrival_at:new Date(now+240000).toISOString()},delivery_complete_at:new Date(now+900000).toISOString(),return_at:new Date(now+1380000).toISOString(),stops:[{id:'stop',kind:'dropoff',order_id:'o',order_no:'K-1',brand_id:'kps',customer_name:'Ahmed',arrival_at:new Date(now+240000).toISOString()}]};}
test('manager sees customer countdown and automatic render reduces minutes without declaring arrival',async()=>{
 const h=harness(),now=Date.now();h.context.Date=class extends Date {static now(){return now;}};await h.context.run('d');h.context.setPlan(countdownPlan(now));
 let html=h.context.details(h.state.dispatchDrivers[0]);assert.match(html,/Ahmed · K-1 · About 4 min remaining/);assert.match(html,/Deliveries complete: About 15 min remaining/);assert.match(html,/Branch return: About 23 min remaining/);
 h.context.Date=class extends Date {static now(){return now+60000;}};assert.match(h.context.details(h.state.dispatchDrivers[0]),/Ahmed · K-1 · About 3 min remaining/);
 h.context.setPlan({...countdownPlan(now+60000),stops:[{id:'stop',kind:'dropoff',customer_name:'Ahmed',arrival_at:new Date(now).toISOString()}]});html=h.context.details(h.state.dispatchDrivers[0]);assert.match(html,/Expected time reached — awaiting update/);assert.doesNotMatch(html,/delivered|0 min remaining/);
});
test('fresh GPS cannot keep an expired or malformed road estimate visible',async()=>{
 const h=harness(),now=Date.now();await h.context.run('d');for(const stamp of [new Date(now-121000).toISOString(),'invalid',null,new Date(now+60000).toISOString()]){h.context.setPlan({...countdownPlan(now),calculated_at:stamp});const html=h.context.details(h.state.dispatchDrivers[0]);assert.match(html,/Road estimate expired/);assert.doesNotMatch(html,/Ahmed · K-1|About 4 min remaining/);}
});
test('cooldown preserves an existing fresh estimate only until its freshness expires',async()=>{
 const h=harness(),now=Date.now();h.context.Date=class extends Date {static now(){return now;}};await h.context.run('d');h.context.setPlan(countdownPlan(now));h.context.mutation=async()=>({status:'unavailable',reason:'route_budget_or_cooldown'});
 h.context.Date=class extends Date {static now(){return now+61000;}};await h.context.refresh();assert.match(h.context.details(h.state.dispatchDrivers[0]),/Ahmed · K-1 · About 3 min remaining/);
 h.context.Date=class extends Date {static now(){return now+122000;}};h.state.dispatchDrivers[0].gps.updated_at=new Date(now+122000).toISOString();await h.context.refresh();const html=h.context.details(h.state.dispatchDrivers[0]);assert.match(html,/Estimate unavailable/);assert.doesNotMatch(html,/Ahmed · K-1/);
});
test('order-page customer fallback respects brand identity',async()=>{
 const h=harness(),now=Date.now();await h.context.run('d');const plan=countdownPlan(now);delete plan.stops[0].customer_name;h.state.orders=[{id:'o',brand_id:'enbishan',customer_name:'Foreign customer'}];h.context.setPlan(plan);assert.doesNotMatch(h.context.details(h.state.dispatchDrivers[0]),/Foreign customer/);h.state.orders.push({id:'o',brand_id:'kps',customer_name:'Authorized customer'});assert.match(h.context.details(h.state.dispatchDrivers[0]),/Authorized customer/);
});
