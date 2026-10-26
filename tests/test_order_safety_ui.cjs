'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../public/ops.js'),'utf8');
const helpers=source.slice(source.indexOf('  async function duplicateReview('),source.indexOf('  async function orderDetail('));
const plain=x=>JSON.parse(JSON.stringify(x));
const fd=data=>({get:name=>data[name]??null});
function harness(brand,role='driver',pending=false){
  const reads=[],writes=[],modals=[],opened=[];
  const result={brand_id:brand,version:7,items:[{name:'Coffee',quantity:3},{name:'Cookie',quantity:2}],returned_quantities:{0:1},returns:pending?[{id:'partial-1',status:'pending',details:{reason:'Customer reduction',credit_halalas:500,lines:[{line_index:0,quantity:1}]}}]:[],related_orders:[{reference:'OLD',preparation_status:'ready',payment_status:'partially_paid'}]};
  const context=vm.createContext({state:{user:{role}},tr:(ar,en)=>en,esc:String,num:String,money:String,statusName:String,
    notice:x=>`<p>${x}</p>`,textarea:(label,name)=>`<textarea name="${name}">${label}</textarea>`,check:(label,name)=>`<input type="checkbox" name="${name}">${label}`,formEnd:label=>`<button type="submit">${label}</button>`,
    api:async(path,options)=>{reads.push([path,plain(options??{})]);return structuredClone(result);},
    mutation:async(path,body,button,options)=>{writes.push([path,plain(body),button,plain(options??{})]);},
    modal:(title,html,submit)=>modals.push({title,html,submit}),orderDetail:async id=>opened.push(id),closeModal:()=>{},loadPage:async()=>{}});
  vm.runInContext(helpers,context);
  return {context,reads,writes,modals,opened,result};
}
for(const brand of ['kps','enbishan']){
  test(`${brand}: existing driver Problem action offers branded partial return and preserves ordinary reporting`,async()=>{
    const h=harness(brand);h.context.driverProblem('task /1',brand);
    const modal=h.modals.at(-1);assert.match(modal.html,/data-action="partial-return"/);assert.match(modal.html,new RegExp(`data-brand="${brand}"`));
    await modal.submit(fd({reason:'Vehicle problem'}));
    assert.deepEqual(h.writes,[['/api/tasks/task%20%2F1/transition',{status:'problem',reason:'Vehicle problem'},null,{brand}]]);
  });
  for(const action of ['confirm_additional','cancel_duplicate'])test(`${brand}: duplicate ${action} sends version, reason, actual disposition and brand`,async()=>{
    const h=harness(brand,'delivery_manager');await h.context.duplicateReview('order /1',brand);
    assert.deepEqual(h.reads,[['/api/orders/order%20%2F1/duplicate-review',{brand}]]);
    const physical=action==='cancel_duplicate'?'returned_to_branch':'';
    await h.modals.at(-1).submit(fd({action,reason:'Confirmed directly with customer',physical_resolution:physical}));
    assert.deepEqual(h.writes,[['/api/orders/order%20%2F1/duplicate-review',{action,version:7,reason:'Confirmed directly with customer',physical_resolution:physical||null},null,{brand}]]);
    assert.deepEqual(h.opened,['order /1']);
  });
  for(const cause of ['customer_reduction','branch_error'])test(`${brand}: driver submits ${cause} with exact original line indices and response brand`,async()=>{
    const h=harness(brand);await h.context.partialReturn('task /1',brand);
    assert.deepEqual(h.reads[0],['/api/delivery-partial-returns/tasks/task%20%2F1',{brand}]);
    assert.match(h.modals.at(-1).html,/name="line_0"[^>]*max="2"/);
    await h.modals.at(-1).submit(fd({line_0:'0',line_1:'1',cause,reason:'Customer refused one cookie'}));
    assert.deepEqual(h.writes,[['/api/delivery-partial-returns/tasks/task%20%2F1',{version:7,lines:[{line_index:1,quantity:1}],cause,reason:'Customer refused one cookie'},null,{brand}]]);
    assert.deepEqual(h.reads.at(-1),['/api/delivery-partial-returns/tasks/task%20%2F1',{brand}]);
  });
  test(`${brand}: zero returned quantity cannot submit`,async()=>{
    const h=harness(brand);await h.context.partialReturn('task',brand);
    await assert.rejects(h.modals.at(-1).submit(fd({line_0:'0',line_1:'0'})),/Select the returned quantity/);assert.equal(h.writes.length,0);
  });
  test(`${brand}: driver sees pending request without manager controls or submit callback`,async()=>{
    const h=harness(brand,'driver',true);await h.context.partialReturn('task',brand);
    const modal=h.modals.at(-1);assert.equal(modal.submit,undefined);assert.doesNotMatch(modal.html,/name="action"|name="approve_below_minimum"|type="submit"/);assert.equal(h.writes.length,0);
  });
  for(const action of ['approve','reject'])test(`${brand}: manager ${action} carries explicit minimum approval and actual custody`,async()=>{
    const h=harness(brand,'delivery_manager',true);await h.context.partialReturn('task',brand);
    const modal=h.modals.at(-1);assert.match(modal.html,/name="approve_below_minimum"/);
    await modal.submit(fd({action,reason:'Reviewed amount and customer request',approve_below_minimum:action==='approve'?'on':null,physical_resolution:'with_driver_return_to_branch'}));
    assert.deepEqual(h.writes,[['/api/delivery-partial-returns/partial-1/decision',{action,reason:'Reviewed amount and customer request',approve_below_minimum:action==='approve',physical_resolution:action==='approve'?'with_driver_return_to_branch':null},null,{brand}]]);
  });
}
test('actual click dispatcher threads task/order brand using the defined button variable',async()=>{
  const start=source.indexOf("      else if(a==='problem')");
  const end=source.indexOf('\n',source.indexOf("      else if(a==='duplicate-review')",start));
  assert.ok(start>0&&end>start);
  const calls=[];const c=vm.createContext({driverProblem:(...args)=>calls.push(['problem',...args]),partialReturn:async(...args)=>calls.push(['partial-return',...args]),duplicateReview:async(...args)=>calls.push(['duplicate-review',...args])});
  vm.runInContext(`async function dispatch(button){const a=button.dataset.action,id=button.dataset.id;if(false){} ${source.slice(start,end)} }`,c);
  for(const action of ['problem','partial-return','duplicate-review'])await c.dispatch({dataset:{action,id:'synthetic-id',brand:'enbishan'}});
  assert.deepEqual(calls,[['problem','synthetic-id','enbishan'],['partial-return','synthetic-id','enbishan'],['duplicate-review','synthetic-id','enbishan']]);
});
