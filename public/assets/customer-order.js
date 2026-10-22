/* Staff-entered orders reuse server pricing, coverage and immutable receipts. */
(() => {
  'use strict';
  window.KPSCustomerOrder={open:async function(ctx){
    const {api,mutation,tr,esc,money,modal,closeModal,user,branches,onCreated,complimentary=false}=ctx;
    const morning=Boolean(user.delivery_context);
    const current=await api('/api/shifts/current');
    const covered=new Set((current.shifts||[]).filter(s=>s.user_id===user.id&&s.kind==='manager'&&s.status==='open').flatMap(s=>s.branch_ids||[]));
    const allowed=branches.filter(b=>covered.has(b.id)&&(!morning||user.delivery_context.branch_ids.includes(b.id)));
    if(!allowed.length)throw new Error(tr('افتح شفت مدير توصيل أولًا.','Open your delivery manager shift first.'));
    let products=[],cart=[],review=null,sequence=0,lookupSequence=0,lookupTimer,selectedContactPhone=null;
    const input=(label,name,type='text',extra='')=>`<label>${esc(label)}<input name="${name}" type="${type}" ${extra}></label>`;
    modal(complimentary?tr('ضيافة / تعويض — توصيل','Hospitality / compensation — delivery'):tr('طلب نيابة عن العميل','Order for a customer'),`<form class="form-columns" id="staff-order-form">
      <p class="notice full">${esc(tr('الفاتورة باسم مركز التوصيل. اختر فرع التجهيز وأدخل بيانات العميل. الدفع يسجل عند التحصيل الفعلي.','The receipt shows Delivery Center. Select the fulfillment branch and enter customer details. Payment is recorded when actually collected.'))}</p>
      <label>${esc(tr('فرع التجهيز','Fulfillment branch'))}<select name="branch_id">${allowed.map(b=>`<option value="${esc(b.id)}">${esc(b.name)}</option>`).join('')}</select></label>
      <label>${esc(tr('الدفع عند الاستلام','Payment on delivery'))}<select name="payment_method"><option value="cash">${esc(tr('كاش','Cash'))}</option><option value="card">${esc(tr('شبكة','Card'))}</option><option value="split">${esc(tr('كاش وشبكة','Cash and card'))}</option></select></label>
      ${input(tr('اسم العميل — قابل للتعديل','Customer name — editable'),'name','text','value="عميل" maxlength="100"')}${input(tr('رقم جوال العميل','Customer phone'),'phone','tel','required dir="ltr" maxlength="30"')}
      <section class="notice full" id="staff-customer-lookup" aria-live="polite">${esc(tr('أدخل الجوال كاملًا للبحث ضمن الفروع المصرح بها. لا يثبت البحث ملكية الرقم.','Enter the full mobile to search permitted branches. Lookup does not verify number ownership.'))}</section>
      ${input(tr('الحي','Neighborhood'),'neighborhood','text','required maxlength="120"')}${input(tr('العنوان ووصف المدخل','Address and entrance'),'address','text','required maxlength="500"')}
      <label class="full">${esc(tr('رابط دبوس جوجل ماب — أو أدخل الإحداثيات أدناه','Google Maps pin link — or enter coordinates below'))}<input name="map_url" type="url" dir="ltr" maxlength="2000"></label>
      ${input(tr('خط العرض','Latitude'),'lat','number','step="any" min="-90" max="90" dir="ltr"')}${input(tr('خط الطول','Longitude'),'lng','number','step="any" min="-180" max="180" dir="ltr"')}
      <fieldset class="full"><legend>${esc(tr('الأصناف','Items'))}</legend><p>${esc(tr('اضغط الصورة لإضافة حبة. أكمل النكهة أو اللون أو المقاس المطلوب أولًا.','Tap the photo to add one. Choose any required flavor, color or size first.'))}</p><div id="staff-menu" class="staff-menu-grid"></div><div class="form-columns"><label>${esc(tr('الصنف','Item'))}<select id="staff-product"></select></label>${input(tr('الكمية بنفس الخيارات','Quantity with these options'),'item_quantity','number','value="1" min="1" max="50" step="1"')}</div><div id="staff-options" class="form-columns" tabindex="-1"></div>
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
      const phone=normalizedPhone(fd.get('phone'));
      if(!phone)throw new Error(tr('أدخل رقم جوال سعودي كاملًا.','Enter a complete Saudi mobile number.'));
      const body={brand_id:user.brand_id,branch_id:fd.get('branch_id'),service:'delivery',payment_method:fd.get('payment_method'),customer:{name:fd.get('name').trim()||'عميل',phone,neighborhood:fd.get('neighborhood'),address:fd.get('address'),location},notes:fd.get('notes'),items:cart.map(({label,...item})=>item)};
      if(complimentary){body.kind=fd.get('kind');body.reason=String(fd.get('notes')||'').trim();body.payment_method='cash';if(!body.reason)throw new Error(tr('اكتب السبب باختصار.','Enter a short reason.'));}
      const key=JSON.stringify(body);
      if(!review||review.key!==key){
        const {quote}=await mutation(complimentary?'/api/orders/complimentary/quote':'/api/quote',body);
        review={key,total:quote.total_halalas};
        form.querySelector('#staff-review').innerHTML=`<div class="notice"><p>${esc(tr('الأصناف','Subtotal'))}: <b>${esc(money(quote.subtotal_halalas))}</b></p><p>${esc(tr('التوصيل','Delivery'))}: <b>${esc(money(quote.delivery_fee_halalas))}</b></p><p>${esc(tr('الإجمالي','Total'))}: <b>${esc(money(quote.total_halalas))}</b></p><p>${esc(tr('راجع البيانات مع العميل ثم أكد الطلب.','Review with the customer, then confirm.'))}</p></div>`;
        if(complimentary){const p=document.createElement('p');p.textContent=tr('بدون تحصيل — القيمة المرجعية للأصناف: ','No collection — reference item value: ')+money(quote.complimentary.catalog_value_halalas);form.querySelector('#staff-review').append(p);}
        form.querySelector('[type=submit]').textContent=complimentary?tr('إرسال للفرع بدون تحصيل','Send to branch — no collection'):tr('تأكيد وإنشاء الطلب','Confirm and create order');return;
      }
      const result=await mutation(complimentary?'/api/orders/complimentary':'/api/orders/delivery-center',{...body,expected_total_halalas:review.total});
      closeModal();await onCreated(result.order.id);
    });
    const form=document.querySelector('#staff-order-form'),select=form.querySelector('#staff-product'),options=form.querySelector('#staff-options'),add=form.querySelector('#staff-add');
    if(morning){
      form.querySelector('p.notice').textContent=tr('طلب واتساب — صباحي حفر الباطن، تجهيز فرع 2. يصل للمحضّر كطلب جديد بانتظار القبول. أدخل بيانات العميل وموقعه كما أرسلها؛ الجوال غير موثق بهذه العملية.','WhatsApp order — Hafar morning, prepared at Branch 2. It reaches the preparer as a new order awaiting acceptance. Enter the customer details and location as provided; this does not verify mobile ownership.');
      form.elements.branch_id.disabled=true;
      form.addEventListener('formdata',event=>event.formData.set('branch_id',allowed[0].id));
    }
    if(complimentary){
      form.querySelector('p.notice').textContent=tr('توصيل ضيافة أو تعويض بدون تحصيل. اختر الأصناف وأدخل المستلم وموقعه والسبب.','Hospitality or compensation delivery with no collection. Select items, recipient, location and reason.');
      const payment=form.elements.payment_method;payment.value='cash';payment.closest('label').hidden=true;
      const label=document.createElement('label');label.textContent=tr('النوع','Type');const kinds=document.createElement('select');kinds.name='kind';
      for(const [value,ar,en] of [['hospitality','ضيافة','Hospitality'],['advertising','ضيافة إعلانية','Advertising hospitality'],['compensation','تعويض','Compensation']]){const option=document.createElement('option');option.value=value;option.textContent=tr(ar,en);kinds.append(option);}label.append(kinds);payment.closest('label').after(label);
      form.elements.notes.required=true;form.elements.notes.placeholder=tr('السبب، مثل منتج تالف أو خطأ بالطلب','Reason, e.g. damaged product or order error');
    }
    const fail=e=>{form.querySelector('.form-error').textContent=e.message;};
    function invalidate(){review=null;form.querySelector('#staff-review').textContent='';form.querySelector('[type=submit]').textContent=tr('مراجعة الإجمالي','Review total');}
    form.addEventListener('input',invalidate);form.addEventListener('change',invalidate);
    const lookupBox=form.querySelector('#staff-customer-lookup');
    if(morning)lookupBox.textContent=tr('أدخل بيانات العميل من محادثة الواتساب. سجل العملاء السابق غير متاح بهذه الصلاحية.','Enter customer details from the WhatsApp conversation. Previous customer history is not available with this access.');
    function compactPhone(value){return String(value).replace(/[٠-٩]/g,d=>String('٠١٢٣٤٥٦٧٨٩'.indexOf(d))).replace(/[۰-۹]/g,d=>String('۰۱۲۳۴۵۶۷۸۹'.indexOf(d))).replace(/[\s()\-\u061c\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]/g,'');}
    function normalizedPhone(value){const phone=compactPhone(value);return /^0?5[0-9]{8}$/.test(phone)?'+966'+phone.replace(/^0/,''):/^\+?9665[0-9]{8}$/.test(phone)?'+'+phone.replace(/^\+/,''):/^009665[0-9]{8}$/.test(phone)?'+'+phone.slice(2):null;}
    const phoneInput=form.elements.phone;
    // Remove presentation characters without rewriting the country code while
    // the user edits, or moving the selection to the end of the field.
    phoneInput.addEventListener('input',()=>{
      const value=phoneInput.value,clean=compactPhone(value);
      if(value===clean)return;
      const start=compactPhone(value.slice(0,phoneInput.selectionStart)).length;
      const end=compactPhone(value.slice(0,phoneInput.selectionEnd)).length;
      const direction=phoneInput.selectionDirection;
      phoneInput.value=clean;phoneInput.setSelectionRange(start,end,direction);
    });
    // Clean before maxlength is applied, including WhatsApp's invisible markers.
    phoneInput.addEventListener('paste',event=>{
      if(!event.clipboardData)return;
      const text=compactPhone(event.clipboardData.getData('text'));
      const start=phoneInput.selectionStart,end=phoneInput.selectionEnd;
      if(phoneInput.value.length-(end-start)+text.length>phoneInput.maxLength)return;
      event.preventDefault();phoneInput.setRangeText(text,start,end,'end');
      phoneInput.dispatchEvent(new Event('input',{bubbles:true}));
    });
    async function lookupCustomer(){const seq=++lookupSequence,phone=normalizedPhone(form.elements.phone.value);if(!phone){lookupBox.textContent=tr('أدخل رقم جوال سعودي كاملًا.','Enter a complete Saudi mobile number.');return;}lookupBox.textContent=tr('جارٍ البحث…','Searching…');try{const data=await api('/api/orders/customer-lookup?'+new URLSearchParams({phone,branch_id:form.elements.branch_id.value}));if(seq!==lookupSequence||!form.isConnected)return;const contacts=data.contacts||[];lookupBox.innerHTML=`<p>${esc(contacts.length?data.ambiguous?tr('توجد بيانات متعددة؛ اختر البيانات المناسبة بعد تأكيدها مع العميل. لا يتم دمج الحسابات.','Multiple records found. Confirm with the customer and choose the appropriate details. Accounts are not merged.'):tr('بيانات مسجلة — اختر العنوان بعد تأكيده مع العميل.','Saved details — confirm the address with the customer before selecting.'):tr('رقم جديد؛ تحفظ بيانات العميل عند تأكيد أول طلب.','New number. Customer details are saved when the first order is confirmed.'))}</p><p class="small-text">${esc(tr('الجوال غير موثق بهذه العملية.','This operation does not verify mobile ownership.'))}</p>`;contacts.forEach(contact=>{const addresses=contact.addresses?.length?contact.addresses:[contact];addresses.forEach(address=>{const item={...contact,...address};const button=document.createElement('button');button.type='button';button.className='quiet customer-address-choice';button.textContent=[item.name,item.phone,item.neighborhood,item.address,item.location?`${item.location.lat}, ${item.location.lng}`:''].filter(Boolean).join(' · ');button.addEventListener('click',()=>{if(normalizedPhone(form.elements.phone.value)!==phone)return;selectedContactPhone=phone;for(const key of ['name','address','neighborhood'])form.elements[key].value=item[key]||'';form.elements.lat.value=item.location?.lat??'';form.elements.lng.value=item.location?.lng??'';form.elements.map_url.value='';invalidate();lookupBox.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));});lookupBox.append(button);});});}catch(e){if(seq===lookupSequence)lookupBox.textContent=e.message;}}
    function clearSelectedContact(){if(!selectedContactPhone)return;selectedContactPhone=null;for(const key of ['name','address','neighborhood','lat','lng','map_url'])form.elements[key].value='';invalidate();}
    if(!morning){
      form.elements.phone.addEventListener('input',()=>{if(selectedContactPhone&&normalizedPhone(form.elements.phone.value)!==selectedContactPhone)clearSelectedContact();++lookupSequence;clearTimeout(lookupTimer);lookupBox.textContent=tr('أدخل الجوال كاملًا للبحث.','Enter the complete mobile to search.');lookupTimer=setTimeout(lookupCustomer,400);});
      form.elements.branch_id.addEventListener('change',()=>{clearSelectedContact();++lookupSequence;clearTimeout(lookupTimer);lookupCustomer();});
    }
    function drawOptions(){const p=products.find(p=>p.id===select.value);options.innerHTML=(p?.option_groups||[]).map((g,i)=>`<fieldset><legend>${esc(g.name)} · ${esc(tr('الاختيارات','Selections'))} ${g.min_select??(g.required===false?0:1)}–${g.max_select??1}</legend>${(g.values||[]).filter(v=>v.available!==false&&v.enabled!==false).map(v=>`<label><input type="checkbox" data-group="${i}" value="${esc(v.code||v.id)}">${esc(v.name)} ${Number(v.price_delta_halalas)?`(+${esc(money(v.price_delta_halalas))})`:''}</label>`).join('')}</fieldset>`).join('');add.disabled=!p;}
    function photoUrl(value){try{const url=new URL(value||'',location.origin);return value&&(url.protocol==='https:'||url.origin===location.origin)?url.href:null;}catch{return null;}}
    function drawMenu(){form.querySelector('#staff-menu').innerHTML=products.map(p=>{const photo=photoUrl(p.image_url);return `<article class="staff-menu-card"><button type="button" class="staff-menu-photo quiet" data-menu-add="${esc(p.id)}" aria-label="${esc(tr('إضافة حبة من ','Add one ')+p.name)}">${photo?`<img src="${esc(photo)}" alt="${esc(p.name)}" loading="lazy">`:`<span>${esc(p.name)}</span>`}</button><strong>${esc(p.name)}</strong><span>${esc(money(p.price_halalas))}</span><div class="staff-menu-quantity"><button type="button" class="quiet" data-menu-minus="${esc(p.id)}" aria-label="${esc(tr('إنقاص حبة من ','Remove one ')+p.name)}">−</button><output data-product-count="${esc(p.id)}" aria-live="polite">0</output><button type="button" class="quiet" data-menu-add="${esc(p.id)}" aria-label="${esc(tr('زيادة حبة من ','Increase one ')+p.name)}">+</button></div></article>`;}).join('');updateCounts();}
    function updateCounts(){for(const count of form.querySelectorAll('[data-product-count]'))count.textContent=String(cart.filter(item=>item.product_id===count.dataset.productCount).reduce((sum,item)=>sum+item.quantity,0));}
    async function load(){const seq=++sequence;add.disabled=true;select.disabled=true;form.querySelector('#staff-menu').replaceChildren();try{const d=await api(`/api/catalog?brand_id=${encodeURIComponent(user.brand_id)}&branch_id=${encodeURIComponent(form.elements.branch_id.value)}&service=delivery`);if(seq!==sequence||!form.isConnected)return;products=d.products.filter(p=>p.available);select.innerHTML=products.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} — ${esc(money(p.price_halalas))}</option>`).join('');drawOptions();drawMenu();}catch(e){if(seq===sequence)fail(e);}finally{if(seq===sequence)select.disabled=false;}}
    function drawCart(){form.querySelector('#staff-cart').innerHTML=cart.map((item,i)=>`<div class="actions"><span>${esc(item.label)} × ${item.quantity}${item.note?` · ${esc(item.note)}`:''}</span><button type="button" class="quiet" data-cart-minus="${i}" aria-label="${esc(tr('إنقاص ','Decrease ')+item.label)}">−</button><button type="button" class="quiet" data-cart-plus="${i}" aria-label="${esc(tr('زيادة ','Increase ')+item.label)}">+</button><button type="button" class="quiet" data-remove="${i}">${esc(tr('حذف','Remove'))}</button></div>`).join('');updateCounts();invalidate();}
    function selectedItem(quantity){const p=products.find(p=>p.id===select.value);if(!p)throw new Error(tr('اختر الصنف.','Select an item.'));if(!Number.isInteger(quantity)||quantity<1||quantity>50)throw new Error(tr('الكمية من 1 إلى 50.','Quantity must be 1–50.'));const choices={},labels=[];for(const [i,g] of (p.option_groups||[]).entries()){const selected=[...options.querySelectorAll(`[data-group="${i}"]:checked`)].map(x=>x.value);if(selected.length<(g.min_select??(g.required===false?0:1))||selected.length>(g.max_select??1)){options.focus();throw new Error(tr('راجع اختيارات ','Check selections for ')+g.name);}choices[g.code||g.id]=selected;labels.push(...selected.map(code=>g.values.find(v=>(v.code||v.id)===code)?.name||code));}return {product_id:p.id,quantity,units:Array.from({length:quantity},()=>({options:structuredClone(choices)})),note:form.elements.item_note.value,label:p.name+(labels.length?' · '+labels.join(' / '):'')};}
    function sameVariant(a,b){return a.product_id===b.product_id&&a.note===b.note&&JSON.stringify(a.units[0].options)===JSON.stringify(b.units[0].options);}
    function changeQuantity(item,delta){const next=item.quantity+delta;if(next>50)throw new Error(tr('الكمية القصوى لنفس الاختيارات 50.','Maximum quantity for the same options is 50.'));if(next<=0){cart.splice(cart.indexOf(item),1);return;}const choices=item.units[0].options;item.quantity=next;item.units=Array.from({length:next},()=>({options:structuredClone(choices)}));}
    function addSelected(quantity){const candidate=selectedItem(quantity),existing=cart.find(item=>sameVariant(item,candidate));if(existing)changeQuantity(existing,quantity);else{if(cart.length>=50)throw new Error(tr('وصلت للحد الأقصى للأصناف.','Maximum item rows reached.'));cart.push(candidate);}drawCart();form.querySelector('.form-error').textContent='';}
    add.addEventListener('click',()=>{try{addSelected(Number(form.elements.item_quantity.value));}catch(e){fail(e);}});
    form.querySelector('#staff-menu').addEventListener('click',e=>{const button=e.target.closest('[data-menu-add],[data-menu-minus]');if(!button||select.disabled)return;try{const id=button.dataset.menuAdd||button.dataset.menuMinus;if(select.value!==id){select.value=id;form.elements.item_note.value='';drawOptions();}if(button.dataset.menuAdd)addSelected(1);else{const candidate=selectedItem(1),existing=cart.find(item=>sameVariant(item,candidate));if(existing){changeQuantity(existing,-1);drawCart();}}}catch(error){fail(error);}});
    form.querySelector('#staff-cart').addEventListener('click',e=>{const b=e.target.closest('[data-remove],[data-cart-minus],[data-cart-plus]');if(!b)return;try{if(b.dataset.remove!==undefined)cart.splice(Number(b.dataset.remove),1);else{const index=Number(b.dataset.cartPlus??b.dataset.cartMinus),item=cart[index];if(item)changeQuantity(item,b.dataset.cartPlus!==undefined?1:-1);}drawCart();form.querySelector('.form-error').textContent='';}catch(error){fail(error);}});
    select.addEventListener('change',()=>{form.elements.item_note.value='';drawOptions();});form.elements.branch_id.addEventListener('change',load);await load();
  }};
})();
