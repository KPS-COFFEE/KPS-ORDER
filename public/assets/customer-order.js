/* Staff-entered orders reuse server pricing, coverage and immutable receipts. */
(() => {
  'use strict';
  window.KPSCustomerOrder={open:async function(ctx){
    const {api,mutation,tr,esc,money,modal,closeModal,user,branches,onCreated}=ctx;
    const current=await api('/api/shifts/current');
    const covered=new Set((current.shifts||[]).filter(s=>s.user_id===user.id&&s.kind==='manager'&&s.status==='open').flatMap(s=>s.branch_ids||[]));
    const allowed=branches.filter(b=>covered.has(b.id));
    if(!allowed.length)throw new Error(tr('افتح شفت مدير توصيل أولًا.','Open your delivery manager shift first.'));
    let products=[],cart=[],review=null,sequence=0;
    const input=(label,name,type='text',extra='')=>`<label>${esc(label)}<input name="${name}" type="${type}" ${extra}></label>`;
    modal(tr('طلب نيابة عن العميل','Order for a customer'),`<form class="form-columns" id="staff-order-form">
      <p class="notice full">${esc(tr('الفاتورة باسم مركز التوصيل. اختر فرع التجهيز وأدخل بيانات العميل. الدفع يسجل عند التحصيل الفعلي.','The receipt shows Delivery Center. Select the fulfillment branch and enter customer details. Payment is recorded when actually collected.'))}</p>
      <label>${esc(tr('فرع التجهيز','Fulfillment branch'))}<select name="branch_id">${allowed.map(b=>`<option value="${esc(b.id)}">${esc(b.name)}</option>`).join('')}</select></label>
      <label>${esc(tr('الدفع عند الاستلام','Payment on delivery'))}<select name="payment_method"><option value="cash">${esc(tr('كاش','Cash'))}</option><option value="card">${esc(tr('شبكة','Card'))}</option><option value="split">${esc(tr('كاش وشبكة','Cash and card'))}</option></select></label>
      ${input(tr('اسم العميل — قابل للتعديل','Customer name — editable'),'name','text','value="عميل" maxlength="100"')}${input(tr('رقم جوال العميل','Customer phone'),'phone','tel','required dir="ltr" maxlength="30"')}
      ${input(tr('الحي','Neighborhood'),'neighborhood','text','required maxlength="120"')}${input(tr('العنوان ووصف المدخل','Address and entrance'),'address','text','required maxlength="500"')}
      <label class="full">${esc(tr('رابط دبوس جوجل ماب — أو أدخل الإحداثيات أدناه','Google Maps pin link — or enter coordinates below'))}<input name="map_url" type="url" dir="ltr" maxlength="2000"></label>
      ${input(tr('خط العرض','Latitude'),'lat','number','step="any" min="-90" max="90" dir="ltr"')}${input(tr('خط الطول','Longitude'),'lng','number','step="any" min="-180" max="180" dir="ltr"')}
      <fieldset class="full"><legend>${esc(tr('الأصناف','Items'))}</legend><div class="form-columns"><label>${esc(tr('الصنف','Item'))}<select id="staff-product"></select></label>${input(tr('الكمية بنفس الخيارات','Quantity with these options'),'item_quantity','number','value="1" min="1" max="50" step="1"')}</div><div id="staff-options" class="form-columns"></div>
      ${input(tr('ملاحظة الصنف','Item note'),'item_note','text','maxlength="300"')}<button type="button" id="staff-add" class="quiet">${esc(tr('إضافة للطلب','Add to order'))}</button><div id="staff-cart" aria-live="polite"></div></fieldset>
      <label class="full">${esc(tr('ملاحظات الطلب','Order notes'))}<textarea name="notes" maxlength="1000"></textarea></label>
      <div id="staff-review" class="full" aria-live="polite"></div><p class="form-error full" role="alert"></p><button type="submit" class="full">${esc(tr('مراجعة الإجمالي','Review total'))}</button></form>`,async(fd,form)=>{
      if(!cart.length)throw new Error(tr('أضف صنفًا واحدًا على الأقل.','Add at least one item.'));
      let location;
      if(fd.get('map_url').trim()){
        try{location=(await mutation('/api/location/parse-map-url',{url:fd.get('map_url').trim()})).location;}
        catch(error){
          const lat=Number(fd.get('lat')),lng=Number(fd.get('lng'));
          if(!fd.get('lat').trim()||!fd.get('lng').trim()||!Number.isFinite(lat)||!Number.isFinite(lng)||Math.abs(lat)>90||Math.abs(lng)>180)throw error;
          if(!window.confirm(tr('تعذر قراءة الرابط. هل تؤكد استخدام الإحداثيات التي أدخلتها لموقع العميل؟','Could not read the link. Confirm using the coordinates you entered for the customer?')))throw error;
          location={lat,lng};
        }
      }
      else {
        if(!fd.get('lat').trim()||!fd.get('lng').trim())throw new Error(tr('أدخل رابط دبوس العميل أو إحداثياته.','Enter the customer pin link or coordinates.'));
        location={lat:Number(fd.get('lat')),lng:Number(fd.get('lng'))};
      }
      const body={brand_id:user.brand_id,branch_id:fd.get('branch_id'),service:'delivery',payment_method:fd.get('payment_method'),customer:{name:fd.get('name').trim()||'عميل',phone:fd.get('phone'),neighborhood:fd.get('neighborhood'),address:fd.get('address'),location},notes:fd.get('notes'),items:cart.map(({label,...item})=>item)};
      const key=JSON.stringify(body);
      if(!review||review.key!==key){
        const {quote}=await mutation('/api/quote',body);
        review={key,total:quote.total_halalas};
        form.querySelector('#staff-review').innerHTML=`<div class="notice"><p>${esc(tr('الأصناف','Subtotal'))}: <b>${esc(money(quote.subtotal_halalas))}</b></p><p>${esc(tr('التوصيل','Delivery'))}: <b>${esc(money(quote.delivery_fee_halalas))}</b></p><p>${esc(tr('الإجمالي','Total'))}: <b>${esc(money(quote.total_halalas))}</b></p><p>${esc(tr('راجع البيانات مع العميل ثم أكد الطلب.','Review with the customer, then confirm.'))}</p></div>`;
        form.querySelector('[type=submit]').textContent=tr('تأكيد وإنشاء الطلب','Confirm and create order');return;
      }
      const result=await mutation('/api/orders/delivery-center',{...body,expected_total_halalas:review.total});
      closeModal();await onCreated(result.order.id);
    });
    const form=document.querySelector('#staff-order-form'),select=form.querySelector('#staff-product'),options=form.querySelector('#staff-options'),add=form.querySelector('#staff-add');
    const fail=e=>{form.querySelector('.form-error').textContent=e.message;};
    function invalidate(){review=null;form.querySelector('#staff-review').textContent='';form.querySelector('[type=submit]').textContent=tr('مراجعة الإجمالي','Review total');}
    form.addEventListener('input',invalidate);form.addEventListener('change',invalidate);
    function drawOptions(){const p=products.find(p=>p.id===select.value);options.innerHTML=(p?.option_groups||[]).map((g,i)=>`<fieldset><legend>${esc(g.name)} · ${esc(tr('الاختيارات','Selections'))} ${g.min_select??(g.required===false?0:1)}–${g.max_select??1}</legend>${(g.values||[]).filter(v=>v.available!==false&&v.enabled!==false).map(v=>`<label><input type="checkbox" data-group="${i}" value="${esc(v.code||v.id)}">${esc(v.name)} ${Number(v.price_delta_halalas)?`(+${esc(money(v.price_delta_halalas))})`:''}</label>`).join('')}</fieldset>`).join('');add.disabled=!p;}
    async function load(){const seq=++sequence;add.disabled=true;select.disabled=true;try{const d=await api(`/api/catalog?brand_id=${encodeURIComponent(user.brand_id)}&branch_id=${encodeURIComponent(form.elements.branch_id.value)}&service=delivery`);if(seq!==sequence||!form.isConnected)return;products=d.products.filter(p=>p.available);select.innerHTML=products.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} — ${esc(money(p.price_halalas))}</option>`).join('');drawOptions();}catch(e){if(seq===sequence)fail(e);}finally{if(seq===sequence)select.disabled=false;}}
    function drawCart(){form.querySelector('#staff-cart').innerHTML=cart.map((item,i)=>`<div class="actions"><span>${esc(item.label)} × ${item.quantity}${item.note?` · ${esc(item.note)}`:''}</span><button type="button" class="quiet" data-remove="${i}">${esc(tr('حذف','Remove'))}</button></div>`).join('');invalidate();}
    add.addEventListener('click',()=>{try{const p=products.find(p=>p.id===select.value);if(!p)return;const quantity=Number(form.elements.item_quantity.value);if(!Number.isInteger(quantity)||quantity<1||quantity>50)throw new Error(tr('الكمية من 1 إلى 50.','Quantity must be 1–50.'));if(cart.length>=50)throw new Error(tr('وصلت للحد الأقصى للأصناف.','Maximum item rows reached.'));const choices={},labels=[];for(const [i,g] of (p.option_groups||[]).entries()){const selected=[...options.querySelectorAll(`[data-group="${i}"]:checked`)].map(x=>x.value);if(selected.length<(g.min_select??(g.required===false?0:1))||selected.length>(g.max_select??1))throw new Error(tr('راجع اختيارات ','Check selections for ')+g.name);choices[g.code||g.id]=selected;labels.push(...selected.map(code=>g.values.find(v=>(v.code||v.id)===code)?.name||code));}cart.push({product_id:p.id,quantity,units:Array.from({length:quantity},()=>({options:choices})),note:form.elements.item_note.value,label:p.name+(labels.length?' · '+labels.join(' / '):'')});drawCart();form.querySelector('.form-error').textContent='';}catch(e){fail(e);}});
    form.querySelector('#staff-cart').addEventListener('click',e=>{const b=e.target.closest('[data-remove]');if(b){cart.splice(Number(b.dataset.remove),1);drawCart();}});
    select.addEventListener('change',drawOptions);form.elements.branch_id.addEventListener('change',load);await load();
  }};
})();
