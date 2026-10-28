const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../public/assets/central-operations.js');
const user={id:'employee-1',role:'delivery_manager',brand_id:'kps',central_operations:{brands:[
  {id:'kps',name:'KPS',branch_ids:['kps-1']},
  {id:'enbishan',name:'Enbishan',branch_ids:['enb-1']},
]}};

test('one inbox includes both brands, with per-brand canonical requests',async()=>{
  const calls=[];const api=create();
  const result=await api.request('/api/orders?sort=newest',{},async(path,opts)=>{
    calls.push(opts.brand);
    return {orders:[{id:opts.brand+'-order',created_at:opts.brand==='kps'?'2026-10-23T10:00:00Z':'2026-10-23T11:00:00Z'}],workspace_counts:{active:1}};
  },user);
  assert.deepEqual(calls,['kps','enbishan']);
  assert.deepEqual(result.orders.map(r=>r.brand_id),['enbishan','kps']);
  assert.equal(result.workspace_counts.active,2);
});

test('a failure in either brand rejects the central inbox',async()=>{
  await assert.rejects(create().request('/api/conversations',{},async(path,opts)=>{
    if(opts.brand==='enbishan')throw new Error('revoked');
    return {conversations:[]};
  },user),/revoked/);
});

test('a clicked order uses its own brand without changing the employee',async()=>{
  const api=create(),calls=[];
  await api.request('/api/orders',{},async(path,opts)=>({orders:[{id:opts.brand+'-order'}]}),user);
  await api.request('/api/orders/enbishan-order/manager-accept',{method:'POST',body:{version:3}},async(path,opts)=>{
    calls.push({path,opts});return {order:{id:'enbishan-order'}};
  },user);
  assert.equal(calls[0].opts.brand,'enbishan');
  assert.equal(calls[0].opts.body.version,3);
  assert.equal(user.brand_id,'kps');
});

test('a deep link resolves its context on the server first',async()=>{
  const calls=[];
  await create().request('/api/conversations/enb-chat/messages',{},async(path,opts)=>{
    calls.push([path,opts]);return path.includes('resource-context')?{brand_id:'enbishan'}:{messages:[]};
  },user);
  assert.match(calls[0][0],/kind=conversation&resource_id=enb-chat/);
  assert.equal(calls[1][1].brand,'enbishan');
});

test('failed context resolution never falls back to the home brand',async()=>{
  let calls=0;
  await assert.rejects(create().request('/api/tasks/forbidden/transition',{method:'POST'},async()=>{
    ++calls;throw new Error('not found');
  },user),/not found/);
  assert.equal(calls,1);
});

test('order-bound assignment reads only the order brand',async()=>{
  const api=create(),calls=[];
  await api.request('/api/orders',{},async(path,opts)=>({orders:opts.brand==='enbishan'?[{id:'enb-order'}]:[]}),user);
  await api.request('/api/shifts?order_id=enb-order',{},async(path,opts)=>{calls.push(opts.brand);return {shifts:[]};},user);
  assert.deepEqual(calls,['enbishan']);
});

test('staff are deduplicated with their combined granted branch scopes',async()=>{
  const result=await create().request('/api/operations/people',{},async(path,opts)=>({users:[
    {id:'shared-driver',branch_ids:opts.brand==='kps'?['kps-1']:['enb-1']},
  ]}),user);
  assert.equal(result.users.length,1);
  assert.deepEqual(result.users[0].branch_ids,['kps-1','enb-1']);
  assert.deepEqual(result.users[0].operational_brands,['kps','enbishan']);
});

test('pagination retains every fetched order and advances both streams',async()=>{
  const result=await create().request('/api/orders?offset=30',{},async(path,opts)=>({
    orders:Array.from({length:30},(_,i)=>({id:opts.brand+i})),next_offset:opts.brand==='kps'?60:null,
  }),user);
  assert.equal(result.orders.length,60);
  assert.equal(result.next_offset,60);
});

test('an explicit order-menu brand is used only for that call',async()=>{
  const calls=[];const api=create();
  const raw=async(path,opts)=>{calls.push(opts.brand);return {products:[]};};
  await api.request('/api/catalog?brand_id=enbishan',{brand:'enbishan'},raw,user);
  await api.request('/api/session',{},raw,user);
  assert.deepEqual(calls,['enbishan',undefined]);
});

test('disabled rollout and restricted morning context retain canonical routing',async()=>{
  const calls=[],raw=async(path,opts)=>{calls.push(opts);return {};};
  await create().request('/api/orders',{},raw,{...user,central_operations:null});
  await create().request('/api/orders',{},raw,{...user,delivery_context:{shift_id:'morning'}});
  assert.deepEqual(calls,[{},{}]);
});

test('a changed grant cannot reuse another employee context cache',async()=>{
  const api=create();api.remember({order:{id:'old-order'}},'enbishan');
  const calls=[];
  await api.request('/api/orders/old-order',{},async(path,opts)=>{
    calls.push(path);return path.includes('resource-context')?{brand_id:'kps'}:{};
  },{...user,id:'employee-2'});
  assert.match(calls[0],/resource-context/);
});

test('one shared workday is displayed once with both branch sets and counts',async()=>{
  const adapter=create();const raw=async(path,options)=>({shifts:[{id:options.brand==='kps'?'root':'enb-leg',brand_id:options.brand,central_workday_id:'day',central_anchor_id:'root',branch_ids:[options.brand+'-branch'],order_counts:{total:options.brand==='kps'?2:3},opening_halalas:options.brand==='kps'?100:0}]});
  const result=await adapter.request('/api/shifts/current',{},raw,user,'shifts');
  assert.equal(result.shifts.length,1);assert.equal(result.shifts[0].id,'root');
  assert.deepEqual(new Set(result.shifts[0].branch_ids),new Set(['kps-branch','enbishan-branch']));
  assert.equal(result.shifts[0].order_counts.total,5);assert.equal(result.shifts[0].opening_halalas,100);
  const calls=[];await adapter.request('/api/shifts/root/closing',{method:'POST',body:{}},async(p,o)=>{calls.push(p);return {};},user,'shifts');
  assert.deepEqual(calls,['/api/central-operations/workdays/root/closing']);
});


test('blind grouped workdays do not acquire withheld monetary fields',async()=>{
  const result=await create().request('/api/shifts/current',{},async(path,options)=>({shifts:[{id:options.brand==='kps'?'root':'enb-leg',central_workday_id:'day',central_anchor_id:'root',submission:{status:'submitted'}}]}),{...user,role:'driver'});
  assert.equal(result.shifts.length,1);
  assert.equal('opening_halalas' in result.shifts[0],false);
  assert.equal('expected_halalas' in result.shifts[0].submission,false);
  assert.equal('permanent_float' in result.shifts[0],false);
});

test('grouped financial cards include disclosed amounts from both ledgers',async()=>{
  const result=await create().request('/api/shifts/current',{},async(path,options)=>({shifts:[{id:options.brand==='kps'?'root':'enb-leg',central_workday_id:'day',central_anchor_id:'root',submission:{counted_halalas:options.brand==='kps'?100:300,difference_halalas:options.brand==='kps'?-50:25}}]}),user);
  assert.equal(result.shifts[0].submission.counted_halalas,400);
  assert.equal(result.shifts[0].submission.difference_halalas,-25);
});

test('departure from a side-brand branch uses its own workday ledger',async()=>{
  const adapter=create();
  await adapter.request('/api/shifts/current',{},async(path,options)=>({shifts:[{id:options.brand==='kps'?'root':'enb-leg',brand_id:options.brand,central_workday_id:'day',central_anchor_id:'root',branch_ids:[options.brand==='kps'?'kps-1':'enb-1']}]}),user);
  const calls=[];
  await adapter.request('/api/driver-work-hours/depart',{method:'POST',body:{shift_id:'root',branch_id:'enb-1',order_ids:['enb-order']}},async(path,options)=>{
    calls.push({path,options});return path.includes('resource-context')?{brand_id:'enbishan'}:{};
  },user);
  assert.equal(calls.at(-1).options.brand,'enbishan');
  assert.equal(calls.at(-1).options.body.shift_id,'enb-leg');
  assert.deepEqual(calls.at(-1).options.body.order_ids,['enb-order']);
});

test('one open legacy evening shift is adopted before showing the workspace',async()=>{
  const adapter=create(),calls=[];let adopted=false;
  const result=await adapter.request('/api/shifts/current',{},async(path,options)=>{
    calls.push(path);
    if(path.endsWith('/adopt')){adopted=true;return {};}
    return {shifts:options.brand==='kps'?[{id:'legacy',user_id:user.id,status:'open',...(adopted?{central_workday_id:'day',central_anchor_id:'legacy'}:{})}]:[]};
  },user);
  assert.equal(calls.filter(path=>path.endsWith('/adopt')).length,1);
  assert.equal(result.shifts[0].central_workday_id,'day');
});


test('a cold side-ledger closure link routes to its authorized shared anchor',async()=>{
  const calls=[];
  await create().request('/api/shifts/enb-leg/return',{method:'POST',body:{reason:'Correct count'}},async(path,options)=>{
    calls.push({path,options});return path.includes('resource-context')?{brand_id:'enbishan',central_anchor_id:'root',central_workday_id:'day'}:{};
  },user);
  assert.equal(calls.at(-1).path,'/api/central-operations/workdays/root/return');
  assert.equal(calls.at(-1).options.body.reason,'Correct count');
});

test('a side-ledger physical break is submitted under its shared anchor',async()=>{
  const calls=[];
  await create().request('/api/driver-breaks',{method:'POST',body:{shift_id:'enb-leg',reason:'rest',minutes:5}},async(path,options)=>{
    calls.push({path,options});return path.includes('resource-context')?{brand_id:path.includes('enb-leg')?'enbishan':'kps',central_anchor_id:'root'}:{};
  },user);
  assert.equal(calls.at(-1).path,'/api/driver-breaks');
  assert.equal(calls.at(-1).options.brand,'kps');
  assert.equal(calls.at(-1).options.body.shift_id,'root');
});


test('manager handover list, proposal and acceptance use atomic central endpoints',async()=>{
  const adapter=create(),calls=[],raw=async(path,options)=>{calls.push(path);return path.includes('resource-context')?{brand_id:'enbishan',central_anchor_id:'root'}:{handovers:[]};};
  await adapter.request('/api/manager-handovers',{},raw,user);
  await adapter.request('/api/shifts/enb-leg/handover-manager',{method:'POST',body:{target_manager_shift_id:'other-root',reason:'Shift change'}},raw,user);
  await adapter.request('/api/manager-handovers/shared-handover/accept',{method:'POST',body:{}},raw,user);
  assert.equal(calls[0],'/api/central-operations/manager-handovers');
  assert.equal(calls.at(-2),'/api/central-operations/workdays/root/handover-manager');
  assert.equal(calls.at(-1),'/api/central-operations/manager-handovers/shared-handover/accept');
});


test('overview uses the server central dashboard with its filters intact',async()=>{
  const calls=[];
  await create().request('/api/dashboard?period=custom&start=2026-10-01&end=2026-10-23&service=delivery',{},async(path,options)=>{calls.push({path,options});return {};},user);
  assert.equal(calls.length,1);
  assert.equal(calls[0].path,'/api/central-operations/dashboard?period=custom&start=2026-10-01&end=2026-10-23&service=delivery');
  assert.equal(calls[0].options.brand,undefined);
});


test('fleet listing and vehicle management preserve the physical vehicle brand',async()=>{
  const adapter=create(),calls=[];
  await adapter.request('/api/fleet/vehicles',{},async(path,options)=>{calls.push({path,options});return {vehicles:[{id:'enb-vehicle',brand_id:'enbishan',plate:'123'}]};},user);
  await adapter.request('/api/fleet/vehicles/enb-vehicle/management',{method:'POST',body:{action:'transfer',driver_id:'shared-driver'}},async(path,options)=>{calls.push({path,options});return {};},user);
  assert.equal(calls[0].path,'/api/central-operations/fleet/vehicles');
  assert.equal(calls[1].options.brand,'enbishan');
  assert.equal(calls[1].options.body.driver_id,'shared-driver');
});


test('driver destination and achievement use shared server contracts',async()=>{
  const calls=[],raw=async(path,options)=>{calls.push({path,options});return {};},adapter=create();
  await adapter.request('/api/operations/driver-destination',{method:'PUT',body:{kind:'branch',branch_id:'enb-1'}},raw,user);
  await adapter.request('/api/driver-incentives?month=2026-10',{},raw,user);
  assert.equal(calls[0].path,'/api/central-operations/driver-destination');
  assert.equal(calls[0].options.body.branch_id,'enb-1');
  assert.equal(calls[1].path,'/api/central-operations/driver-incentives?month=2026-10');
});


test('flag-off canonical discovery drains existing shared workdays without adopting new ones',async()=>{
  const adapter=create(),calls=[],disabled={...user,central_operations:null};
  const raw=async(path,options)=>{calls.push({path,options});return path==='/api/shifts/current'?{shifts:[{id:'root',central_workday_id:'day',central_anchor_id:'root'}]}:{};};
  await adapter.request('/api/shifts/current',{},raw,disabled);
  await adapter.request('/api/shifts/root/closing',{method:'POST',body:{}},raw,disabled);
  await adapter.request('/api/shifts',{method:'POST',body:{kind:'manager'}},raw,disabled);
  assert.equal(calls[1].path,'/api/central-operations/workdays/root/closing');
  assert.equal(calls[2].path,'/api/shifts');
  assert.equal(calls.some(c=>c.path.endsWith('/adopt')),false);
});

test('flag-off cold detail drains only after canonical authorized metadata',async()=>{
  const calls=[];
  await create().request('/api/shifts/enb-leg',{},async(path,options)=>{
    calls.push(path);return path==='/api/shifts/enb-leg'?{shift:{id:'enb-leg',central_workday_id:'day',central_anchor_id:'root'}}:{};
  },{...user,central_operations:null});
  assert.deepEqual(calls,['/api/shifts/enb-leg','/api/central-operations/workdays/root']);
});


test('session drain metadata routes cold group actions while rollout is off',async()=>{
  const calls=[],disabled={...user,central_operations:null,central_workday_drain:[{id:'day',anchor_shift_id:'root',kind:'driver',legs:[{brand_id:'kps',shift_id:'root',branch_ids:['kps-1']},{brand_id:'enbishan',shift_id:'enb-leg',branch_ids:['enb-1']}]}]};
  await create().request('/api/shifts/enb-leg/submit',{method:'POST',body:{legs:[]}},async(path,options)=>{calls.push({path,options});return {};},disabled);
  assert.equal(calls.length,1);
  assert.equal(calls[0].path,'/api/central-operations/workdays/root/submit');
});

test('owner history uses physical workday server pagination and total',async()=>{
  const calls=[];
  const result=await create().request('/api/shifts?owner_view=history&offset=50&search=driver',{},async(path,options)=>{calls.push(path);return {shifts:[{id:'root',central_workday_id:'day',central_anchor_id:'root'}],total:51,offset:50,next_offset:null};},{...user,role:'owner'});
  assert.deepEqual(calls,['/api/central-operations/shifts?owner_view=history&offset=50&search=driver']);
  assert.equal(result.total,51);assert.equal(result.offset,50);assert.equal(result.shifts.length,1);
});

test('amendment inbox retains both order and request arrays across brands',async()=>{
  const result=await create().request('/api/order-amendments/inbox',{},async(path,options)=>({orders:[{id:options.brand+'-order'}],requests:[{id:options.brand+'-request'}]}),user);
  assert.equal(result.orders.length,2);assert.equal(result.requests.length,2);
  assert.deepEqual(result.requests.map(r=>r.brand_id),['kps','enbishan']);
});

test('fleet options and reports use central physical readings contracts',async()=>{
  const calls=[],adapter=create(),raw=async(path,options)=>{calls.push(path);return {};};
  await adapter.request('/api/fleet/options',{},raw,user);
  await adapter.request('/api/fleet/report?start=2026-10-01&offset=50',{},raw,user);
  assert.deepEqual(calls,['/api/central-operations/fleet/options','/api/central-operations/fleet/report?start=2026-10-01&offset=50']);
});


test('dispatch preserves full nested cohort and source counters across both brands',async()=>{
  const snapshots={
    kps:{all:{total:536,waiting:12,completed:524,sources:{manager:300,website:236}},current:{total:126,waiting:12,completed:114,sources:{manager:80,website:46}}},
    enbishan:{all:{total:7,waiting:2,completed:5,sources:{manager:3,website:4}},current:{total:3,waiting:1,completed:2,sources:{manager:1,website:2}}},
  };
  const original=JSON.stringify(snapshots),calls=[];
  const result=await create().request('/api/orders?limit=100&dispatch_workspace=true&dispatch_scope=current&dispatch_city=Hafar',{},async(path,opts)=>{
    calls.push([path,opts.brand]);
    return {orders:[{id:opts.brand+'-visible'}],workspace_counts:snapshots[opts.brand]};
  },user);
  assert.equal(result.orders.length,2);
  assert.deepEqual(result.workspace_counts,{
    all:{total:543,waiting:14,completed:529,sources:{manager:303,website:240}},
    current:{total:129,waiting:13,completed:116,sources:{manager:81,website:48}},
  });
  assert.ok(calls.every(([path])=>path.includes('dispatch_scope=current')&&path.includes('dispatch_city=Hafar')));
  assert.equal(JSON.stringify(snapshots),original,'canonical response counters stay untouched');
});

test('an empty second brand retains populated dispatch and explicit zero source counts',async()=>{
  const result=await create().request('/api/orders?dispatch_workspace=true&dispatch_scope=all',{},async(path,opts)=>({
    orders:[],workspace_counts:opts.brand==='kps'?
      {all:{total:536,completed:536,sources:{manager:300,website:236}},current:{total:126,completed:126,sources:{manager:80,website:46}}}:
      {all:{total:0,sources:{manager:0,website:0}},current:{total:0,sources:{manager:0,website:0}}},
  }),user);
  assert.equal(result.workspace_counts.all.total,536);
  assert.equal(result.workspace_counts.current.total,126);
  assert.deepEqual(result.workspace_counts.current.sources,{manager:80,website:46});
  const empty=await create().request('/api/orders?dispatch_workspace=true',{},async()=>({orders:[],workspace_counts:{all:{total:0,sources:{manager:0,website:0}},current:{total:0,sources:{manager:0,website:0}}}}),user);
  assert.deepEqual(empty.workspace_counts.current,{total:0,sources:{manager:0,website:0}});
});

test('receipted history remains readable while only the fresh physical shift is adopted',async()=>{
  const adapter=create(),calls=[];let adopted=false;
  const old={id:'receipted',user_id:user.id,kind:'manager',status:'submitted',staffing_ended_at:'2026-10-25T08:00:00Z'};
  const result=await adapter.request('/api/shifts/current',{},async(path,options)=>{
    calls.push(path);
    if(path.endsWith('/adopt')){assert.equal(path,'/api/central-operations/workdays/fresh/adopt');adopted=true;return {};}
    return {shifts:options.brand==='kps'?[old,{id:'fresh',user_id:user.id,kind:'manager',status:'open',staffing_ended_at:null,...(adopted?{central_workday_id:'new-day',central_anchor_id:'fresh'}:{})}]:[]};
  },user);
  assert.equal(calls.filter(path=>path.endsWith('/adopt')).length,1);
  assert.equal(result.shifts.find(s=>s.id==='receipted').staffing_ended_at,old.staffing_ended_at);
  assert.equal(result.shifts.find(s=>s.id==='fresh').central_workday_id,'new-day');
});

test('mixed route permits keep central destination validation on POST',async()=>{
 const calls=[];await create().request('/api/operations/driver-route-context',{method:'POST',body:{driver_id:'driver',task_id:'enb-task'}},async(path,opts)=>{calls.push({path,opts});return {};},user);
 assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/central-operations/driver-route-context');assert.equal(calls[0].opts.brand,undefined);assert.equal(calls[0].opts.body.task_id,'enb-task');
});
test('mixed itinerary estimate with return branch retains full physical route scope',async()=>{
 const calls=[];await create().request('/api/operations/route-eta',{method:'POST',body:{driver_id:'driver',return_branch_id:'enb-1',publish:true}},async(path,opts)=>{calls.push({path,opts});return {};},user);
 assert.equal(calls.length,1);assert.equal(calls[0].opts.brand,undefined);assert.equal(calls[0].opts.body.publish,true);
});
test('history and alert decisions resolve canonical shift grants',async()=>{
 const api=create(),calls=[];const raw=async(path,opts)=>{calls.push({path,opts});return path.includes('resource-context')?{brand_id:'enbishan'}:{};};
 await api.request('/api/operations/map-services/history?shift_id=enb-shift',{},raw,user);
 assert.match(calls[0].path,/kind=shift/);assert.equal(calls[1].opts.brand,'enbishan');
 await api.request('/api/operations/map-services/alerts/resolve',{method:'POST',body:{shift_id:'enb-shift',kind:'location_stale',reason:'network unavailable',action:'contacted driver'}},raw,user);
 assert.equal(calls.at(-1).opts.brand,'enbishan');assert.equal(calls.at(-1).opts.body.reason,'network unavailable');
});

test('driver live counts deduplicate shared duty and include the busy brand leg',async()=>{
  const raw=async(path,opts)=>({drivers:[
    {id:'shared',branch_ids:[opts.brand+'-1'],live_status:opts.brand==='kps'?'available':'busy'},
    {id:'one-duty',branch_ids:[opts.brand+'-1'],live_status:opts.brand==='kps'?'no_shift':'available'},
    {id:'off-duty',branch_ids:[opts.brand+'-1'],live_status:'no_shift'},
  ],driver_counts:{total:3,available:1,busy:1,no_shift:1}});
  for(const brands of [user.central_operations.brands,[...user.central_operations.brands].reverse()]){
    const result=await create().request('/api/operations/drivers',{},raw,{...user,central_operations:{brands}});
    assert.equal(result.drivers.length,3);
    assert.equal(result.drivers.find(d=>d.id==='shared').live_status,'busy');
    assert.equal(result.drivers.find(d=>d.id==='one-duty').live_status,'available');
    assert.deepEqual(result.driver_counts,{total:3,available:1,busy:1,no_shift:1});
  }
});
