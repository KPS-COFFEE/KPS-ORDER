const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const helpers=source.slice(source.indexOf('  function ownUnclosedDriverShift('),source.indexOf('  async function tasks()'));
const open=source.split('\n').find(x=>x.startsWith('  async function openShift('));
const manager=source.split('\n').find(x=>x.startsWith('  function ownManagerShift('));
(async()=>{
 for(const lang of ['ar','en']){
  const old={id:'receipted',kind:'driver',user_id:'me',status:'submitted',staffing_ended_at:'2026-10-25T08:00:00Z'};
  const live={id:'next',kind:'driver',user_id:'me',status:'open',staffing_ended_at:null};
  let rows=[old],modalCalls=0,peopleCalls=0,loaded=0,requests=[];
  const kind={value:'driver',addEventListener(){}},inspection={hidden:false,querySelectorAll:()=>[]};
  const context={document:{addEventListener(){}},state:{user:{id:'me',role:'driver',branch_ids:['b']},branches:[]},
   tr:(ar,en)=>lang==='ar'?ar:en,esc:String,isAdmin:()=>false,
   $:selector=>selector.includes('[name=kind]')?kind:selector==='#open-vehicle-inspection'?inspection:selector.includes('[name=receipt_trial]')?null:{open:false},
   loadBranches:async()=>{},api:async path=>{requests.push(path);return path==='/api/shifts/current'?{shifts:rows}:{shift:live};},
   people:async()=>{peopleCalls++;return {users:[],available_manager_shifts:[]};},
   modal:()=>{modalCalls++;},notice:()=>'',selectOptions:()=>'',inspectionFields:()=>'',formEnd:()=>'',
   loadPage:async()=>{loaded++;},setStatus:()=>{},vehicleProfile:()=>'<profile>',vehicleShiftSummary:()=>'<live-summary>'};
  vm.createContext(context);vm.runInContext(helpers+'\n'+open+'\n'+manager+'\nthis.open=openShift;this.pick=ownUnclosedDriverShift;this.manager=ownManagerShift;this.vehicle=driverVehicle;',context);
  await context.open();
  assert.equal(modalCalls,1,'receipted submitted shift must allow the new shift form');
  assert.equal(peopleCalls,1);assert.equal(loaded,0,'must not resume previous financial closure');
  assert.equal(context.pick([old]),undefined);
  assert.equal(context.pick([old,live]).id,'next');
  assert.equal(context.manager([{...old,kind:'manager'}]),undefined);
  assert.equal(context.manager([{...old,kind:'manager'},{...live,kind:'manager'}]).id,'next');
  context.state.driverCurrentShift=old;rows=[old,live];requests=[];
  await context.vehicle();
  assert.deepEqual(requests,['/api/shifts/current','/api/shifts/next']);
  assert.equal(context.state.driverCurrentShift.id,'next');
  rows=[old];requests=[];await context.vehicle();
  assert.deepEqual(requests,['/api/shifts/current']);
  assert.equal(context.state.driverCurrentShift,undefined);
 }
 console.log('PASS: AR/EN receipted closures allow fresh opening; driver/manager current selectors and fresh vehicle context exclude historical staffing.');
})().catch(e=>{console.error(e);process.exit(1);});
