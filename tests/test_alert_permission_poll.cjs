const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/ops.js','utf8');
const start=source.indexOf('  function currentAlertScope(');
const end=source.indexOf('  // A narrow order read is sufficient:',start);
assert.ok(start>=0&&end>start);

function harness(role,permissions,{failOperations=false}={}){
  const calls=[],channels=[],apiOptions=[];
  const host={dataset:{},innerHTML:'',textContent:'',hidden:false,contains:()=>false,
    querySelectorAll:()=>[],replaceChildren(fragment){this.innerHTML=fragment.html;}};
  const ctx={state:{user:{id:'delegate',role,effective_permissions:permissions}},trialMode:false,
    alertAudio:{state:'running'},operationalAlertScope:'',alertChannels:new Map(),alertBusy:false,
    stopAlertChannel(){},refreshAlertPolicy:async()=>{},ensureMandatoryAlertSound:async()=>{},
    tr:(_ar,en)=>en,esc:value=>String(value??''),num:String,orderLabel:row=>row.id,printAlertRow:()=>'',
    syncAlertChannel:(kind,ids)=>channels.push({kind,ids:[...ids]}),concealAlertControls(){},
    navigator:{onLine:true},window:{scrollY:0},document:{hidden:false,documentElement:{hasAttribute:()=>false},activeElement:null,querySelectorAll:()=>[],
      createElement(tag){assert.equal(tag,'template');return {innerHTML:'',get content(){return {html:this.innerHTML};}};}},
    $:selector=>selector==='#operational-alerts'?host:selector==='#content'?{getBoundingClientRect:()=>({top:0})}:null,
    can:permission=>role==='owner'||permissions.includes(permission),
    api:async (path,options)=>{
      calls.push(path);apiOptions.push(options);
      if(path==='/api/accounting/manager-cards?page_size=20&offset=0')return {cards:[{approved_day:null}]};
      if(path==='/api/order-financial-cases')return {cases:[]};
      if(path==='/api/driver-floats')return {requests:[]};
      if(path==='/api/driver-breaks'){
        if(role!=='owner'&&!permissions.includes('drivers_availability'))throw Error('403 FORBIDDEN');
        return {breaks:[{status:'requested'}]};
      }
      if(path==='/api/operations/alerts'){
        if(failOperations)throw Error('offline');
        return {unassigned:[{id:'pending-order'}]};
      }
      if(path==='/api/support-tickets')return {tickets:[]};
      if(path==='/api/conversations/alerts')return {awaiting_reply:[]};
      if(path==='/api/driver-closure')return {requests:[]};
      if(path==='/api/order-amendments/inbox')return {orders:[]};
      if(path==='/api/fuel-exceptions')return {requests:[]};
      throw Error('Unexpected request '+path);
    },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync('public/assets/accounting-alerts.js','utf8'),ctx);
  const helperStart=source.indexOf('  function accountingNotificationsOnly('),helperEnd=source.indexOf('  async function refreshAlertPolicy(',helperStart);
  vm.runInContext(source.slice(helperStart,helperEnd),ctx);
  vm.runInContext(source.slice(start,end),ctx);
  return {ctx,host,calls,channels,apiOptions};
}

test('a cancellation delegate without break permission keeps order and chat alerts working',async()=>{
  const {ctx,host,calls,channels}=harness('delivery_manager',['orders_cancel_after_print','operational_alerts','chat_view']);
  await ctx.refreshOperationalAlerts();
  assert.ok(calls.includes('/api/operations/alerts'));
  assert.ok(calls.includes('/api/conversations/alerts'));
  assert.ok(!calls.includes('/api/driver-breaks'));
  assert.match(host.innerHTML,/pending-order/);
  assert.doesNotMatch(host.innerHTML,/data-action="break-manage"/);
  assert.match(host.innerHTML,/data-action="driver-report-center"/);
  assert.equal(host.textContent,'');
  assert.deepEqual(channels.find(channel=>channel.kind==='orders').ids,['pending-order']);
});

for(const role of ['owner','delivery_manager'])test(`${role} with break authority still receives break requests`,async()=>{
  const {ctx,host,calls}=harness(role,['drivers_availability','operational_alerts','chat_view']);
  await ctx.refreshOperationalAlerts();
  assert.ok(calls.includes('/api/driver-breaks'));
  assert.match(host.innerHTML,/data-action="break-manage"/);
  assert.match(host.innerHTML,/Driver breaks.*\(1\)/);
  assert.equal(host.textContent,'');
});

test('an unrelated operational-alert failure remains visible and does not clear ringing work',async()=>{
  const {ctx,host,channels}=harness('delivery_manager',['orders_cancel_after_print','operational_alerts','chat_view'],{failOperations:true});
  await ctx.refreshOperationalAlerts();
  assert.match(host.textContent,/Alerts could not refresh/);
  assert.equal(channels.length,0);
  assert.equal(ctx.alertBusy,false);
});


test('accounting admin with chat access gets only actionable financial reads and clears order ringing',async()=>{
  const {ctx,host,calls,channels}=harness('admin',['accounting_reports','accounting_review','chat_view','orders_view']);
  await ctx.refreshOperationalAlerts();
  assert.deepEqual(calls,['/api/accounting/manager-cards?page_size=20&offset=0','/api/order-financial-cases','/api/driver-floats']);
  assert.match(host.innerHTML,/Closures needing completion or approval/);
  for(const channel of channels)assert.deepEqual(channel.ids,[]);
  assert.doesNotMatch(host.innerHTML,/order-detail|alert-sound|Messages awaiting reply/);
});


for(const central of [false,true])test(`accounting alert brand header follows actual multibrand session availability: ${central}`,async()=>{
 const {ctx,apiOptions}=harness('admin',['accounting_reports','accounting_review']);
 ctx.state.user.brand_id='kps';ctx.state.accountingBrand='enbishan';
 if(central)ctx.state.user.operations_brands=[{id:'kps'},{id:'enbishan'}];
 await ctx.refreshOperationalAlerts();
 assert.ok(apiOptions.length);
 for(const options of apiOptions)assert.equal(options?.brand,central?'enbishan':undefined);
});
