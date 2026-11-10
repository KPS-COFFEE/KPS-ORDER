/* Exercise the shipped embedded review with distinct brand ledgers and closure drafts. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/assets/driver-order-review.js','utf8');
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
class Element {
  constructor(tag,attributes,generation){this.tag=tag;this.attributes=attributes;this.dataset={};this.generation=generation;this.value=attributes.value||'';this.hidden='hidden' in attributes;this.textContent='';for(const [k,v]of Object.entries(attributes))if(k.startsWith('data-'))this.dataset[k.slice(5).replace(/-([a-z])/g,(_,x)=>x.toUpperCase())]=v;}
  hasAttribute(key){return key in this.attributes;}
  closest(selector){return selector==='button'&&this.tag==='button'?this:null;}
  set innerHTML(value){this.html=value;}
  get innerHTML(){return this.html||'';}
}
class Host {
  constructor(){this.listeners={};this.attributes={};this.nodes=[];this.generation=0;this.html='';}
  set innerHTML(value){this.html=value;this.generation++;this.nodes=[];for(const match of value.matchAll(/<(button|input|div|p)\b([^>]*)>/g)){const attributes={};for(const a of match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g))attributes[a[1]]=a[2]??'';this.nodes.push(new Element(match[1],attributes,this.generation));}}
  get innerHTML(){return this.html;}
  addEventListener(name,fn){assert.equal(name,'click');assert(!this.listeners[name],'mount must register only one delegated click handler');this.listeners[name]=fn;}
  setAttribute(key,value){this.attributes[key]=value;}
  contains(node){return this.nodes.includes(node);}
  querySelectorAll(selector){const m=selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);assert(m,'unsupported test selector '+selector);return this.nodes.filter(n=>n.hasAttribute(m[1])&&(m[2]===undefined||n.attributes[m[1]]===m[2]));}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  async click(selector){const target=this.querySelector(selector);assert(target,'missing UI control '+selector);await this.listeners.click({target});}
}
const brands=[{id:'kps',name:'KPS',count:2,total_halalas:7000},{id:'enbishan',name:'Enbishan',count:1,total_halalas:1950}];
const kps={id:'kps-one',brand_id:'kps',order_no:101,total_halalas:2500,payment_method:'cash',payment_status:'paid',preparation_status:'ready',delivery_status:'delivered',created_at:'2026-10-26T03:00:00Z',snapshot:{order_reference:'KPS-101'}};
const next={...kps,id:'kps-two',order_no:102,total_halalas:4500,payment_method:'card',delivery_status:'picked_up',snapshot:{order_reference:'KPS-102'}};
const enbishan={...kps,id:'enbishan-one',brand_id:'enbishan',order_no:201,total_halalas:1950,payment_method:'card',delivery_status:'problem',snapshot:{order_reference:'ENB-201'}};
(async()=>{
  for(const lang of ['ar','en']){
    const tr=(ar,en)=>lang==='ar'?ar:en,host=new Host(),requests=[];
    const context={window:{},Intl,URLSearchParams,Date};vm.createContext(context);vm.runInContext(source,context);
    const api=async(path,options)=>{
      requests.push({path,options});
      if(path.startsWith('/api/orders/'))return {order:{customer:{name:'<img src=x>',phone:'+966500000001'},snapshot:{items:[{name:'<script>Item</script>',quantity:2}]},notes:'<b>source note</b>'}};
      const q=new URL(path,'https://example.invalid').searchParams,brand=q.get('brand_id');
      return {driver:{id:'amr',display_name:'عمرو <source>'},shift:{id:'driver-shift',opened_at:'2026-10-25T18:00:00Z',status:'open'},brands,orders:brand==='enbishan'?[enbishan]:q.get('offset')==='30'?[next]:[kps,enbishan],total_count:3,next_offset:brand!=='enbishan'&&q.get('offset')!=='30'?30:null,view:q.get('view')};
    };
    const options={host,api,tr,esc:escape,money:v=>(v/100).toFixed(2),datetime:String,statusName:v=>v,driverId:'amr'};
    await context.window.KPSDriverOrderReview.mount(options);
    assert.equal(requests.length,2);let query=new URL(requests[0].path,'https://example.invalid').searchParams;
    assert.equal(query.get('driver_id'),'amr');assert.equal(query.get('view'),'current');assert.equal(query.get('shift_id'),null);
    assert.match(host.innerHTML,/KPS-101/);assert.doesNotMatch(host.innerHTML,/ENB-201/);assert.match(host.innerHTML,/70\.00/);assert.match(host.innerHTML,/19\.50/);assert.match(host.innerHTML,/&lt;source&gt;/);assert.doesNotMatch(host.innerHTML,/<source>/);
    assert.match(host.innerHTML,lang==='ar'?/تم التوصيل/:/Delivered/);assert.match(host.innerHTML,lang==='ar'?/كاش/:/Cash/);
    for(const button of host.nodes.filter(n=>n.tag==='button'))assert.equal(button.attributes.type,'button','review must not submit containing closure forms');
    await host.click('[data-review-more]');query=new URL(requests.at(-1).path,'https://example.invalid').searchParams;assert.equal(query.get('offset'),'30');assert.match(host.innerHTML,/KPS-102/);assert.doesNotMatch(host.innerHTML,/KPS-101/);assert.match(host.innerHTML,/70\.00/,'brand total must cover complete ledger rather than one page');
    await host.click('[data-review-brand="enbishan"]');query=new URL(requests.at(-1).path,'https://example.invalid').searchParams;assert.equal(query.get('brand_id'),'enbishan');assert.equal(query.get('offset'),'0');assert.match(host.innerHTML,/ENB-201/);assert.doesNotMatch(host.innerHTML,/KPS-102|KPS-101/);assert.match(host.innerHTML,lang==='ar'?/يحتاج متابعة/:/Needs attention/);assert.match(host.innerHTML,lang==='ar'?/شبكة/:/Card/);
    await host.click('[data-review-order="enbishan-one"]');assert.equal(requests.at(-1).path,'/api/orders/enbishan-one');assert.equal(requests.at(-1).options.brand,'enbishan');const detail=host.querySelector('[data-review-detail="enbishan-one"]');assert.equal(detail.hidden,false);assert.match(detail.innerHTML,/&lt;img/);assert.match(detail.innerHTML,/&lt;script&gt;/);assert.doesNotMatch(detail.innerHTML,/<img|<script>/);
    const beforeHistory=requests.length;await host.click('[data-review-history]');assert.equal(requests.length,beforeHistory+1,'history must fetch matching records before changing the view label');query=new URL(requests.at(-1).path,'https://example.invalid').searchParams;assert.equal(query.get('view'),'history');assert(query.get('start'));assert(query.get('end'));
    host.querySelector('[data-review-start]').value='2026-10-20';host.querySelector('[data-review-end]').value='2026-10-26';await host.click('[data-review-apply]');query=new URL(requests.at(-1).path,'https://example.invalid').searchParams;assert.equal(query.get('start'),'2026-10-20');assert.equal(query.get('end'),'2026-10-26');
    const beforeInvalid=requests.length;host.querySelector('[data-review-start]').value='2026-11-01';host.querySelector('[data-review-end]').value='2026-10-26';await host.click('[data-review-apply]');assert.equal(requests.length,beforeInvalid);assert(host.querySelector('[data-review-error]').textContent);
    const closureHost=new Host(),draft={cash:'50.00',terminal:'66.50',reason:'actual draft'},draftBefore=JSON.stringify(draft),closureRequests=[];
    await context.window.KPSDriverOrderReview.mount({...options,host:closureHost,shiftId:'exact-closed-shift',api:async(path,opts)=>{closureRequests.push({path,opts});return api(path,opts);}});
    assert.equal(JSON.stringify(draft),draftBefore);assert.doesNotMatch(closureHost.innerHTML,/<form\b|data-review-history|data-review-start|data-review-end|data-review-current/);
    for(const r of closureRequests){query=new URL(r.path,'https://example.invalid').searchParams;assert.equal(query.get('shift_id'),'exact-closed-shift');assert.equal(query.get('driver_id'),null);assert.equal(query.get('start'),null);assert.equal(query.get('end'),null);}
    await closureHost.click('[data-review-brand="enbishan"]');query=new URL(closureRequests.at(-1).path,'https://example.invalid').searchParams;assert.equal(query.get('shift_id'),'exact-closed-shift');assert.equal(query.get('brand_id'),'enbishan');assert.equal(JSON.stringify(draft),draftBefore);
    // Execute the real closure entry point: it must keep the existing form/draft and mount only its slot.
    const ops=fs.readFileSync('public/ops.js','utf8'),helper=ops.slice(ops.indexOf('  function driverOrderReviewControl('),ops.indexOf('  async function drivers(){'));
    const slotHost=new Host();slotHost.hidden=true;slotHost.dataset={};const closureForm={cash:{value:'50.00'},terminal:{value:'66.50'},reason:{value:'Actual reason'}};
    const closeButton={dataset:{id:'fixed-shift'},closest:selector=>{assert.equal(selector,'.driver-order-review-slot');return {querySelector:()=>slotHost};},setAttribute:()=>{}};
    Object.assign(context,{state:{user:{role:'delivery_manager'}},can:()=>true,api,tr,esc:escape,money:options.money,datetime:String,statusName:v=>v,modal:()=>{throw Error('Opening closure orders must not replace the closure dialog');}});
    vm.runInContext(helper,context);assert.match(context.driverOrderReviewControl({id:'fixed-shift',kind:'driver'}),/type="button"/);
    await context.closureOrderReview(closeButton);assert.equal(slotHost.hidden,false);assert.equal(closureForm.cash.value,'50.00');assert.equal(closureForm.terminal.value,'66.50');assert.equal(closureForm.reason.value,'Actual reason');
    const requestsBeforeToggle=requests.length;await context.closureOrderReview(closeButton);await context.closureOrderReview(closeButton);assert.equal(requests.length,requestsBeforeToggle,'reopening existing review should retain state without remounting listeners');
    context.state.user.role='driver';assert.equal(context.driverOrderReviewControl({id:'fixed-shift',kind:'driver'}),'','driver must not gain the manager money review');
    const retryHost=new Host();let failed=true;await context.window.KPSDriverOrderReview.mount({...options,host:retryHost,api:async path=>{if(failed)throw Error('<backend failure>');return api(path);}});assert.equal(retryHost.querySelector('[data-review-error]').textContent,'<backend failure>');assert.equal(retryHost.querySelector('[data-review-retry]').attributes.type,'button');failed=false;await retryHost.click('[data-review-retry]');assert.match(retryHost.innerHTML,/KPS-101/);assert.equal(retryHost.attributes['aria-busy'],'false');
    const emptyHost=new Host();await context.window.KPSDriverOrderReview.mount({...options,host:emptyHost,api:async()=>({brands:[],orders:[],next_offset:null,total_count:0})});assert.match(emptyHost.innerHTML,lang==='ar'?/لا توجد طلبات/:/No orders/);assert.equal(emptyHost.attributes['aria-busy'],'false');
  }
  console.log('PASS: AR/EN current workday, separate brand rows and full-ledger totals, paging, payment/status, canonical branded details, history dates, exact closure scope, non-submitting controls, escaped text and empty states.');
})().catch(e=>{console.error(e);process.exitCode=1;});
