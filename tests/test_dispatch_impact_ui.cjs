const assert=require('node:assert/strict');const {test}=require('node:test');const vm=require('node:vm');const fs=require('node:fs');
function setup(api){
  class Element{constructor(tag){this.tag=tag;this.children=[];this.events={};this.hidden=false;this.isConnected=true;this.value='';}append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}setAttribute(){}addEventListener(name,fn){this.events[name]=fn;}removeEventListener(name){delete this.events[name];}}
  const root=new Element('section'),select=new Element('select');let now=100000;
  const context={window:{},document:{createElement:tag=>new Element(tag)},Date:class extends Date{static now(){return now;}},Math,Number};
  vm.createContext(context);vm.runInContext(fs.readFileSync('public/assets/dispatch-impact.js','utf8'),context);
  const controller=context.window.KPSDispatchImpact.create({root,select,api,orderId:'order',taskId:null,policy:{},tr:(ar,en)=>en});
  const choices=root.children[3],reason=choices.children[0].children[0],contact=choices.children[1].children[0];
  return {root,select,reason,contact,controller,advance(ms){now+=ms;}};
}
test('failed provider preview allows only explicit enumerated operational override',async()=>{
  const t=setup(async()=>{throw Error('offline');});t.select.value='driver';await t.select.events.change();
  assert.throws(()=>t.controller.payload(),/Select assignment reason/);
  t.reason.value='estimate_unavailable';t.contact.value='not_contacted';
  assert.equal(t.controller.payload().impact_quote_id,null);assert.equal(t.controller.payload().impact_reason,'estimate_unavailable');
});
test('only selected-driver fresh quote is sent; stale quote cannot silently pass',async()=>{
  const t=setup(async()=>({enabled:true,status:'within_target',quote_id:'q',expires_in_seconds:45,orders:[]}));
  t.select.value='d1';await t.select.events.change();assert.equal(t.controller.payload().impact_quote_id,'q');
  t.advance(41000);assert.throws(()=>t.controller.payload(),/Select assignment reason/);
  t.reason.value='estimate_unavailable';t.contact.value='not_contacted';assert.equal(t.controller.payload().impact_quote_id,null);
});
test('a late response from old selection cannot replace current quote',async()=>{
  let a,b;const t=setup((_p,request)=>new Promise(resolve=>{if(request.body.driver_shift_id==='a')a=resolve;else b=resolve;}));
  t.select.value='a';const first=t.select.events.change();t.select.value='b';const second=t.select.events.change();
  b({enabled:true,status:'within_target',quote_id:'b',expires_in_seconds:45});await second;
  a({enabled:true,status:'within_target',quote_id:'a',expires_in_seconds:45});await first;
  assert.equal(t.controller.payload().impact_quote_id,'b');
});
test('closing modal discards future response and detaches selection event',async()=>{
  let done;const t=setup(()=>new Promise(r=>done=r));t.select.value='a';const pending=t.select.events.change();t.controller.close();done({enabled:true,status:'within_target',quote_id:'a'});await pending;
  assert.equal(t.select.events.change,undefined);assert.equal(Object.keys(t.controller.payload()).length,0);
});
