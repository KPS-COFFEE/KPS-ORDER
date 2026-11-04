'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const source = fs.readFileSync(require('node:path').join(__dirname,'../public/ops.js'),'utf8');
const start = source.lastIndexOf("  document.addEventListener('submit',async e=>{",source.indexOf("const form=e.target;if(!form.matches('.staff-branch-form'))"));
const end = source.indexOf('\n  function checkedBranches',start);
const handler = source.slice(start,end);
function setup({role='owner',sms=true,authError,patchError}={}) {
  let submit, release;
  const calls=[];
  const button={disabled:false,isConnected:true};
  const error={textContent:''};
  const form={dataset:{staffId:'staff/test'},matches:s=>s==='.staff-branch-form',querySelector:s=>s==='button[type=submit]'?button:error};
  const body={branch_ids:['kps-branch-2','enbishan-branch-1'],reason:'Owner approved addition'};
  const gate=new Promise(resolve=>{release=resolve});
  const state={user:{role,employee_sms_email_retired:sms},staff:['unchanged']};
  vm.runInNewContext(handler,{
    document:{addEventListener:(event,cb)=>{submit=cb}},
    FormData:class{get(){return body.reason}},
    checkedBranches:()=>body.branch_ids,
    state,encodeURIComponent,
    ownerStepUp:async()=>{calls.push('verify');await gate;if(authError)throw Error(authError)},
    api:async(path,options)=>{calls.push({path,body:options.body,method:options.method});if(patchError)throw Error(patchError)},
    loadPage:async()=>calls.push('reload'),
    setStatus:msg=>calls.push(msg),tr:(_ar,en)=>en
  });
  return {run:()=>submit({target:form,preventDefault(){}}),release,calls,state,button,error,body};
}
test('owner branch PATCH waits for successful SMS and preserves additive scope',async()=>{
 const t=setup();const pending=t.run();
 assert.deepEqual(t.calls,['verify']);assert.equal(t.button.disabled,true);
 t.release();await pending;
 const patch=t.calls[1];assert.equal(patch.path,'/api/identity/staff/staff%2Ftest');
 assert.equal(patch.method,'PATCH');assert.equal(JSON.stringify(patch.body),JSON.stringify(t.body));
 assert.equal(t.state.staff,null);assert.equal(t.button.disabled,false);
 assert.ok(t.calls.includes('reload'));
});
for (const authError of ['Verification cancelled','SMS unavailable','Invalid code']) {
 test('no branch write when '+authError,async()=>{
  const t=setup({authError});const pending=t.run();t.release();await pending;
  assert.deepEqual(t.calls,['verify']);assert.deepEqual(t.state.staff,['unchanged']);
  assert.equal(t.error.textContent,authError);assert.equal(t.button.disabled,false);
 });
}
test('authorized non-owner keeps existing server authorization path',async()=>{
 const t=setup({role:'delivery_manager'});await t.run();
 assert.equal(t.calls[0].method,'PATCH');assert.equal(t.calls.includes('verify'),false);
});
test('failed PATCH keeps entered scope and exposes error for retry',async()=>{
 const t=setup({patchError:'Scope denied'});const pending=t.run();t.release();await pending;
 assert.equal(t.error.textContent,'Scope denied');assert.deepEqual(t.state.staff,['unchanged']);
 assert.equal(t.calls.includes('reload'),false);assert.equal(t.button.disabled,false);
});
