const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/ops.js','utf8');
function extract(start,end){const a=source.indexOf(start),b=source.indexOf(end,a);assert.ok(a>=0&&b>a);return source.slice(a,b);}
test('owner dashboard forwards the selected invoice brand to detail opening',async()=>{
  const opened=[];let configuration;
  const root={};const heading={dataset:{},innerHTML:''};
  const ctx={ownerDashboardController:null,state:{user:{role:'owner',brand_id:'kps'},page:'ownerDashboard'},
    canPage:()=>true,tr:(_a,en)=>en,esc:String,pageLoadScope:()=> 'scope',api(){},
    $:selector=>selector==='#page-heading'?heading:root,
    window:{KPSOwnerDashboard:{create(options){configuration=options;return {mount:async()=>{}};}}},
    orderDetail:async(id,brand)=>opened.push({id,brand}),revokeEmployeeView(){}};
  vm.createContext(ctx);vm.runInContext(extract('  async function ownerDashboard(){','  async function loadMorningTrial'),ctx);
  await ctx.ownerDashboard();await configuration.onOrder('enbishan-invoice','enbishan');
  assert.deepEqual(opened,[{id:'enbishan-invoice',brand:'enbishan'}]);
});
test('order and driver-task detail reads use the same explicit brand',async()=>{
  const requests=[];let title;
  const order={id:'invoice',brand_id:'enbishan',service:'delivery',items:[],customer:{},timings:{}};
  const ctx={state:{user:{role:'owner'},orders:[]},
    api:async(path,options)=>{requests.push({path,brand:options?.brand});return path.startsWith('/api/orders/')?{order,events:[]}:{tasks:[]};},
    normalizeOrder:value=>value,modal:(value)=>{title=value;},tr:(_a,en)=>en,esc:String,
    orderCard:()=>'',morningDispatchDetails:()=>'',dataItem:()=>'',duration:()=>'',
    isManager:()=>false,can:()=>false,orderHistoryTimeline:()=>''};
  vm.createContext(ctx);vm.runInContext(extract('  async function orderDetail(','  async function nearestBranches'),ctx);
  await ctx.orderDetail('invoice','enbishan');
  assert.deepEqual(requests,[{path:'/api/orders/invoice',brand:'enbishan'},{path:'/api/tasks?order_id=invoice',brand:'enbishan'}]);
  assert.equal(title,'Order details');assert.equal(ctx.state.orders[0].brand_id,'enbishan');
});
