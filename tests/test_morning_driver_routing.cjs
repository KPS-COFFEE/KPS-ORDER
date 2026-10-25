const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../public/assets/central-operations.js');
const driver={id:'driver-1',role:'driver',brand_id:'kps',central_operations:{brands:[
  {id:'kps',name:'KPS',branch_ids:['branch-2']},
  {id:'enbishan',name:'Enbishan',branch_ids:['enb-1']},
]}};
const morning={id:'morning-manager',kind:'manager',status:'open',branch_ids:['branch-2'],delivery_window_snapshot:{period:'morning'}};
const body={kind:'driver',manager_shift_id:morning.id,branch_ids:[],vehicle_inspection:{odometer_km:12345,fuel_percent:100}};
async function discover(adapter,path,manager=morning,user=driver){
  await adapter.request(path,{},async(_path,opts)=>({available_manager_shifts:opts.brand==='kps'?[manager]:[],shifts:[]}),user);
}
for(const discovery of ['/api/operations/people','/api/shifts/current'])test('morning driver uses canonical shift from '+discovery,async()=>{
  const adapter=create();await discover(adapter,discovery);const calls=[];
  await adapter.request('/api/shifts',{method:'POST',body},async(path,opts)=>{calls.push({path,opts});return {};},driver);
  assert.equal(calls.length,1);
  assert.equal(calls[0].path,'/api/shifts');
  assert.equal(calls[0].opts.brand,'kps');
  assert.deepEqual(calls[0].opts.body,body);
});
test('shared evening manager keeps unified workday routing',async()=>{
  const adapter=create();await discover(adapter,'/api/operations/people',{...morning,central_workday_id:'day',delivery_window_snapshot:{period:'evening'}});
  const calls=[];await adapter.request('/api/shifts',{method:'POST',body},async(path)=>{calls.push(path);return {};},driver);
  assert.deepEqual(calls,['/api/central-operations/workdays']);
});
test('unknown manager never gets assumed to be morning',async()=>{
  const calls=[];await create().request('/api/shifts',{method:'POST',body},async(path)=>{calls.push(path);return {};},driver);
  assert.deepEqual(calls,['/api/central-operations/workdays']);
});
test('new principal cannot reuse a previous morning manager cache',async()=>{
  const adapter=create();await discover(adapter,'/api/operations/people');const calls=[];
  await adapter.request('/api/shifts',{method:'POST',body},async(path)=>{calls.push(path);return {};},{...driver,id:'other-driver'});
  assert.deepEqual(calls,['/api/central-operations/workdays']);
});
test('canonical rejection is preserved without shared fallback or retry',async()=>{
  const adapter=create();await discover(adapter,'/api/operations/people');const calls=[];
  await assert.rejects(adapter.request('/api/shifts',{method:'POST',body},async(path)=>{calls.push(path);throw new Error('inspection required');},driver),/inspection required/);
  assert.deepEqual(calls,['/api/shifts']);
});
test('owner morning driver opening preserves selected branches and mismatched explicit period for server validation',async()=>{
  const owner={...driver,id:'owner',role:'owner'};const adapter=create();await discover(adapter,'/api/operations/people',morning,owner);
  const requestBody={...body,user_id:'driver-1',branch_ids:['branch-2'],delivery_period:'evening'};const calls=[];
  await adapter.request('/api/shifts',{method:'POST',body:requestBody},async(path,opts)=>{calls.push({path,opts});return {};},owner);
  assert.equal(calls[0].path,'/api/shifts');assert.deepEqual(calls[0].opts.body,requestBody);
});
