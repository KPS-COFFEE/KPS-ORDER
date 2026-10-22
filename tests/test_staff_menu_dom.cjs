/* Exercise the actual manager form with DOM events and inspect its quote payload. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require(process.env.KPS_JSDOM_MODULE||'jsdom');
const source=fs.readFileSync('public/assets/customer-order.js','utf8');

async function journey(morning,lang){
  const dom=new JSDOM('<main id="mount"></main>',{url:'https://fixture.invalid',runScripts:'outside-only'});
  const w=dom.window;w.structuredClone=structuredClone;
  const products=[{id:'plain',name:'Coffee',price_halalas:1000,available:true,image_url:'/assets/coffee.webp',option_groups:[]},
    {id:'options',name:'Cup',price_halalas:2000,available:true,image_url:'/assets/cup.webp',option_groups:[
      {code:'color',name:'Color',required:true,min_select:1,max_select:1,values:[{code:'red',name:'Red'},{code:'blue',name:'Blue'},{code:'hidden',name:'Hidden',available:false}]},
      {code:'flavor',name:'Flavor',required:true,min_select:1,max_select:1,values:[{code:'vanilla',name:'Vanilla'},{code:'lemon',name:'Lemon'}]}]}];
  let submit;const writes=[];
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  w.eval(source);
  await w.KPSCustomerOrder.open({
    api:async path=>path==='/api/shifts/current'?{shifts:[{user_id:'manager',kind:'manager',status:'open',branch_ids:['b2']}]}:
      path.startsWith('/api/catalog?')?{products}:(()=>{throw Error('Unexpected API '+path)})(),
    mutation:async(path,body)=>{writes.push({path,body});assert.equal(path,'/api/quote');return {quote:{subtotal_halalas:1000,delivery_fee_halalas:0,total_halalas:1000}};},
    tr:(ar,en)=>lang==='ar'?ar:en,esc,money:n=>String(n),
    modal:(_title,html,callback)=>{w.document.querySelector('#mount').innerHTML=html;submit=callback;},
    closeModal:()=>{},onCreated:()=>{},
    user:{id:'manager',brand_id:'kps',role:'delivery_manager',...(morning?{delivery_context:{branch_ids:['b2']}}:{})},
    branches:[{id:'b2',name:'Branch 2'}],
  });
  const form=w.document.querySelector('form'),cart=form.querySelector('#staff-cart');
  const click=selector=>{const el=form.querySelector(selector);assert.ok(el,selector);el.click();};
  const total=id=>Number(form.querySelector(`[data-product-count="${id}"]`).textContent);
  const choose=(group,value)=>{const el=form.querySelector(`[data-group="${group}"][value="${value}"]`);assert.ok(el);el.checked=true;el.dispatchEvent(new w.Event('change',{bubbles:true}));};
  const tap=id=>click(`.staff-menu-photo[data-menu-add="${id}"] img`);
  tap('plain');tap('plain');tap('plain');assert.equal(total('plain'),3);assert.equal(cart.children.length,1);
  click('[data-cart-minus="0"]');assert.equal(total('plain'),2);
  click('[data-cart-plus="0"]');assert.equal(total('plain'),3);
  tap('options');assert.equal(total('options'),0);assert.match(form.querySelector('.form-error').textContent,/Color/);
  assert.equal(form.querySelector('[value="hidden"]'),null);
  choose(0,'red');tap('options');assert.equal(total('options'),0);assert.match(form.querySelector('.form-error').textContent,/Flavor/);
  choose(1,'vanilla');tap('options');tap('options');assert.equal(total('options'),2);assert.equal(cart.children.length,2);
  // A changed option creates a separate line; it cannot change the prior units.
  form.querySelector('[value="red"]').checked=false;choose(0,'blue');tap('options');assert.equal(total('options'),3);assert.equal(cart.children.length,3);
  choose(0,'red');tap('options');assert.equal(total('options'),3); // max_select enforced
  form.querySelector('[value="red"]').checked=false;
  click('[data-menu-minus="options"]');assert.equal(total('options'),2);assert.equal(cart.children.length,2);
  click('[data-cart-plus="1"]');assert.equal(total('options'),3); // increments the stored red/vanilla line
  tap('plain');tap('options');assert.equal(total('options'),3); // changing product resets mandatory choices
  assert.equal(form.querySelectorAll('#staff-options input:checked').length,0);
  // Review sends canonical phone and a full independent option set per unit.
  for(const [key,value] of Object.entries({name:'Fixture',phone:'\u200e+966 53 104 1449\u200e',neighborhood:'N',address:'A',lat:'28.42',lng:'45.97'}))form.elements[key].value=value;
  form.elements.phone.dispatchEvent(new w.Event('input',{bubbles:true}));
  assert.equal(form.elements.phone.value,'+966531041449');
  const fd=new w.FormData(form);
  // jsdom does not emit the native browser FormData construction event.
  form.dispatchEvent(Object.assign(new w.Event('formdata'),{formData:fd}));
  await submit(fd,form);
  assert.equal(writes.length,1);const body=writes[0].body;
  assert.equal(body.customer.phone,'+966531041449');assert.equal(body.branch_id,'b2');
  const variant=body.items.find(item=>item.product_id==='options');
  assert.equal(variant.quantity,3);assert.equal(variant.units.length,3);
  for(const unit of variant.units)assert.equal(JSON.stringify(unit.options),JSON.stringify({color:['red'],flavor:['vanilla']}));
  // Plus respects the server's existing 50-unit limit, including rapid clicks.
  for(let i=0;i<55;i++)click('[data-cart-plus="0"]');
  assert.equal(total('plain'),50);assert.ok(form.querySelector('.form-error').textContent);
  dom.window.close();
}
(async()=>{for(const morning of [false,true])for(const lang of ['ar','en'])await journey(morning,lang);console.log('Staff image taps, +/- variants, required options and WhatsApp phone: 4 journeys PASS');})().catch(error=>{console.error(error);process.exitCode=1;});
