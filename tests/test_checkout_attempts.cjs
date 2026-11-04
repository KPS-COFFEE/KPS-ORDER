'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../public/customer.js'),'utf8');
const helpers=source.slice(source.indexOf('  async function checkoutAttempt('),source.indexOf('  const node = (tag, cls, content) =>'));
let sequence=0;
function harness({saved=null,generation='basket-1',api=async()=>({order:{id:'order-1'}})}={}){
  let record=saved,tail=Promise.resolve();const writes=[],opened=[];
  const context=vm.createContext({state:{session:{user:{id:'customer-1',brand_id:'kps',role:'customer'}},cart:[{id:'coffee'}],quote:{}},requestedBrandId:'kps',basketGeneration:generation,
    checkoutStore:update=>{const next=tail.then(()=>{const result=update(record);record=result;return structuredClone(result);});tail=next.catch(()=>{});return next;},
    key:()=>`attempt-${++sequence}`,api:async(...args)=>{writes.push(args);return api(...args);},
    localStorage:{getItem:()=>JSON.stringify({generation})},basketStorageKey:'basket',persistDraft:()=>{},renderCart:()=>{},queueNearestBranches:()=>{},queueQuote:()=>{},
    window:{},closeDialog:()=>{},toast:()=>{},t:x=>x,openOrders:async id=>opened.push(id)});
  vm.runInContext(helpers,context);
  return {context,writes,opened,get record(){return record;}};
}
const body={brand_id:'kps',items:[{product_id:'coffee',quantity:2}],expected_gift:true,expected_gift_version:1};
test('concurrent confirmation acquires one frozen attempt even when gift differs',async()=>{
  const h=harness();const [a,b]=await Promise.all([h.context.checkoutAttempt(body),h.context.checkoutAttempt({...body,expected_gift:false,expected_gift_version:2})]);
  assert.equal(a.key,b.key);assert.deepEqual(a.payload,b.payload);assert.equal(a.payload.expected_gift,true);
});
test('lost response and refreshed gift replay identical request and key',async()=>{
  let calls=0;const h=harness({api:async()=>{if(++calls===1)throw Object.assign(new Error('lost'),{code:'NETWORK'});return {order:{id:'saved'}};}});
  await assert.rejects(h.context.submitCheckout(body));const pending=h.record;
  await h.context.submitCheckout({...body,expected_gift:false,expected_gift_version:2});
  assert.equal(h.writes[0][1].idempotency,h.writes[1][1].idempotency);
  assert.deepEqual(h.writes[0][1].data,h.writes[1][1].data);assert.equal(pending.key,h.record.key);assert.deepEqual(h.opened,['saved']);
});
test('page reload recovers pending attempt without cart, quote or new purchase body',async()=>{
  const first=harness({api:async()=>{throw Object.assign(new Error('timeout'),{status:503});}});
  await assert.rejects(first.context.submitCheckout(body));
  const reloaded=harness({saved:first.record});await reloaded.context.recoverCheckout();
  assert.equal(reloaded.writes[0][1].idempotency,first.record.key);assert.equal(reloaded.record.status,'saved');
});
test('completed tombstone recovers stale tab without another POST',async()=>{
  const first=harness();await first.context.submitCheckout(body);
  const stale=harness({saved:first.record});await stale.context.submitCheckout({...body,expected_gift:false});
  assert.equal(stale.writes.length,0);assert.deepEqual(stale.opened,['order-1']);
});
test('editing basket cannot abandon unresolved attempt; deliberate new basket follows completion',async()=>{
  const first=harness();await first.context.checkoutAttempt(body);
  const changed=harness({saved:first.record,generation:'basket-2'});await changed.context.submitCheckout({...body,items:[]});
  assert.equal(changed.writes[0][1].data.items.length,1);assert.equal(changed.context.state.cart.length,1);
  const next=await changed.context.checkoutAttempt({...body,expected_gift:false});assert.equal(next.generation,'basket-2');assert.notEqual(next.key,first.record.key);
});
test('definite gift rejection permits reviewed retry; ambiguous conflict does not',async()=>{
  for(const code of ['GIFT_CHANGED','IDEMPOTENCY_CONFLICT']){
    const h=harness({api:async()=>{throw Object.assign(new Error(code),{code,status:409});}});
    await assert.rejects(h.context.submitCheckout(body));const old=h.record.key;
    const next=await h.context.checkoutAttempt({...body,expected_gift:false});
    assert.equal(next.key===old,code==='IDEMPOTENCY_CONFLICT');
  }
});
test('storage failure and replacement identity prevent any order POST',async()=>{
  const h=harness();h.context.checkoutStore=async()=>{throw new Error('storage failed');};await assert.rejects(h.context.submitCheckout(body));assert.equal(h.writes.length,0);
  const changed=harness();changed.context.checkoutStore=async update=>{changed.context.state.session.user={id:'replacement'};return update(null);};
  await assert.rejects(changed.context.submitCheckout(body),{code:'SESSION_CHANGED'});assert.equal(changed.writes.length,0);
});
test('malformed success keeps purchase pending for exact recovery',async()=>{
  const h=harness({api:async()=>({})});await assert.rejects(h.context.submitCheckout(body));assert.equal(h.record.status,'pending');
  assert.equal((await h.context.checkoutAttempt({...body,expected_gift:false})).key,h.record.key);
});
