const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const root=path.join(__dirname,'..','public');
const ops=fs.readFileSync(path.join(root,'ops.js'),'utf8');
const confirmation=ops.slice(ops.indexOf('  async function ownerOperationalConfirm('),ops.indexOf('  async function ownerStepUp('));
const advertising=fs.readFileSync(path.join(root,'assets','advertising-policy.js'),'utf8');

for(const brand of ['kps','enbishan'])for(const lang of ['ar','en']){
  test(`${brand}/${lang}: cancel retains draft; confirmation saves once without OTP`,async()=>{
    const writes=[],prompts=[];let accept=false,submit,markup,closed=false;
    const tr=(ar,en)=>lang==='ar'?ar:en;
    const context=vm.createContext({state:{user:{role:'owner',brand_id:brand}},tr,
      window:{confirm:message=>{prompts.push(message);return accept;}}});
    vm.runInContext(confirmation+'\n'+advertising,context);
    const draft=new Map([['advertising_enabled','on'],['reason','Owner approved change']]);
    await context.window.KPSAdvertisingPolicy.open({user:context.state.user,tr,esc:String,
      api:async(url,options)=>{if(options){writes.push({url,...options});return {};}
        return {policy:{version:7,advertising_enabled:false}};},
      modal:(title,html,save)=>{markup=html;submit=save;},
      authorize:()=>context.ownerOperationalConfirm(tr('ظهور الإعلانات للعملاء','Customer advertising visibility')),
      closeModal:()=>{closed=true;},onSaved:async()=>{}});
    assert.equal(markup.includes('type="password"'),false);
    await assert.rejects(submit(draft));
    assert.equal(writes.length,0);assert.equal(closed,false);
    assert.equal(draft.get('reason'),'Owner approved change');
    accept=true;await submit(draft);
    assert.equal(prompts.length,2);assert.equal(writes.length,1);assert.equal(closed,true);
    assert.equal(writes[0].url,'/api/advertising/policy');
    assert.equal(writes[0].body.expected_version,7);
    assert.equal(writes[0].body.reason,draft.get('reason'));
    assert.equal(writes[0].body.advertising_enabled,true);
  });
}

test('nonowner cannot approve an owner operation',async()=>{
  const context=vm.createContext({state:{user:{role:'admin'}},tr:(ar,en)=>en,
    window:{confirm:()=>{throw Error('Must not prompt');}}});
  vm.runInContext(confirmation,context);
  await assert.rejects(context.ownerOperationalConfirm('Save'),/Owner only/);
});
