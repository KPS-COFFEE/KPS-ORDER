'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/ops.js','utf8');
function section(a,b){const start=source.indexOf(a),end=source.indexOf(b,start);assert(start>=0&&end>start);return source.slice(start,end);}
function harness(role,permissions=[],delivery_context){
 const nav={innerHTML:'',insertAdjacentHTML(_where,html){this.innerHTML+=html;}};
 const user={id:'actor',role,brand_id:'kps',branch_ids:['b1'],effective_permissions:permissions,delivery_context};
 const ctx={state:{user,page:role==='delivery_manager'?'orders':'accounting'},document:{activeElement:null},$:()=>nav,tr:(_ar,en)=>en,esc:String,isAdmin:()=>['owner','admin'].includes(role),isManager:()=>['owner','delivery_manager'].includes(role),canConfigure:()=>false};
 ctx.can=p=>role==='owner'||permissions.includes(p);ctx.driverSupportButton=()=>'';
 vm.createContext(ctx);vm.runInContext(section('  const pagePermissions=','  const actionPermissions=')+'\nthis.allowed=canPage;\n'+source.split('\n').find(l=>l.startsWith('  function navigation()')),ctx);
 return {ctx,nav};
}
test('accounting sees product sales and accounting without owner dashboard or driver fuel entry',()=>{
 const {ctx,nav}=harness('admin',['accounting_reports','accounting_review']);ctx.navigation();
 assert(ctx.allowed('productSales'));assert(ctx.allowed('accounting'));assert(!ctx.allowed('ownerDashboard'));assert(!ctx.allowed('driverFuel'));
 assert.match(nav.innerHTML,/data-page="productSales"/);assert.match(nav.innerHTML,/data-page="accounting"/);assert.doesNotMatch(nav.innerHTML,/data-page="dashboard"|data-page="ownerDashboard"/);
});
test('morning manager retains orders landing and only scoped report grant',()=>{
 const {ctx,nav}=harness('delivery_manager',['orders_view','reports_view'],{kind:'morning'});ctx.navigation();
 assert.equal(ctx.state.page,'orders');assert(ctx.allowed('productSales'));assert(!ctx.allowed('accounting'));assert(!ctx.allowed('ownerDashboard'));assert.match(nav.innerHTML,/data-page="orders"/);
 const boot=harness('delivery_manager',['orders_view','reports_view'],{bootstrap:true});assert(!boot.ctx.allowed('productSales'));
});
test('owner and explicitly authorized GM can read fuel report, accounting alone cannot',()=>{
 for(const [role,permissions,expected] of [['owner',[],true],['admin',['vehicle_maintenance_manage'],true],['admin',['accounting_reports'],false],['delivery_manager',['reports_view','reports_supervise'],true],['delivery_manager',['reports_view'],false],['driver',['vehicle_maintenance_manage'],false]])assert.equal(harness(role,permissions).ctx.allowed('driverFuel'),expected);
});
test('vehicle shift summary exposes refuel only on the driver own open shift',()=>{
 for(const [role,uid,expected] of [['owner','someone',false],['delivery_manager','someone',false],['admin','someone',false],['driver','someone',false],['driver','actor',true]]){
  const ctx={state:{user:{id:'actor',role},lang:'en'},tr:(_ar,en)=>en,esc:String,money:String,inspectionItems:{},table:()=>'',dataItem:()=>'',plateText:()=>'',datetime:String};
  vm.createContext(ctx);vm.runInContext(source.split('\n').find(l=>l.startsWith('  function vehicleShiftSummary(')),ctx);
  const html=ctx.vehicleShiftSummary({shift:{id:'s',kind:'driver',user_id:uid,status:'open',vehicle_log:{}},tasks:[]});assert.equal(html.includes('data-action="shift-refuel"'),expected);
 }
});
