'use strict';
// Exercise the storefront's actual handlers without provider or order traffic.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/customer.js','utf8');
const slice=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
const policy=slice('  function requiresPhoneVerification()', '  // Presentation only:');
const strings=vm.runInNewContext('('+source.slice(source.indexOf('  const uiStrings=')+'  const uiStrings='.length,source.indexOf('  const localizedBindings')).replace(/;\s*$/,'')+')');
class Element{
  constructor(){this.value='';this.hidden=false;this.dataset={};this.children=[];this.classList={toggle(){}};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  querySelector(){return this.paragraph||(this.paragraph=new Element());}
  focus(){}
}
function harness({brand='enbishan',language='ar',session={},phoneRequired=false,smsAvailable=false}={}){
  const fields=new Map(),calls=[],dialogs=[],errors=[];
  const $=id=>{if(!fields.has(id))fields.set(id,new Element());return fields.get(id);};
  $('customer-name').value='Synthetic Customer';$('customer-phone').value='0500000001';
  $('neighborhood').value='Synthetic neighborhood';
  const state={phoneRequired,session:{user:null,csrf_token:null,...session},channels:{guest:{available:true},sms:{available:smsAvailable}},
    cart:[],branches:[],brand:{id:brand},branchId:'branch',service:'delivery',location:{lat:28,lng:45},identityReady:true};
  const context=vm.createContext({state,requestedBrandId:brand,language,$,window:{},
    t:key=>strings[key]?.[language]||key,entryText:(ar,en)=>language==='ar'?ar:en,
    text:(id,value)=>{$(id).textContent=String(value);},showError:(id,value)=>errors.push([id,value]),
    openDialog:id=>dialogs.push(id),closeDialog(){},renderCustomerEntry(){},updatePolicyLinks(){},
    branch:()=>({name:'Synthetic Branch'}),sourceText:record=>record.name,serviceName:()=>'',
    syncMobileBasket(){},catalogReady:()=>true,document:{querySelector:id=>$(id)},
    renderAccount(){calls.push('account');},refreshQuote:async()=>{calls.push('quote');state.quote={total_halalas:100};},
    orderBody:()=>({customer:{address:'Synthetic address'},payment_method:'cash'}),node:()=>new Element(),
    renderFirstGift(){},money:String,confirmAction:()=>calls.push('confirm'),requireCustomer:()=>calls.push('wrong-brand'),
    showEnbishanLocation:()=>calls.push('location'),api:async path=>{calls.push(path);throw Error('Unexpected HTTP');},
    loadCustomerProfile:()=>calls.push('profile'),maskedPhone:String,
  });
  vm.runInContext(policy,context);
  return {context,state,$,calls,dialogs,errors,load:(start,end)=>vm.runInContext(slice(start,end),context)};
}
const guest=brand=>({user:{id:'synthetic-guest',role:'customer',brand_id:brand,identity_kind:'guest'},csrf_token:'synthetic-csrf',customer_verified:false});
const verified=brand=>({...guest(brand),customer_verified:true,user:{...guest(brand).user,identity_kind:'phone_sms_verified',phone:'0500000001',display_name:'Synthetic Customer'}});

for(const language of ['ar','en']){
  test(`${language}: stale guest policy cannot enable Enbishan order labels`,()=>{
    const h=harness({language});h.state.cart=[{id:'synthetic'}];
    h.load('  function renderEnbishanJourney(){','  async function reviewEnbishanCheckout(){');
    h.context.renderEnbishanJourney();
    assert.equal(h.context.requiresPhoneVerification(),true);
    assert.equal(h.$('enbishan-continue').textContent,strings.review_and_confirm_order[language]);
    assert.equal(h.$('.basket-footnote').textContent,strings.verified_checkout_notice[language]);
  });
  test(`${language}: reviewing a new Enbishan basket does not create an automatic guest`,async()=>{
    const h=harness({language});h.context.beginCheckout=async()=>h.calls.push('checkout');
    h.load('  async function reviewEnbishanCheckout(){','  function renderCustomerEntry(){');
    await h.context.reviewEnbishanCheckout();
    assert.deepEqual(h.calls,['checkout']);
  });
  for(const [kind,session] of [['anonymous',{}],['existing guest',guest('enbishan')],['verified',verified('enbishan')]]){
    test(`${language}: ${kind} reaches ${kind==='verified'?'review':'OTP'} at checkout`,async()=>{
      const h=harness({language,session});h.context.recoverCheckout=async()=>false;
      h.load('  async function beginCheckout()', '  async function requestOTP(');
      await h.context.beginCheckout();
      assert.equal(h.calls.includes('confirm'),kind==='verified');
      assert.equal(h.calls.includes('quote'),kind==='verified');
      assert.deepEqual(h.dialogs,kind==='verified'?[]:['account-dialog']);
      if(kind!=='verified')assert.equal(h.state.afterLogin,h.context.beginCheckout);
    });
  }
  for(const smsAvailable of [false,true]){
    test(`${language}: OTP form remains required when SMS available=${smsAvailable}`,()=>{
      const h=harness({language,smsAvailable,session:guest('enbishan')});
      h.load('  function renderAccount() {','  async function establishIdentity(');
      h.context.renderAccount();
      assert.equal(h.$('guest-form').hidden,true);
      assert.equal(h.$('otp-request-form').hidden,false);
    });
  }
}
test('unverified Enbishan pending attempts are neither replayed nor posted directly',async()=>{
  const h=harness({session:guest('enbishan')});
  h.context.checkoutAttempt=async()=>{throw Error('Must not write/replay pending checkout');};
  h.load('  async function submitCheckout(payload)', '  const node = (tag, cls, content) =>');
  assert.equal(await h.context.recoverCheckout(),false);
  await assert.rejects(h.context.submitCheckout({brand_id:'enbishan'}),{code:'CUSTOMER_PHONE_VERIFICATION_REQUIRED'});
  assert.deepEqual(h.calls,[]);
});
test('verified Enbishan can resume its existing pending attempt',async()=>{
  const h=harness({session:verified('enbishan')});
  h.load('  async function recoverCheckout()', '  const node = (tag, cls, content) =>');
  h.context.submitCheckout=async()=>{h.calls.push('resume');return true;};
  assert.equal(await h.context.recoverCheckout(),true);assert.deepEqual(h.calls,['resume']);
});
test('KPS and other brand policy behavior remains unchanged',async()=>{
  for(const brand of ['kps','synthetic'])for(const phoneRequired of [undefined,false,true]){
    const h=harness({brand,phoneRequired,session:guest(brand)});
    // The default parameter above is false; set undefined explicitly too.
    h.state.phoneRequired=phoneRequired;
    assert.equal(h.context.requiresPhoneVerification(),phoneRequired!==false);
    h.load('  async function recoverCheckout()', '  const node = (tag, cls, content) =>');
    h.context.submitCheckout=async()=>{h.calls.push('resume');return true;};
    assert.equal(await h.context.recoverCheckout(),true);assert.deepEqual(h.calls,['resume']);
  }
});
