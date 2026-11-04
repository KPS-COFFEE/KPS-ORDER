const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../public/assets/preparation-routing.js'),'utf8');
function harness(change={}) {
  const data={order_id:'order-1',brand_id:'enbishan',version:7,can_choose:true,
    source_branch:{id:'source',name:'Original'},preparation_branch:{id:'source',name:'Original'},
    branches:[{id:'source',name:'Original',available:true,current:true,distance_from_branch_meters:0,distance_to_customer_meters:2000},
      {id:'target',name:'Target <img src=x>',available:true,distance_from_branch_meters:1500,distance_to_customer_meters:400},
      {id:'denied',name:'Other city',available:false,reason:'Outside city <script>',distance_from_branch_meters:null}],...change};
  const calls=[],mutations=[],notices=[];let dialog,refreshed=0,closed=0;
  const window={};vm.runInNewContext(source,{window,console});
  const helpers={state:{lang:'en',orders:[{id:'order-1',brand_id:'enbishan'}]},can:()=>true,
    api:async(...args)=>{calls.push(args);return data;},mutation:async(...args)=>{mutations.push(args);return {};},
    modal:(...args)=>{dialog=args;},notify:(...args)=>notices.push(args),refresh:async()=>refreshed++,closeModal:()=>closed++};
  window.KPSPreparationRouting.init(helpers);
  return {ui:window.KPSPreparationRouting,helpers,calls,mutations,notices,get dialog(){return dialog;},get refreshed(){return refreshed;},get closed(){return closed;},data};
}
test('reads canonical brand and shows both distances with escaped labels',async()=>{
  const h=harness();await h.ui.open('order-1');
  assert.equal(h.calls[0][0],'/api/preparation-routing/orders/order-1');
  assert.equal(h.calls[0][1].brand,'enbishan');
  assert.match(h.dialog[1],/1\.5 km/);assert.match(h.dialog[1],/400 m/);
  assert.match(h.dialog[1],/Target &lt;img src=x&gt;/);assert.doesNotMatch(h.dialog[1],/<script>|<img/);
  assert.match(h.dialog[1],/not road distances or arrival estimates/);
});
test('write sends expected branch and version with no price or seller input',async()=>{
  const h=harness();await h.ui.open('order-1');
  await h.dialog[2](new Map([['target_branch_id','target'],['reason',' Reduce distance ']]),null,{});
  const [url,body,,options]=h.mutations[0];
  assert.equal(url,'/api/preparation-routing/orders/order-1');
  assert.deepEqual(JSON.parse(JSON.stringify(body)),{target_branch_id:'target',expected_preparation_branch_id:'source',version:7,reason:'Reduce distance'});
  assert.equal(options.brand,'enbishan');assert.equal(h.refreshed,1);assert.equal(h.closed,1);
});
test('printed or assigned orders show blocker without a submit handler',async()=>{
  const h=harness({can_choose:false,blocked_reason:'Already printed'});await h.ui.open('order-1');
  assert.equal(h.dialog[2],undefined);assert.match(h.dialog[1],/Already printed/);assert.doesNotMatch(h.dialog[1],/type="submit"/);
});
test('revoked local permission never offers a write',async()=>{
  const h=harness();h.helpers.can=permission=>permission!=='orders_route_preparation';await h.ui.open('order-1');
  assert.equal(h.dialog[2],undefined);
});
test('unavailable or current branch cannot be submitted by altered form',async()=>{
  const h=harness();await h.ui.open('order-1');
  for(const target of ['source','denied','missing'])await assert.rejects(()=>h.dialog[2](new Map([['target_branch_id',target],['reason','Test reason']])),/eligible branch/);
  assert.equal(h.mutations.length,0);
});
test('target preparation queue submits a limited transition in its own brand',async()=>{
  const h=harness();const task={id:'routed',brand_id:'enbishan',version:3,order_no:22,preparation_status:'accepted',workflow:'branch_fulfillment',
    preparation_branch:{name:'Target'},items:[{name:'Coffee <svg>',quantity:2,note:'No sugar',options:[]}]};
  h.helpers.api=async()=>({preparation_tasks:[task]});let handler;
  const container={innerHTML:'',addEventListener:(event,fn)=>handler=fn,removeEventListener:()=>{},contains:()=>true};
  await h.ui.renderQueue(container);
  assert.match(container.innerHTML,/Start preparing/);assert.match(container.innerHTML,/Coffee &lt;svg&gt;/);
  const button={disabled:false,dataset:{prepTask:'routed',prepStatus:'preparing'}};
  await handler({target:{closest:()=>button}});
  assert.equal(h.mutations[0][0],'/api/preparation-routing/tasks/routed/prepare');
  assert.deepEqual(JSON.parse(JSON.stringify(h.mutations[0][1])),{version:3,status:'preparing'});
  assert.equal(h.mutations[0][3].brand,'enbishan');
});

test('postpickup approved revision is acknowledged without replaying physical handover',async()=>{
 const h=harness();const task={id:'routed-picked-up',brand_id:'enbishan',version:8,preparation_status:'handed_over',workflow:'manager_dispatch',next_status:'reviewed',items:[],operational_instructions:'Approved <script> extra item'};
 h.helpers.api=async()=>({preparation_tasks:[task]});let handler;
 const container={innerHTML:'',addEventListener:(_event,fn)=>handler=fn,removeEventListener:()=>{},contains:()=>true};
 await h.ui.renderQueue(container);
 assert.match(container.innerHTML,/Acknowledge approved revision/);
 assert.match(container.innerHTML,/Approved &lt;script&gt; extra item/);assert.doesNotMatch(container.innerHTML,/<script>|data-prep-status="handed_over"/);
 await handler({target:{closest:()=>({disabled:false,dataset:{prepTask:task.id,prepStatus:'reviewed'}})}});
 assert.equal(h.mutations.length,1);assert.equal(h.mutations[0][0],'/api/preparation-routing/tasks/routed-picked-up/prepare');
 assert.deepEqual(JSON.parse(JSON.stringify(h.mutations[0][1])),{version:8,status:'reviewed'});
});
