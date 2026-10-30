'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('public/ops.js','utf8');
const part=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b));
test('dashboard data repaint never mounts, updates or fetches a map',()=>{
 const nodes=new Map(),calls=[];
 const node=()=>({innerHTML:'',dataset:{},contains:()=>false});
 const ctx={document:{activeElement:null},state:{branches:[],orders:[],dispatchDrivers:[]},$:(s)=>{if(!nodes.has(s))nodes.set(s,node());return nodes.get(s);},tr:(_,en)=>en,esc:String,num:String,dispatchNames:{},sortDispatchOrders:xs=>xs,empty:x=>x,hideUnavailableActions(){},updateDispatchMap:()=>{calls.push('map');throw Error('dashboard map must be inactive');},api:()=>{calls.push('api');throw Error('dashboard repaint must not request map resources');}};
 vm.createContext(ctx);vm.runInContext(part('  function renderDispatchRows(){','  function orderItems(o)'),ctx);
 for(let i=0;i<5;i++)ctx.renderDispatchRows();
 assert.deepEqual(calls,[]);assert.match(nodes.get('#dispatch-orders').innerHTML,/No matching orders/);
});
test('explicit tracking page still mounts its map view and disposes the previous controller',async()=>{
 let mounts=0,destroys=0;const host={};
 const ctx={state:{page:'tracking',user:{id:'manager'},branches:[]},$:()=>host,api(){},loadBranches:async()=>{},heading(){},tr:(_,en)=>en,window:{KPSOrderTracking:{mount:async(h,options)=>{assert.equal(h,host);assert.equal(options.isActive(),true);mounts++;return {destroy:()=>destroys++};}}}};
 vm.createContext(ctx);vm.runInContext(part('  function pageReadApi(','  function assertPageLoad('),ctx);vm.runInContext(part('  let trackingController=null;','  async function dashboard()'),ctx);
 await ctx.tracking();await ctx.tracking();assert.equal(mounts,2);assert.equal(destroys,1);
});
test('dashboard map launch remains explicit and route/time calculations are not removed',()=>{
 const dashboard=part('  async function deliveryWorkspace(){','  function selectDispatchPanel(panel)');
 assert.match(dashboard,/class="dispatch-map-launch"/);assert.match(dashboard,/data-page="tracking"/);
 assert.doesNotMatch(dashboard,/KPSDispatchMap\.mount|\/api\/operations\/map-config/);
 assert.match(dashboard,/\/api\/operations\/driver-locations/);
 assert.match(source,/trackingDriverDetails\(d\)/);
 assert.match(source,/\/api\/operations\/route-eta/);
 assert.match(source,/\/api\/driver-work-hours\/depart/);
});
