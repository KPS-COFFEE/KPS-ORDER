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
