'use strict';
// Isolated UI behavior: no real accounts, messages, grants or navigation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const ops = fs.readFileSync(path.join(__dirname, '../public/ops.js'), 'utf8');
const entry = fs.readFileSync(path.join(__dirname, '../public/work-access.js'), 'utf8');
const token = 'T'.repeat(43);
const origin = 'https://order.kps.sa';
const tr = (ar, en) => en;
const esc = value => String(value ?? '').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
const block = (start, end) => ops.slice(ops.indexOf(start), ops.indexOf(end, ops.indexOf(start)));

function ownerUI({role='owner', authError=null}={}) {
  const calls=[];
  const user={id:'staff/test',display_name:'Test staff',role:'admin',active:true};
  const state={user:{id:'owner',role},staff:[user]};
  let currentModal, release;
  const gate=new Promise(resolve=>{release=resolve;});
  const context={state,tr,esc,encodeURIComponent,URL,URLSearchParams,location:{origin},
    roleName:String,datetime:String,notice:String,empty:String,
    table:(_headers,rows)=>JSON.stringify(rows),formEnd:String,
    api:async (url,options={})=>{
      calls.push({url,...options});
      if(options.method==='POST')return {grant:{expires_at:'2030-01-01T00:00:00Z'},entry_url:origin+'/work-access#token='+token};
      return {enabled:true,grants:[]};
    },
    ownerStepUp:async (...args)=>{calls.push({verify:args});await gate;if(authError)throw Error(authError);},
    modal:(title,html,submit)=>{currentModal={title,html,submit};},
    $:()=>({querySelectorAll:()=>[]}),setStatus:()=>{},
  };
  vm.createContext(context);
  vm.runInContext(block('  async function staffGeoAccess(', "  document.addEventListener('submit',e=>{\n    if(e.target.id!=='staff-filters')"),context);
  context.showStaffGeoLink=(_user,result)=>calls.push({created:result});
  return {context,user,calls,release,modal:()=>currentModal};
}

test('outside-Saudi action appears only for owner with active nonarchived staff',()=>{
  const context={state:{user:{role:'owner',id:'owner'}},tr,esc};
  vm.createContext(context);
  vm.runInContext(block('  function staffActions(', '  async function staffGeoAccess('),context);
  const staff={id:'staff',role:'admin',active:true,invitation_status:'active'};
  assert.match(context.staffActions(staff), /data-action="staff-geo-access"/);
  assert.doesNotMatch(context.staffActions({...staff,active:false}), /staff-geo-access/);
  assert.doesNotMatch(context.staffActions({...staff,invitation_status:'archived'}), /staff-geo-access/);
  context.state.user.role='delivery_manager';
  assert.doesNotMatch(context.staffActions(staff), /staff-geo-access/);
});

test('grant creation waits for forced owner SMS before posting exact account, duration and reason',async()=>{
  const t=ownerUI();
  await t.context.staffGeoAccess(t.user.id);
  assert.match(t.modal().html, /value="24" selected/);
  const pending=t.modal().submit(new Map([['hours','24'],['reason','  Travel approval  ']]));
  assert.equal(t.calls.length,2);
  assert.equal(t.calls[1].verify[0],undefined);
  assert.equal(t.calls[1].verify[1],true);
  assert.equal(t.calls.filter(c=>c.method==='POST').length,0);
  t.release();await pending;
  const post=t.calls.find(c=>c.method==='POST');
  assert.equal(post.url,'/api/admin/staff-geo/grants');
  assert.equal(JSON.stringify(post.body),JSON.stringify({user_id:t.user.id,hours:24,reason:'Travel approval'}));
  assert.ok(t.calls.at(-1).created);
});

for (const authError of ['SMS unavailable','Verification cancelled','Invalid owner code']) {
  test('no grant on '+authError,async()=>{
    const t=ownerUI({authError});
    await t.context.staffGeoAccess(t.user.id);
    const pending=t.modal().submit(new Map([['hours','24'],['reason','Travel approval']]));
    t.release();await assert.rejects(pending,new RegExp(authError));
    assert.equal(t.calls.filter(c=>c.method==='POST').length,0);
  });
}

test('nonowner cannot open the permission modal or read grants',async()=>{
  const t=ownerUI({role:'admin'});
  await assert.rejects(t.context.staffGeoAccess(t.user.id),/Only the owner/);
  assert.equal(t.calls.length,0);
});

test('invalid duration and blank reasons do not request owner SMS',async()=>{
  const t=ownerUI();await t.context.staffGeoAccess(t.user.id);
  for(const data of [new Map([['hours','169'],['reason','Test']]),new Map([['hours','24'],['reason','  ']])]) {
    await assert.rejects(t.modal().submit(data),/Select a duration/);
  }
  assert.equal(t.calls.length,1);
});

test('revocation waits for owner SMS and preserves entered reason',async()=>{
  const t=ownerUI();
  t.context.staffGeoRevoke(t.user,'grant/test');
  const pending=t.modal().submit(new Map([['reason','Travel finished']]));
  assert.equal(t.calls.length,1);assert.equal(t.calls[0].verify[1],true);
  t.release();await pending;
  const post=t.calls.find(c=>c.method==='POST');
  assert.equal(post.url,'/api/admin/staff-geo/grants/grant%2Ftest/revoke');
  assert.equal(post.body.reason,'Travel finished');
});

function linkUI() {
  const nodes={};let html;
  const dialog={open:true,handlers:[],addEventListener:(_name,handler)=>dialog.handlers.push(handler),removeEventListener:(_name,handler)=>{dialog.handlers=dialog.handlers.filter(x=>x!==handler);}};
  const context={URL,URLSearchParams,location:{origin},tr,esc,datetime:String,notice:String,
    modal:(_title,markup)=>{html=markup;},
    navigator:{clipboard:{writeText:async()=>{}}},
    staffGeoAccess:async()=>{},setStatus:()=>{},
    $:selector=>selector==='#modal'?dialog:(nodes[selector]??={value:'',isConnected:true,addEventListener(){},focus(){},select(){}}),
  };
  vm.createContext(context);
  vm.runInContext(block('  function showStaffGeoLink(', '  function staffGeoRevoke('),context);
  return {context,nodes,dialog,html:()=>html};
}

test('one-time link remains visible after stale dialog close and is wiped on real close',()=>{
  const t=linkUI();
  t.context.showStaffGeoLink({id:'staff',display_name:'Test'}, {entry_url:origin+'/work-access#token='+token,grant:{expires_at:'2030-01-01'}});
  const input=t.nodes['#staff-geo-link'];
  assert.equal(input.value,origin+'/work-access#token='+token);
  assert.ok(!t.html().includes(token));
  t.dialog.handlers[0]();assert.ok(input.value.includes(token));
  t.dialog.open=false;t.dialog.handlers[0]();
  assert.equal(input.value,'');assert.equal(t.dialog.handlers.length,0);
});

for (const link of [
  'https://other.invalid/work-access#token='+token,
  origin+'/ops#token='+token,
  origin+'/work-access?token='+token,
  origin+'/work-access#token='+token+'&token='+token,
]) {
  test('grant link rejects an unsafe origin, path or token location: '+link.split('#')[0].split('?')[0],()=>{
    const t=linkUI();
    assert.throws(()=>t.context.showStaffGeoLink({id:'staff'},{entry_url:link}),/secure link could not be displayed/);
  });
}

async function entryUI({hash='#token='+token,response={ok:true,status:200,json:async()=>({accepted:true,redirect:'/ops'}),headers:{get:()=> 'application/json'}},fetchError=false}={}) {
  const calls=[];const messages={};
  const location={hash,pathname:'/work-access',replace:destination=>calls.push({navigate:destination})};
  const context={URLSearchParams,location,
    history:{replaceState:(_state,_unused,url)=>{calls.push({history:url});location.hash='';}},
    document:{getElementById:id=>(messages[id]??={textContent:''})},
    fetch:async(url,options)=>{calls.push({url,...options});if(fetchError)throw Error('Network');return response;},
  };
  vm.runInNewContext(entry,context);
  await new Promise(resolve=>setImmediate(resolve));
  return {calls,messages,location};
}

test('entry removes fragment before redemption, sends token only in POST body, then opens fixed ops path',async()=>{
  const t=await entryUI();
  assert.deepEqual(t.calls[0],{history:'/work-access'});
  const post=t.calls[1];
  assert.equal(post.url,'/api/staff-geo/redeem');assert.equal(post.method,'POST');
  assert.equal(post.credentials,'same-origin');assert.equal(post.cache,'no-store');
  assert.equal(post.body,JSON.stringify({token}));
  assert.equal(JSON.stringify(post.headers).includes(token),false);
  assert.deepEqual(t.calls[2],{navigate:'/ops'});
});

for(const hash of ['', '#token=short', '#token='+token+'&token='+token, '#token='+token+'&redirect=https://other.invalid']) {
  test('malformed entry fragments never call API: '+hash.slice(0,20),async()=>{
    const t=await entryUI({hash});assert.equal(t.calls.length,1);
    assert.match(t.messages['access-status-en'].textContent,/invalid/);
  });
}

test('a revoked or consumed link never opens administration',async()=>{
  const t=await entryUI({response:{ok:false,status:403}});
  assert.equal(t.calls.length,2);
  assert.match(t.messages['access-status-en'].textContent,/revoked/);
});

test('a false successful response cannot redirect or expose arbitrary server text',async()=>{
  const t=await entryUI({response:{ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>({accepted:true,redirect:'https://other.invalid'})}});
  assert.equal(t.calls.length,2);
  assert.match(t.messages['access-status-en'].textContent,/could not be confirmed/);
});

test('network failure leaves a clear neutral error without opening administration',async()=>{
  const t=await entryUI({fetchError:true});
  assert.equal(t.calls.length,2);
  assert.match(t.messages['access-status-en'].textContent,/could not be confirmed/);
});
