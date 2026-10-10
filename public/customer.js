'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const brandParameters=new URLSearchParams(location.search);
  const brandValue=brandParameters.has('brand_id')?brandParameters.get('brand_id'):'kps';
  const requestedBrandId=brandParameters.getAll('brand_id').length<=1&&/^[a-z][a-z0-9_-]{1,59}$/.test(brandValue||'')?brandValue:null;
  const basketStorageKey=`kps-unconfirmed-basket-v2:${requestedBrandId||'invalid'}`;
  const state = {products: [], branches: [], brand: {id: requestedBrandId||'', name: requestedBrandId==='kps'?'KPS COFFEE':'الطلبات'}, sourceVerified: false, session: {user: null, csrf_token: null}, branchId: '', service: 'pickup', location: null, cart: [], quote: null, quoteError: '', quotePending: false, quoteRevision: 0, category: '', product: null, optionValues: [], banners: [], bannerIndex: 0, bannerTimer: null, presentation: null, tickerPaused: false, conversation: null, chatSequence: 0, chatTimer: null, ordersTimer: null, orders: [], nextOrdersOffset: null, activeOrder: null, challenge: null, afterLogin: null, confirmation: null, conversations: [], contactOrders: [], nextConversationOffset: null};
  state.legacyItemMappings={};
  state.channels={guest:{available:false},email:{available:false},sms:{available:false},whatsapp:{available:false}};
  const pendingKeys = new Map();
  const focusHistory = new WeakMap();
  const currency = new Intl.NumberFormat('ar-SA', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const number = new Intl.NumberFormat('ar-SA');
  const money = (halalas) => `${currency.format(Number(halalas || 0) / 100)} ر.س`;
  const key = () => globalThis.crypto?.randomUUID?.() || `kps-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const stableKey = (operation, payload) => {
    const signature = `${requestedBrandId}:${state.session.user?.id || 'guest'}:${operation}:${JSON.stringify(payload)}`;
    let a=0x811c9dc5,b=0x9e3779b9;
    for(let i=0;i<signature.length;i++){a=Math.imul(a^signature.charCodeAt(i),0x01000193)>>>0;b=Math.imul(b^signature.charCodeAt(i),0x85ebca6b)>>>0;}
    const storageKey=`kps-pending-operation:${requestedBrandId}:${a.toString(16)}${b.toString(16)}`;
    if(!pendingKeys.has(signature)){let saved;try{saved=sessionStorage.getItem(storageKey);}catch{}pendingKeys.set(signature,saved||key());try{sessionStorage.setItem(storageKey,pendingKeys.get(signature));}catch{}}
    return {signature,value:pendingKeys.get(signature),storageKey};
  };
  function releaseKey(idem){pendingKeys.delete(idem.signature);try{sessionStorage.removeItem(idem.storageKey);}catch{}}
  const node = (tag, cls, content) => { const el = document.createElement(tag); if (cls) el.className = cls; if (content != null) el.textContent = String(content); return el; };
  const text = (id, content) => { $(id).textContent = content; };
  const showError = (id, content) => { const el = $(id); el.textContent = content || ''; el.hidden = !content; };
  const safeImage = (url) => { if(typeof url!=='string'||!url.trim())return ''; try { const parsed = new URL(url, location.origin); return parsed.origin === location.origin && !parsed.pathname.startsWith('/api/admin') ? parsed.pathname + parsed.search : ''; } catch { return ''; } };
  const city = (name) => String(name || '').replace(/\s/g, '').replace('رفحا', 'رفحاء').replace('رفحاءء', 'رفحاء');
  const branch = () => state.branches.find((entry) => entry.id === state.branchId);
  const restricted = (product) => (product.available_cities || []).length > 0;
  const groups = (product) => product.option_groups || product.settings?.option_groups || [];
  const isMug = (product) => ['kps-30', 'kps-31'].includes(product.source_id || product.id) || groups(product).some((g) => g.code === 'COLOR');
  const isKhaliya = (product) => product.unified_khaliya === true;
  const cartLineName = (product, options={}) => isKhaliya(product||{}) ? `${product.name} — ${((groups(product).find((g)=>g.code==='TYPE')?.values)||[]).find((v)=>v.code===options.TYPE)?.name||'اختر النوع'}` : product?.name || 'صنف محفوظ';
  const optionLabel = (product, options) => groups(product).map((g) => { const v = (g.values || []).find((entry) => (entry.code || entry.id) === options[g.code]); return v ? `${g.name}: ${v.name}` : ''; }).filter(Boolean).join(' · ');
  const serviceName = (value) => ({delivery: 'توصيل', pickup: 'استلام من الفرع', car: 'استلام بالسيارة'}[value] || value);
  const validNeighborhood = value => typeof value==='string'&&!value.includes('\0')&&(value.trim().match(/[\p{L}\p{N}]/gu)||[]).length>=2;
  const statusName = (value) => ({new:'جديد', pending:'بانتظار المعالجة', accepted:'مقبول', preparing:'قيد التجهيز', ready:'جاهز', handed_over:'تم التسليم', handed_to_driver:'سُلّم للسائق', pending_assignment:'بانتظار الإسناد', assigned:'مسند', collected:'تم التحصيل', unpaid:'لم يُحصّل', paid:'مدفوع', partially_paid:'مدفوع جزئيًا', delivered:'تم التسليم', completed:'مكتمل', cancelled:'ملغي', rejected:'مرفوض', out_for_delivery:'في الطريق', picked_up:'استلم السائق', not_assigned:'غير مسند', unassigned:'غير مسند', refunded:'مسترد', partially_refunded:'مسترد جزئيًا', not_required:'لا ينطبق',not_applicable:'لا ينطبق', pending_acceptance:'بانتظار قبول المهمة', received:'مستلم', awaiting_payment:'بانتظار التحصيل'}[value] || 'قيد المعالجة');
  const dateTime = (value) => { if (!value) return ''; try { return new Intl.DateTimeFormat('ar-SA', {dateStyle:'short', timeStyle:'short', timeZone:'Asia/Riyadh'}).format(new Date(value)); } catch { return ''; } };
  function toast(message) { text('toast', message); $('toast').hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => { $('toast').hidden = true; }, 3400); }
  function openDialog(id) { const dialog = $(id); if (dialog.open) return; focusHistory.set(dialog, document.activeElement); dialog.showModal(); }
  function clearConfirmation() { const pending=state.confirmation; state.confirmation=null; if(pending?.cancel)pending.cancel(); }
  function closeDialog(dialog) { if (typeof dialog === 'string') dialog = $(dialog); if (dialog.open) { if(dialog.id==='confirm-dialog')clearConfirmation(); dialog.close(); } }
  async function api(path, {method='GET', data, idempotency, signal} = {}) {
    const headers = {'Accept': 'application/json'};
    if (data !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET' && method !== 'HEAD') { if (state.session.csrf_token) headers['X-CSRF-Token'] = state.session.csrf_token; headers['Idempotency-Key'] = idempotency || key(); }
    let response;
    try { response = await fetch(path, {method, credentials: 'same-origin', headers, body: data === undefined ? undefined : JSON.stringify(data), signal, cache: 'no-store'}); }
    catch (cause) { if (cause.name === 'AbortError') throw cause; const error = new Error('تعذر الاتصال بالموقع. تحقق من الإنترنت ثم حاول مجددًا؛ لم نؤكد حفظ هذه العملية.'); error.code = 'NETWORK'; throw error; }
    let body; try { body = await response.json(); } catch { body = {}; }
    if (!response.ok) {
      const detail = body.error || body.detail || body;
      const error = new Error(typeof detail.message === 'string' ? detail.message : typeof detail === 'string' && /[\u0600-\u06FF]/.test(detail) ? detail : response.status === 401 ? 'انتهت الجلسة. افتح جلسة ضيف جديدة أو ادخل ببريدك الموثق.' : response.status === 429 ? 'محاولات كثيرة. انتظر قليلًا ثم حاول مجددًا.' : 'تعذر إتمام العملية. راجع البيانات وحاول مرة أخرى.');
      error.code = detail.code || body.code || String(response.status); error.status = response.status; error.details = detail;
      if (response.status === 401) { state.session = {user: null, csrf_token: null}; renderAccount(); }
      throw error;
    }
    return body;
  }
  async function busy(button, fn) { if (button.disabled) return; const original = button.textContent; button.disabled = true; button.setAttribute('aria-busy', 'true'); try { return await fn(); } finally { button.disabled = false; button.removeAttribute('aria-busy'); button.textContent = original; if(button.id==='checkout-button')renderQuote(); } }
  function persistDraft() { try { localStorage.setItem(basketStorageKey, JSON.stringify({brandId: state.brand.id, cart: state.cart.map(({productId, quantity, options, note, restoredName}) => ({productId, quantity, options, note, restoredName}))})); } catch {} }
  function restoreDraft() {
    try {
      const saved=JSON.parse(localStorage.getItem(basketStorageKey)||(requestedBrandId==='kps'?localStorage.getItem('kps-unconfirmed-basket-v1'):null)||'{}');
      if(saved.brandId!==state.brand.id||!Array.isArray(saved.cart))return;
      state.cart=saved.cart.slice(0,100).filter((item)=>typeof item.productId==='string'&&Number.isInteger(item.quantity)&&item.quantity>=1&&item.quantity<=30).map((item)=>{
        const options=typeof item.options==='object'&&item.options&&!Array.isArray(item.options)?{...item.options}:{};
        const mapping=state.legacyItemMappings[item.productId];
        // The former IDs prove the original type. Never replace an explicit saved selection.
        const remapped=mapping&&Object.keys(options).length===0;
        return {productId:remapped?mapping.product_id:item.productId,quantity:item.quantity,
          options:remapped?{...mapping.options}:options,note:typeof item.note==='string'?item.note.slice(0,300):'',
          restoredName:remapped?mapping.original_name:typeof item.restoredName==='string'?item.restoredName.slice(0,100):'',rowId:key()};
      }).filter((item)=>state.products.some((product)=>product.id===item.productId)||state.legacyItemMappings[item.productId]);
      if(state.cart.some((item)=>item.restoredName))toast('تم ربط أصناف الخلية المحفوظة بأنواعها الأصلية. راجع النوع والكمية في السلة.');
    } catch {}
  }
  function syncMobileBasket() {const basket=$('basket'),viewport=window.visualViewport,height=viewport?.height||innerHeight,top=viewport?.offsetTop||0;basket.classList.toggle('basket-scroll-flow',basket.offsetHeight>height-40);const rect=basket.getBoundingClientRect(),visible=rect.bottom>top&&rect.top<top+height;$('mobile-basket').hidden=!state.cart.length||visible;}
  window.addEventListener('scroll',syncMobileBasket,{passive:true});
  window.addEventListener('resize',syncMobileBasket,{passive:true});
  window.visualViewport?.addEventListener('resize',syncMobileBasket,{passive:true});
  window.visualViewport?.addEventListener('scroll',syncMobileBasket,{passive:true});
  if(globalThis.ResizeObserver){state.basketResizeObserver=new ResizeObserver(syncMobileBasket);state.basketResizeObserver.observe($('basket'));}
  function productFor(line) {
    const current=state.products.find((product)=>product.id===line.productId);if(current)return current;
    const mapping=state.legacyItemMappings[line.productId];if(!mapping)return undefined;
    const parent=state.products.find((product)=>product.id===mapping.product_id);if(!parent)return undefined;
    // Unrecognized old selections remain visible and blocked, never silently discarded or repriced as a different type.
    const value=groups(parent).find((group)=>group.code==='TYPE')?.values?.find((entry)=>entry.code===mapping.options.TYPE);
    return {...parent,id:line.productId,name:mapping.original_name,price_halalas:parent.price_halalas+Number(value?.price_delta_halalas||0),
      enabled:false,source_verified:false,unified_khaliya:false,option_groups:[],unavailable_reason:'راجع نوع الخلية المحفوظ وأعد اختياره من البطاقة الموحدة.'};
  }
  function linePrice(line) { const product = productFor(line); const extra = groups(product || {}).reduce((sum,g) => sum + Number((g.values || []).find((v) => (v.code || v.id) === line.options[g.code])?.price_delta_halalas || 0), 0); return Number(product?.price_halalas || 0) + extra; }
  function draftSubtotal() { return state.cart.reduce((sum, line) => sum + linePrice(line) * line.quantity, 0); }
  function eligibleForProduct(product, target=branch()) { if (product.enabled === false) return {ok:false, reason:'هذا الصنف غير متاح مؤقتًا.'}; if (restricted(product)) { if (!target) return {ok:false, reason:'حدد فرع التنفيذ للتحقق من الإتاحة في حفر الباطن.'}; if (!product.available_cities.some((c) => city(c) === city(target.city))) return {ok:false, reason:'متوفر في حفر الباطن فقط؛ اختر فرعًا مؤهلًا.'}; } if (state.branchId && product.available === false && product.unavailable_reason && !/تحقق|مهيأ|مصدر|سعر/.test(product.unavailable_reason)) return {ok:false, reason:product.unavailable_reason}; return {ok:true, reason:''}; }
  function catalogReady() { return state.cart.length > 0 && state.cart.every((line) => productFor(line)?.source_verified === true); }
  function renderAccount() { const user=state.session.user,authenticated=Boolean(user&&state.session.csrf_token);text('account-button',user?(user.role==='customer'?'حسابي':'حساب التشغيل'):'اطلب كضيف');$('account-other-brand').hidden=!user||user.brand_id===requestedBrandId;$('account-profile').hidden=!authenticated;$('guest-form').hidden=authenticated||!state.channels.guest?.available;$('email-login-button').hidden=authenticated||!state.channels.email?.available;$('email-request-form').hidden=true;$('otp-request-form').hidden=authenticated||!(state.channels.sms?.available||state.channels.whatsapp?.available);$('otp-verify-form').hidden=true;$('otp-unavailable').hidden=true;$('link-email-button').hidden=!authenticated||user.role!=='customer'||user.identity_kind!=='guest'||!state.channels.email?.available;if(user){text('profile-label',user.display_name||'تم تسجيل الدخول');text('identity-label',user.identity_kind==='guest'?'ضيف — رقم الاتصال غير موثق':user.email_verified_at?'البريد موثق · توثيق الجوال مستقل':'حالة التحقق حسب القناة');if(user.brand_id===requestedBrandId){if(!$('customer-name').value)$('customer-name').value=user.display_name||'';if(!$('customer-phone').value)$('customer-phone').value=user.phone||'';}}$('guest-name').value=$('customer-name').value;$('guest-phone').value=$('customer-phone').value; }
  async function establishIdentity(result) {if(!result.user||!result.csrf_token)throw new Error('لم يؤكد الخادم إنشاء جلسة آمنة.');state.session=result;renderAccount();closeDialog('account-dialog');if(result.user.role!=='customer'){location.assign('/ops.html');return;}queueQuote();const callback=state.afterLogin;state.afterLogin=null;if(callback)await callback();}
  async function requestGuest(event){event.preventDefault();await busy($('guest-submit'),async()=>{try{const data={display_name:$('guest-name').value.trim(),phone:$('guest-phone').value.trim(),brand_id:requestedBrandId};if($('guest-email').value.trim())data.email=$('guest-email').value.trim();await establishIdentity(await api('/api/identity/guest',{method:'POST',data}));toast('تم فتح جلسة ضيف آمنة.');}catch(error){showError('account-error',error.message);}});}
  async function requestEmail(event){event.preventDefault();await busy($('email-request-submit'),async()=>{try{await api('/api/identity/email/request',{method:'POST',data:{email:$('login-email').value.trim(),brand_id:requestedBrandId,purpose:state.session.user?.identity_kind==='guest'?'customer_link':'email_login'}});text('email-request-state','إذا كان البريد مؤهلًا، سيصلك رابط آمن صالح لمرة واحدة. قبول الطلب لا يؤكد وصول البريد.');}catch(error){showError('account-error',error.message);}});}
  async function verifyEmailLink(){const fragment=new URLSearchParams(location.hash.slice(1));if(!fragment.has('identity_challenge')||!fragment.has('identity_token'))return;history.replaceState(null,'',location.pathname+location.search);state.emailLink={challenge_id:fragment.get('identity_challenge'),token:fragment.get('identity_token')};try{const result=await api('/api/identity/email/verify',{method:'POST',data:state.emailLink});state.emailLink=null;await establishIdentity(result);toast('تم التحقق من البريد.');}catch(error){showError('account-error',error.message);if(error.code==='STAFF_PROFILE_REQUIRED'){$('guest-form').hidden=true;$('email-login-button').hidden=true;$('identity-onboarding-form').hidden=false;}$('account-title').textContent='التحقق من البريد';openDialog('account-dialog');}}
  function directionsLink(b){if(!b)return '';const raw=b.directions_url;if(typeof raw!=='string')return '';try{const u=new URL(raw);return u.protocol==='https:'&&['www.google.com','google.com','maps.google.com','maps.app.goo.gl'].includes(u.hostname)?u.href:'';}catch{return '';}}
  function showImage(product){const url=safeImage(product?.image_url);if(!url){toast('الصورة غير متاحة حاليًا.');return;}$('image-full').src=url;$('image-full').alt=product.name;text('image-title',product.name);openDialog('image-dialog');}
  function renderBranches() { const select = $('branch-select'); select.replaceChildren(new Option('اختر الفرع', '')); for (const b of state.branches) select.append(new Option(`${b.branch_no} · ${b.name}`, b.id)); select.value = state.branchId; }
  function renderService() { const selected = branch(); text('branch-summary', selected ? `${selected.branch_no} · ${selected.name}` : 'حدد الفرع للتحقق من الإتاحة'); $('location-fields').hidden = state.service !== 'delivery';$('neighborhood').required=state.service==='delivery';$('neighborhood').setAttribute('aria-required',String(state.service==='delivery')); $('car-fields').hidden = state.service !== 'car';const directions=directionsLink(selected);$('branch-directions').hidden=!directions;if(directions)$('branch-directions').href=directions;else $('branch-directions').removeAttribute('href');document.querySelectorAll('input[name=service]').forEach(r=>{const permitted=!selected||!['BRAND_SERVICE_DISABLED','SERVICE_DISABLED','DELIVERY_CITY_NOT_ELIGIBLE'].includes(selected.services?.[r.value]?.code);r.disabled=!permitted;r.closest('label').hidden=!permitted;}); const service = selected?.services?.[state.service]; text('service-notice', selected ? service?.available ? `الخدمة متاحة حسب نطاق الفرع وإعداداته الحالية.${state.service === 'delivery' ? ' سيُتحقق من تغطية موقعك عند مراجعة الطلب.' : ''}` : service?.reason || 'هذه الخدمة لم تُفعّل للفرع بعد.' : 'اختر الفرع. إتاحة كل خدمة تُحدد بصورة مستقلة.'); $('service-notice').classList.toggle('status', service?.available === false); document.querySelectorAll('input[name=service]').forEach((r) => r.checked = r.value === state.service); }
  function renderCategories() { const tabs = $('categories'); tabs.replaceChildren(); const categories = [...new Set(state.products.map((p) => p.category).filter(Boolean))]; for (const category of ['', ...categories]) { const button = node('button','category-tab',category || 'كل المنيو'); button.type='button'; button.setAttribute('aria-current', String(state.category === category)); button.addEventListener('click', () => { state.category = category; renderCategories(); renderProducts(); }); tabs.append(button); } }
  function renderProducts() { const search = $('search').value.trim().toLocaleLowerCase('ar'); const products = state.products.filter((p) => (!state.category || p.category === state.category) && (!search || `${p.name} ${p.category} ${groups(p).flatMap((g)=>(g.values||[]).map((v)=>v.name)).join(' ')}`.toLocaleLowerCase('ar').includes(search))); const container = $('products'); container.replaceChildren(); text('menu-count', `${number.format(state.products.length)} صنفًا${requestedBrandId==='kps'?' · الصور والخيارات من منيو KPS':` · ${state.brand.name}`}`); if (!products.length) { const empty=node('div','empty-state'); empty.append(node('h3','', state.products.length?'لا توجد أصناف مطابقة':'لم تُضف أصناف لهذا البراند بعد.'),node('p','', state.products.length?'جرّب كلمة أخرى أو قسمًا مختلفًا.':'ستظهر هنا الأصناف التي تعتمدها إدارة البراند.')); container.append(empty); return; }
    for (const product of products) { const card=node('article','product-card');card.dataset.productId=product.id; const imageButton=node('button','product-image-button'); imageButton.type='button'; imageButton.setAttribute('aria-label',`تفاصيل ${product.name}`); const imageUrl=safeImage(product.image_url); if (imageUrl) { const img=node('img'); img.src=imageUrl; img.alt=product.name;if(isKhaliya(product))img.classList.add('khaliya-owner-image'); img.loading='lazy'; img.decoding='async'; img.addEventListener('error', () => { img.remove(); imageButton.append(node('span','image-fallback','الصورة غير متاحة حاليًا')); }, {once:true}); imageButton.append(img); } else imageButton.append(node('span','image-fallback','لا توجد صورة للصنف')); imageButton.addEventListener('click',()=>openProduct(product)); const info=node('div','product-content'); const title=node('h3'); const titleButton=node('button','',product.name); titleButton.type='button'; titleButton.addEventListener('click',()=>openProduct(product)); title.append(titleButton); info.append(title); if (restricted(product)) info.append(node('span','pill warning','متوفر في حفر الباطن فقط')); if (groups(product).length) info.append(node('span','pill',isMug(product)?'ألوان لكل كوب':'خيارات لكل وحدة')); const bottom=node('div','product-card-bottom'); bottom.append(node('span','money',money(product.price_halalas))); const add=node('button','product-add-button','＋'); add.type='button'; add.setAttribute('aria-label',`إضافة ${product.name} إلى السلة`); const eligibility=eligibleForProduct(product); add.disabled=!eligibility.ok; add.title=eligibility.reason || 'اختر العدد والخيارات'; add.addEventListener('click',()=>openProduct(product)); bottom.append(add); info.append(bottom); if(imageUrl){const zoom=node('button','image-zoom-button','تكبير الصورة');zoom.type='button';zoom.setAttribute('aria-label',`تكبير صورة ${product.name}`);zoom.addEventListener('click',()=>showImage(product));info.append(zoom);}card.append(imageButton,info); container.append(card); }
  }
  function renderCart() { const container=$('cart-lines'); container.replaceChildren(); const count=state.cart.reduce((sum,line)=>sum+line.quantity,0); text('header-count',number.format(count)); text('basket-count',`${number.format(count)} أصناف`); text('mobile-basket-count',`سلتك · ${number.format(count)} أصناف`); text('mobile-basket-total',money(state.quote?.total_halalas ?? draftSubtotal())); $('basket-details').hidden=!count;syncMobileBasket();
    if (!count) { const empty=node('div','empty-state'); empty.append(node('span','empty-symbol','＋'),node('h3','','ابدأ باختيارك المفضل'),node('p','','الأصناف التي تضيفها ستظهر هنا.')); container.append(empty); }
    for (const line of state.cart) { const product=productFor(line); const row=node('div','cart-line'); const title=node('div','cart-line-title'); title.append(node('span','',cartLineName(product,line.options)),node('span','money',money(linePrice(line)*line.quantity))); row.append(title); const label=optionLabel(product || {},line.options); if(label&&!isKhaliya(product||{})) row.append(node('small','',label)); if(line.restoredName)row.append(node('small','',`صنف محفوظ سابقًا: ${line.restoredName} · النوع والكمية محفوظان`)); if(line.note) row.append(node('small','',`ملاحظة: ${line.note}`)); const eligible=eligibleForProduct(product || {}); if(!eligible.ok) row.append(node('small','status error',eligible.reason)); const controls=node('div','cart-line-controls'); const stepper=node('div','stepper'); const minus=node('button','','−'); minus.type='button'; minus.setAttribute('aria-label',`تقليل كمية ${product?.name}`); minus.addEventListener('click',()=>updateQuantity(line.rowId,-1)); const plus=node('button','','＋'); plus.type='button'; plus.disabled=line.quantity>=30; plus.setAttribute('aria-label',`زيادة كمية ${product?.name}`); plus.addEventListener('click',()=>updateQuantity(line.rowId,1)); stepper.append(minus,node('span','',number.format(line.quantity)),plus); const remove=node('button','remove-line','حذف'); remove.type='button'; remove.setAttribute('aria-label',`حذف ${product?.name}${label?' '+label:''}`); remove.addEventListener('click',()=>{state.cart=state.cart.filter((item)=>item.rowId!==line.rowId); cartChanged();}); controls.append(stepper,remove); row.append(controls); container.append(row); }
    renderQuote();
  }
  function renderQuote() { const summary=$('quote-summary'); summary.replaceChildren(); const row=(label,amount,cls='')=>{const entry=node('div',`quote-row ${cls}`); entry.append(node('span','',label),node('span','money',money(amount))); summary.append(entry);}; row('المنتجات',state.quote?.subtotal_halalas ?? draftSubtotal()); if(state.quote) {row('رسوم التوصيل',state.quote.delivery_fee_halalas || 0); if(Number.isInteger(state.quote.discount_halalas)&&state.quote.discount_halalas>0) row('الخصم',-state.quote.discount_halalas); row('المبلغ المستحق',state.quote.total_halalas,'grand');} else {row('إجمالي المنتجات المبدئي',draftSubtotal(),'grand');summary.append(node('p','small muted',state.quotePending?'جاري التحقق من الإتاحة والمبلغ…':'الرسوم والمبلغ النهائي يظهران بعد تحقق الفرع والخدمة.'));} showError('basket-error',state.quoteError); $('checkout-button').disabled=!state.cart.length||state.quotePending||!state.quote||!catalogReady(); text('checkout-button',state.quotePending?'جاري التحقق…':!state.cart.length?'أضف أصنافًا للسلة':!catalogReady()?'راجع الأصناف غير المعتمدة في السلة':'مراجعة وتأكيد الطلب'); }
  function updateQuantity(id,delta) { const row=state.cart.find((line)=>line.rowId===id); if(!row) return; if(row.quantity+delta<1) state.cart=state.cart.filter((line)=>line.rowId!==id); else row.quantity=Math.min(30,row.quantity+delta); cartChanged(); }
  function cartChanged() { state.quote=null; persistDraft(); renderCart(); queueQuote(); }
  function orderBody(cart=state.cart,branchId=state.branchId) { const customer={name:$('customer-name').value.trim() || state.session.user?.display_name || ''};const contactPhone=$('customer-phone').value.trim()||state.session.user?.phone;if(contactPhone)customer.phone=contactPhone; if(state.service==='delivery') {if(state.location) customer.location=state.location; customer.address=$('address').value.trim();customer.neighborhood=$('neighborhood').value.trim();} if(state.service==='car') customer.car={model:$('car-model').value.trim(),color:$('car-color').value.trim(),plate:$('car-plate').value.trim()}; return {brand_id:state.brand.id||'kps',branch_id:branchId,service:state.service,payment_method:document.querySelector('input[name=payment]:checked')?.value||'cash',items:cart.map((line)=>({product_id:line.productId,quantity:line.quantity,...(groups(productFor(line)||{}).length?{units:Array.from({length:line.quantity},()=>({options:line.options}))}:{}),note:line.note||''})),customer,notes:$('order-notes').value.trim()}; }
  function queueQuote() { clearTimeout(queueQuote.timer); state.quote=null; state.quoteError=''; state.quotePending=false; state.quoteRevision++; renderQuote(); if(!state.cart.length) return; if(!$('customer-name').value.trim()&&!state.session.user?.display_name) {state.quoteError='أدخل اسم المستلم لمراجعة الطلب.';renderQuote();return;} if(!state.branchId) {state.quoteError='حدد الفرع للتحقق من طلبك.';renderQuote();return;} if(state.service==='delivery'&&!state.location) {state.quoteError='حدد موقع التوصيل للتحقق من التغطية والرسوم.';renderQuote();return;} if(state.service==='delivery'&&!$('address').value.trim()){state.quoteError='أدخل وصف عنوان التوصيل.';renderQuote();return;} if(state.service==='delivery'&&!validNeighborhood($('neighborhood').value)){state.quoteError='أدخل اسم حي التوصيل بصورة واضحة.';renderQuote();return;} if(state.service==='car'&&(!$('car-plate').value.trim()||!$('car-color').value.trim())){state.quoteError='أدخل لون السيارة ورقم اللوحة.';renderQuote();return;} queueQuote.timer=setTimeout(()=>refreshQuote(),350); }
  async function refreshQuote() { const revision=++state.quoteRevision; state.quotePending=true; state.quoteError=''; renderQuote(); try {const payload=orderBody();delete payload.notes;const result=await api('/api/quote',{method:'POST',data:payload}); if(revision!==state.quoteRevision) return; state.quote=result.quote; if(!state.quote || !Number.isInteger(state.quote.total_halalas)) throw new Error('تعذر التحقق من المبلغ النهائي. حاول مرة أخرى.');} catch(error) {if(revision!==state.quoteRevision)return;state.quote=null;state.quoteError=error.message;} finally {if(revision===state.quoteRevision){state.quotePending=false;renderQuote();}} }
  function openProduct(product) { state.product=product; state.optionValues=[{}]; $('product-qty').value='1'; text('product-title',product.name); text('product-price',money(product.price_halalas)); const image=safeImage(product.image_url); $('product-image').hidden=!image;$('product-image-zoom').hidden=!image; if(image){$('product-image').src=image;$('product-image').alt=product.name;$('product-image').classList.toggle('khaliya-owner-image',isKhaliya(product));} $('product-description').hidden=!product.description; text('product-description',product.description||''); $('product-restriction').hidden=!restricted(product); const eligibility=eligibleForProduct(product); $('product-add').disabled=!eligibility.ok; showError('product-availability',eligibility.reason); text('product-add','إضافة للسلة'); $('product-notes').value=''; showError('product-error',''); renderOptions(); openDialog('product-dialog'); }
  function renderOptions() { const product=state.product; if(!product)return; const quantity=Math.max(1,Math.min(30,Math.floor(Number($('product-qty').value)||1))); $('product-qty').value=String(quantity); while(state.optionValues.length<quantity)state.optionValues.push({}); state.optionValues.length=quantity; const optionGroups=groups(product); const container=$('product-options');container.replaceChildren(); $('product-option-hint').hidden=!optionGroups.length; text('product-option-hint',isMug(product)?'اختر العدد ثم حدد اللون لكل كوب قبل الإضافة.':isKhaliya(product)?'اختر النوع لكل وحدة: كلاسيك أو بيكان.':optionGroups.length?'اختر العدد ثم حدد النكهة لكل وحدة قبل الإضافة.':''); $('apply-first-option').hidden=optionGroups.length===0||quantity<2; text('apply-first-option',isMug(product)?'تطبيق لون الكوب الأول على الكل':isKhaliya(product)?'تطبيق نوع الوحدة الأولى على الكل':'تطبيق نكهة الوحدة الأولى على الكل'); for(let index=0;index<quantity&&optionGroups.length;index++){const unit=node('div','option-unit');unit.append(node('h3','',`${isMug(product)?'الكوب':'الوحدة'} ${number.format(index+1)}`));for(const group of optionGroups){const label=node('label','field',group.name);const select=node('select');select.setAttribute('aria-label',`${group.name} ${number.format(index+1)}`); select.append(new Option(isKhaliya(product)?'اختر النوع':`اختر ${isMug(product)?'اللون':group.name}`,''));for(const value of group.values||[])select.append(new Option(value.name,value.code||value.id));select.value=state.optionValues[index][group.code]||'';select.required=group.required!==false;select.addEventListener('change',()=>{state.optionValues[index][group.code]=select.value;showError('product-error','');});label.append(select);unit.append(label);}container.append(unit);} $('product-minus').disabled=quantity<=1;$('product-plus').disabled=quantity>=30; }
  async function addProduct() { const product=state.product;const eligibility=eligibleForProduct(product);if(!eligibility.ok){showError('product-error',eligibility.reason);return;}const quantity=Number($('product-qty').value);if(state.cart.reduce((sum,line)=>sum+line.quantity,0)+quantity>100){showError('product-error','الحد الأقصى مئة وحدة في السلة.');return;}if(state.branchId){try{const result=await api(`/api/products/${encodeURIComponent(product.id)}/availability?branch_id=${encodeURIComponent(state.branchId)}`);if(result.available!==true){showError('product-error',result.reason||'الصنف غير متاح في هذا الفرع.');return;}}catch(error){showError('product-error',error.message);return;}}const optionGroups=groups(product);for(let index=0;index<quantity;index++)for(const group of optionGroups){const selected=state.optionValues[index]?.[group.code];if(group.required!==false&&!(group.values||[]).some((v)=>(v.code||v.id)===selected)){showError('product-error',`${isMug(product)?'حدد اللون للكوب':isKhaliya(product)?'حدد النوع للوحدة':'حدد النكهة للوحدة'} ${number.format(index+1)} قبل الإضافة.`);$('product-options').querySelectorAll('.option-unit')[index]?.querySelector('select')?.focus();return;}}const note=$('product-notes').value.trim();const proposed=state.cart.map((line)=>({...line}));for(let index=0;index<quantity;index++){const options=optionGroups.length?{...state.optionValues[index]}:{};const signature=JSON.stringify(options);const current=proposed.find((line)=>line.productId===product.id&&JSON.stringify(line.options)===signature&&line.note===note);if(current&&current.quantity<30)current.quantity++;else proposed.push({productId:product.id,quantity:1,options,note,rowId:key()});} if(catalogReady()&&state.branchId&&($('customer-name').value.trim()||state.session.user?.display_name)&&(state.service!=='delivery'||state.location&&$('address').value.trim()&&validNeighborhood($('neighborhood').value))&&(state.service!=='car'||$('car-color').value.trim()&&$('car-plate').value.trim())){try{const payload=orderBody(proposed);delete payload.notes;await api('/api/quote',{method:'POST',data:payload});}catch(error){showError('product-error',error.message);return;}}state.cart=proposed;cartChanged();closeDialog('product-dialog');toast(`أضيف ${product.name} إلى السلة`); }
  function confirmAction({title, content, label, action, cancel}) { state.confirmation={action,cancel}; text('confirm-title',title);$('confirm-content').replaceChildren(content);showError('confirm-error','');text('confirm-submit',label);$('confirm-submit').disabled=false;openDialog('confirm-dialog'); }
  function changeBranch(newId) { const previous=state.branchId;const target=state.branches.find((b)=>b.id===newId);const invalid=state.cart.filter((line)=>{const p=productFor(line);return restricted(p||{})&&(!target||!p.available_cities.some((c)=>city(c)===city(target.city)));});if(invalid.length){const content=node('div');content.append(node('p','','تغيير الفرع سيجعل هذه الأصناف غير متاحة. لن نحذفها دون موافقتك:'));const list=node('ul');invalid.forEach((line)=>list.append(node('li','',`${productFor(line)?.name} × ${number.format(line.quantity)}`)));content.append(list,node('p','small muted','يمكنك إغلاق هذه النافذة للاحتفاظ بالفرع والسلة الحاليين.'));$('branch-select').value=previous;confirmAction({title:'مراجعة تغيير الفرع',content,label:'حذف الأصناف غير المتاحة وتغيير الفرع',action:async()=>{const ids=new Set(invalid.map((i)=>i.rowId));state.cart=state.cart.filter((line)=>!ids.has(line.rowId));state.branchId=newId;$('branch-select').value=newId;renderService();renderProducts();cartChanged();loadBranchCatalog();loadBanners();closeDialog('confirm-dialog');}});return;}state.branchId=newId;renderService();renderProducts();queueQuote();loadBranchCatalog();loadBanners(); }
  async function beginCheckout() { if(state.session.user&&state.session.user.brand_id!==requestedBrandId){requireCustomer(beginCheckout);return;}if(!catalogReady()){showError('basket-error','راجع الصنف الذي تغير سعره أو خياراته في السلة قبل تأكيد الطلب.');return;}if(!state.session.user){state.afterLogin=beginCheckout;renderAccount();showError('account-error','');openDialog('account-dialog');return;}if(state.session.user.role!=='customer'){showError('basket-error','يلزم دخول عميل أو جلسة ضيف لتأكيد الطلب. حساب فريق التشغيل مخصص للتشغيل.');return;}if(!state.session.csrf_token){state.afterLogin=beginCheckout;showError('account-error','يلزم تجديد الدخول للتحقق الآمن.');openDialog('account-dialog');return;}await refreshQuote();if(!state.quote)return;const payload=orderBody();const quoted=state.quote;const content=node('div');content.append(node('h3','',branch()?.name||''),node('p','muted',`${serviceName(state.service)} · ${payload.payment_method==='cash'?'كاش':'شبكة عند الاستلام'}`));const list=node('ul','order-items');state.cart.forEach((line)=>{const p=productFor(line);const li=node('li','',`${cartLineName(p,line.options)} × ${number.format(line.quantity)}${optionLabel(p||{},line.options)?' · '+optionLabel(p||{},line.options):''}`);list.append(li);});content.append(list);if(state.service==='delivery')content.append(node('p','small',payload.customer.address||'الموقع المحدد للتوصيل'));if(state.service==='car')content.append(node('p','small',`${payload.customer.car.model} · ${payload.customer.car.color} · ${payload.customer.car.plate}`));const total=node('div','quote-row grand');total.append(node('strong','','المبلغ المستحق'),node('strong','money',money(quoted.total_halalas)));content.append(total,node('p','small muted','سيُحفظ طلب واحد بعد تأكيد الخادم. التحصيل يتم عند الاستلام.'));payload.expected_total_halalas=quoted.total_halalas;const idem=stableKey('create-order',payload);confirmAction({title:'مراجعة الطلب',content,label:'تأكيد الطلب',action:async()=>{const result=await api('/api/orders',{method:'POST',data:payload,idempotency:idem.value});if(!result.order?.id)throw new Error('لم يؤكد الخادم حفظ الطلب. راجع طلباتي قبل إعادة المحاولة.');state.cart=[];state.quote=null;persistDraft();renderCart();releaseKey(idem);closeDialog('confirm-dialog');toast('تم حفظ طلبك. يمكنك متابعة حالته ومحادثته.');await openOrders(result.order.id);}}); }
  async function requestOTP(event) { event.preventDefault();showError('account-error','');const payload={phone:$('phone').value.trim(),brand_id:state.brand.id||'kps'};await busy($('send-otp'),async()=>{try{const result=await api('/api/auth/otp/request',{method:'POST',data:payload});if(!result.challenge_id)throw new Error('لم يؤكد مزود التحقق إرسال الرمز.');state.challenge=result.challenge_id;$('otp-request-form').hidden=true;$('otp-verify-form').hidden=false;$('otp-unavailable').hidden=true;const masked=payload.phone.slice(-4);text('otp-info',`أُرسل رمز التحقق إلى الرقم المنتهي بـ${masked}${result.channel==='whatsapp'?' عبر واتساب':' عبر الرسائل النصية'}.`);$('otp-code').value='';$('otp-code').focus();}catch(error){showError('account-error',error.message);$('otp-unavailable').hidden=error.code!=='OTP_PROVIDER_UNAVAILABLE';}}); }
  async function verifyOTP(event) { event.preventDefault();showError('account-error','');await busy($('verify-otp'),async()=>{try{const result=await api('/api/auth/otp/verify',{method:'POST',data:{challenge_id:state.challenge,code:$('otp-code').value.trim(),display_name:$('customer-name').value.trim()}});if(!result.user||!result.csrf_token)throw new Error('لم تُنشأ جلسة دخول آمنة. حاول مجددًا.');state.session=result;state.challenge=null;renderAccount();closeDialog('account-dialog');toast('تم التحقق من جوالك');const callback=state.afterLogin;state.afterLogin=null;if(callback)await callback();}catch(error){showError('account-error',error.message);}}); }
  async function openOrders(orderId) { if(state.session.user&&state.session.user.brand_id!==requestedBrandId){requireCustomer(()=>openOrders(orderId));return;}if(!state.session.user){state.afterLogin=()=>openOrders(orderId);renderAccount();showError('account-error','');openDialog('account-dialog');return;}if(state.session.user.role!=='customer'){toast('طلبات العملاء متاحة لحساب العميل. لوحة فريق التشغيل من رابط أسفل الصفحة.');return;}openDialog('orders-dialog');showError('orders-error','');$('order-detail').hidden=true;$('orders-list').hidden=false;$('orders-list').replaceChildren(node('div','empty-state','جاري تحميل طلباتك…'));try{const result=await api('/api/orders?limit=30');state.orders=result.orders||[];state.nextOrdersOffset=result.next_offset;renderOrders(state.orders);scheduleOrdersPoll();if(orderId)await showOrder(orderId);}catch(error){showError('orders-error',error.message);$('orders-list').replaceChildren();} }
  function orderBranchLabel(order) { const saved=order.snapshot||{};const current=state.branches.find((b)=>b.id===order.branch_id);const no=saved.branch_no||order.branch_no||current?.branch_no;return `${no?`${no} · `:''}${saved.branch_name||order.branch_name||current?.name||'فرع التنفيذ'}`; }
  function conversationOrderLabel(conversation) { const order=state.orders.find((o)=>o.id===conversation.order_id)||state.contactOrders.find((o)=>o.id===conversation.order_id);return conversation.order_no||order?.order_no||order?.number||conversation.order_id; }
  function renderOrders(orders) { const container=$('orders-list');container.replaceChildren();if(!orders.length){container.append(node('div','empty-state','لا توجد طلبات محفوظة في حسابك حتى الآن.'));return;}for(const order of orders){const card=node('article','order-card');card.append(node('h3','',`طلب ${order.number||order.order_no||order.id}`));const summary=node('div','row wrap');summary.append(node('span','pill',serviceName(order.service)),node('span','pill',statusName(order.status||order.preparation_status)),node('strong','money',money(order.total_halalas||order.snapshot?.total_halalas)));card.append(summary,node('p','small',orderBranchLabel(order)),node('p','small muted',dateTime(order.created_at)));const button=node('button','button secondary','عرض الطلب ومحادثته');button.type='button';button.addEventListener('click',()=>showOrder(order.id));card.append(button);container.append(card);} if(state.nextOrdersOffset!=null){const more=node('button','button secondary full','تحميل طلبات أقدم');more.type='button';more.addEventListener('click',()=>busy(more,async()=>{try{const result=await api(`/api/orders?limit=30&offset=${state.nextOrdersOffset}`);state.orders.push(...(result.orders||[]).filter((o)=>!state.orders.some((existing)=>existing.id===o.id)));state.nextOrdersOffset=result.next_offset;renderOrders(state.orders);}catch(error){showError('orders-error',error.message);}}));container.append(more);} }
  function scheduleOrdersPoll(){clearTimeout(state.ordersTimer);if(!$('orders-dialog').open)return;state.ordersTimer=setTimeout(async()=>{if(!$('orders-dialog').open)return;try{if(!document.hidden){const scroll=$('orders-dialog').querySelector('.dialog-scroll').scrollTop;if(state.activeOrder)await showOrder(state.activeOrder.id,true);else{const result=await api('/api/orders?limit=30');const recent=result.orders||[];const older=state.orders.filter((order)=>!recent.some((r)=>r.id===order.id));state.orders=[...recent,...older];renderOrders(state.orders);}$('orders-dialog').querySelector('.dialog-scroll').scrollTop=scroll;}}catch(error){showError('orders-error',error.message);}finally{scheduleOrdersPoll();}},8000);}
  async function showOrder(id,quiet=false) { showError('orders-error','');try{const result=await api(`/api/orders/${encodeURIComponent(id)}`);state.activeOrder=result.order;renderOrderDetail(result.order,result.events||[]);if(!quiet){$('orders-list').hidden=true;$('order-detail').hidden=false;}}catch(error){showError('orders-error',error.message);} }
  function renderOrderDetail(order,events) { if(!order)return;const container=$('order-detail');container.replaceChildren();const back=node('button','button quiet','العودة إلى طلباتي');back.type='button';back.addEventListener('click',()=>{state.activeOrder=null;openOrders();});container.append(back,node('h3','',`طلب ${order.number||order.order_no||order.id}`),node('p','small muted',dateTime(order.created_at)));const statuses=node('div','order-status-grid');[['التجهيز',order.preparation_status],['التوصيل',order.delivery_status||order.fulfillment_status],['الإسناد',order.assignment_status],['الدفع',order.payment_status]].forEach(([label,value])=>{const status=node('div');status.append(node('span','',label),node('b','',statusName(value)));statuses.append(status);});container.append(statuses);const snapshot=order.snapshot||{};const orderBranch=state.branches.find((b)=>b.id===order.branch_id);container.append(node('p','small',`${orderBranchLabel(order)} · ${serviceName(order.service)}`));const list=node('ul','order-items');for(const item of order.items||snapshot.items||[]){const line=node('li');line.append(node('strong','',`${item.name||item.product_name||'صنف'} × ${number.format(item.quantity||item.qty||1)}`));if(item.note)line.append(node('p','small muted',item.note));for(const unit of item.units||[]){const label=unit.option_label||unit.options_label||Object.values(unit.option_snapshot||{}).map((g)=>`${g.name}: ${(g.choices||[]).map((v)=>v.name).join('، ')}`).join(' · ');if(label)line.append(node('p','small muted',label));}list.append(line);}container.append(list);if(snapshot.notes)container.append(node('p','small muted',`ملاحظات الطلب: ${snapshot.notes}`));const total=node('div','quote-row grand');total.append(node('strong','','مبلغ الطلب المحفوظ'),node('strong','money',money(order.total_halalas??snapshot.total_halalas)));container.append(total);const actions=node('div','row wrap');const chat=node('button','button primary','محادثة الطلب');chat.type='button';chat.addEventListener('click',()=>openChat(order));actions.append(chat);if(order.service==='car'&&!['completed','delivered','cancelled','rejected'].includes(order.status||order.preparation_status)&&!order.arrival_at){const arrived=node('button','button secondary','وصلت إلى الفرع');arrived.type='button';arrived.addEventListener('click',()=>busy(arrived,async()=>{try{const payload={version:order.version};const idem=stableKey(`arrival-${order.id}`,payload);await api(`/api/orders/${encodeURIComponent(order.id)}/arrival`,{method:'POST',data:payload,idempotency:idem.value});releaseKey(idem);toast('تم حفظ إشعار وصولك للفرع.');await showOrder(order.id,true);}catch(error){showError('orders-error',error.message);}}));actions.append(arrived);}container.append(actions);if(events.length){const details=node('details');details.append(node('summary','','سجل حالة الطلب'));const history=node('ul','order-items');events.forEach((event)=>history.append(node('li','small',`${dateTime(event.created_at)} · ${event.message||event.label||statusName(event.status||String(event.event_type||'').split('.').at(-1))}`)));details.append(history);container.append(details);} }
  function requireCustomer(callback) {
    if(!state.session.user||!state.session.csrf_token){state.afterLogin=callback;renderAccount();showError('account-error','');openDialog('account-dialog');return false;}
    if(state.session.user.brand_id!==requestedBrandId){state.afterLogin=callback;renderAccount();showError('account-error','الدخول الحالي خاص ببراند آخر. سجل الخروج للدخول إلى هذا البراند.');openDialog('account-dialog');return false;}
    if(state.session.user.role!=='customer'){toast('التواصل هنا متاح لجلسة الضيف أو حساب العميل. دخول فريق التشغيل من الرابط أسفل الصفحة.');return false;}
    return true;
  }
  async function openContacts() {
    if(!requireCustomer(openContacts))return;
    closeDialog('chat-dialog');closeDialog('orders-dialog');openDialog('contacts-dialog');showError('contacts-error','');
    $('contacts-list').replaceChildren(node('div','empty-state','جاري تحميل محادثاتك…'));
    const select=$('contact-branch');select.replaceChildren(new Option('اختر الفرع',''));state.branches.forEach((b)=>select.append(new Option(`${b.branch_no} · ${b.name}`,b.id)));select.value=state.branchId;
    $('contact-service').value=state.service;$('contact-order').replaceChildren(new Option('محادثة دون طلب',''));updateContactContext();
    const results=await Promise.allSettled([api('/api/conversations'),api('/api/orders?limit=30')]);
    if(!$('contacts-dialog').open)return;
    if(results[0].status==='fulfilled'){state.conversations=results[0].value.conversations||[];state.nextConversationOffset=state.conversations.length===50?50:null;renderContacts();}
    else{$('contacts-list').replaceChildren(node('p','small muted','تعذر تحميل المحادثات المحفوظة.'));showError('contacts-error',results[0].reason.message);}
    if(results[1].status==='fulfilled'){state.contactOrders=results[1].value.orders||[];state.contactOrders.forEach((order)=>$('contact-order').append(new Option(`طلب ${order.number||order.order_no||order.id} · ${serviceName(order.service)}`,order.id)));}
    else{state.contactOrders=[];showError('contacts-error',results[1].reason.message);}
  }
  function renderContacts() {
    const list=$('contacts-list');list.replaceChildren();
    if(!state.conversations.length)list.append(node('p','small muted','لا توجد محادثات محفوظة في حسابك حتى الآن.'));
    state.conversations.forEach((conversation)=>{const card=node('article','order-card');const label=conversation.order_id?`محادثة طلب ${conversationOrderLabel(conversation)}`:'محادثة دون طلب';card.append(node('h3','',label),node('p','small muted',`${conversation.branch_name||state.branches.find((b)=>b.id===conversation.branch_id)?.name||'الفرع المختص'} · ${serviceName(conversation.service||conversation.context_service||'')}`));const summary=node('div','row wrap');summary.append(node('span','pill',({open:'مفتوحة',in_progress:'قيد المعالجة',resolved:'تمت المعالجة'}[conversation.status]||'مفتوحة')));if(conversation.unread_count>0)summary.append(node('span','pill warning',`${number.format(conversation.unread_count)} رسائل جديدة`));card.append(summary);const button=node('button','button secondary','فتح المحادثة');button.type='button';button.dataset.conversationId=conversation.id;button.addEventListener('click',()=>showConversation(conversation));card.append(button);list.append(card);});
    if(state.nextConversationOffset!==null){const more=node('button','button secondary full','تحميل محادثات أقدم');more.type='button';more.addEventListener('click',()=>busy(more,async()=>{try{const result=await api(`/api/conversations?offset=${state.nextConversationOffset}`);const rows=result.conversations||[];state.conversations.push(...rows.filter((row)=>!state.conversations.some((entry)=>entry.id===row.id)));state.nextConversationOffset=rows.length===50?state.nextConversationOffset+50:null;renderContacts();}catch(error){showError('contacts-error',error.message);}}));list.append(more);}
  }
  function updateContactContext() { const linked=Boolean($('contact-order').value);$('contact-context-fields').hidden=linked;$('contact-branch').required=!linked;$('contact-service').required=!linked; }
  async function startContact(event) {
    event.preventDefault();if(!requireCustomer(openContacts))return;
    const ownOrder=state.contactOrders.find((order)=>order.id===$('contact-order').value);
    const payload=ownOrder?{order_id:ownOrder.id}:{branch_id:$('contact-branch').value,service:$('contact-service').value};
    if(!ownOrder&&!payload.branch_id){showError('contacts-error','حدد الفرع ونوع الخدمة لبدء المحادثة.');return;}
    await busy($('contact-start'),async()=>{try{const idem=stableKey('start-conversation',payload);const result=await api('/api/conversations',{method:'POST',data:payload,idempotency:idem.value});const conversation=result.conversation||result;if(!conversation.id)throw new Error('لم يؤكد الخادم فتح المحادثة.');releaseKey(idem);await showConversation({...conversation,order_no:ownOrder?.order_no||ownOrder?.number,service:ownOrder?.service||payload.service});}catch(error){showError('contacts-error',error.message);}});
  }
  async function showConversation(conversation) {
    if(!requireCustomer(openContacts))return;
    clearTimeout(state.chatTimer);state.conversation=conversation;state.chatSequence=0;
    text('chat-title',conversation.order_id?`محادثة طلب ${conversationOrderLabel(conversation)}`:'محادثة دون طلب');text('chat-route',`${conversation.route==='delivery'?'مدير وفريق التوصيل':'فريق الفرع المختص'} · ${serviceName(conversation.service||conversation.context_service||'')}`);
    $('chat-messages').replaceChildren();$('chat-text').value='';$('chat-image').value='';text('chat-image-name','');showError('chat-error','');
    closeDialog('orders-dialog');closeDialog('contacts-dialog');openDialog('chat-dialog');await pollChat();
  }
  async function openChat(order) {
    if(!requireCustomer(()=>openChat(order)))return;
    const payload={order_id:order.id};const idem=stableKey('start-conversation',payload);
    try{const result=await api('/api/conversations',{method:'POST',data:payload,idempotency:idem.value});const conversation=result.conversation||result;if(!conversation.id)throw new Error('تعذر فتح محادثة الطلب.');releaseKey(idem);await showConversation({...conversation,order_no:order.order_no||order.number,service:order.service});}
    catch(error){showError('orders-error',error.message);}
  }
  async function pollChat() { if(!$('chat-dialog').open||!state.conversation?.id)return;const id=state.conversation.id;try{const result=await api(`/api/conversations/${encodeURIComponent(id)}/messages?after=${state.chatSequence}`);if(state.conversation?.id!==id)return;const container=$('chat-messages');const nearBottom=container.scrollHeight-container.scrollTop-container.clientHeight<80;if((result.messages||[]).length)container.querySelector('.chat-empty')?.remove();for(const message of result.messages||[]){if(message.seq<=state.chatSequence)continue;const bubble=node('div',`chat-bubble${message.sender_id===state.session.user?.id?' mine':''}`);if(message.text)bubble.append(node('p','',message.text));if(message.attachment_id){const url=safeImage(message.attachment_url||message.attachment?.url||`/api/chat-attachments/${encodeURIComponent(message.attachment_id)}`);if(url){const img=node('img');img.src=url;img.alt='صورة مرفقة في المحادثة';img.loading='lazy';const link=node('a');link.href=url;link.target='_blank';link.rel='noopener';link.append(img);bubble.append(link);}}bubble.append(node('small','',dateTime(message.created_at)));container.append(bubble);state.chatSequence=Math.max(state.chatSequence,message.seq||0);}if(!container.children.length)container.append(node('p','small muted chat-empty','ابدأ رسالتك هنا. تبقى المحادثة محفوظة في حسابك.'));if(nearBottom)container.scrollTop=container.scrollHeight;if(state.chatSequence>0)await api(`/api/conversations/${encodeURIComponent(id)}/read`,{method:'POST',data:{through_seq:state.chatSequence}});showError('chat-error','');}catch(error){showError('chat-error',error.message);}finally{clearTimeout(state.chatTimer);if($('chat-dialog').open&&state.conversation?.id===id)state.chatTimer=setTimeout(pollChat,5000);} }
  const readFile = (file) => new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('تعذرت قراءة الصورة.'));reader.readAsDataURL(file);});
  async function sendMessage(event) {event.preventDefault();if(!state.conversation?.id){showError('chat-error','انتظر فتح المحادثة قبل الإرسال.');return;}const message=$('chat-text').value.trim();const file=$('chat-image').files[0];if(!message&&!file){showError('chat-error','أضف نصًا أو صورة قبل الإرسال.');return;}if(file&&(!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size>5*1024*1024)){showError('chat-error','اختر صورة JPG أو PNG أو WebP بحجم لا يتجاوز 5 ميجابايت.');return;}await busy($('send-message'),async()=>{try{let attachmentId=null;if(file){const upload={data_base64:await readFile(file),content_type:file.type};const uploadKey=stableKey(`chat-upload-${state.conversation.id}`,{name:file.name,size:file.size,lastModified:file.lastModified});const uploaded=await api(`/api/conversations/${encodeURIComponent(state.conversation.id)}/attachments`,{method:'POST',data:upload,idempotency:uploadKey.value});attachmentId=uploaded.id||uploaded.attachment?.id;if(!attachmentId)throw new Error('لم يؤكد الخادم حفظ الصورة.');}const payload={text:message,attachment_id:attachmentId};const idem=stableKey(`chat-message-${state.conversation.id}`,payload);await api(`/api/conversations/${encodeURIComponent(state.conversation.id)}/messages`,{method:'POST',data:payload,idempotency:idem.value});releaseKey(idem);$('chat-text').value='';$('chat-image').value='';text('chat-image-name','');showError('chat-error','');await pollChat();}catch(error){showError('chat-error',error.message);}});}
  async function loadBranchCatalog() {
    const selected=state.branchId;
    const params=new URLSearchParams({brand_id:state.brand.id||'kps'});
    if(selected)params.set('branch_id',selected);
    try {const result=await api(`/api/catalog?${params}`);if(state.branchId!==selected)return;state.products=result.products||[];state.sourceVerified=result.source_verified===true;renderProducts();renderCart();queueQuote();}
    catch(error){if(state.branchId===selected){state.quote=null;state.quoteError=error.message;renderQuote();}}
  }

  const prefersLessMotion=()=>window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function renderTicker() {
    const area=$('announcement-ticker'),track=$('announcement-track'),control=$('ticker-pause');
    const config=state.presentation||{};
    const phrases=Array.isArray(config.ticker_lines)?config.ticker_lines.filter(x=>typeof x==='string'&&x.trim()).slice(0,8):[];
    area.hidden=!config.ticker_enabled||!phrases.length;
    track.replaceChildren();
    if(area.hidden)return;
    for(let repeat=0;repeat<2;repeat++){
      const part=node('span','ticker-cycle');
      if(repeat)part.setAttribute('aria-hidden','true');
      for(const phrase of phrases){
        part.append(node('span','ticker-text',phrase));
        const point=node('span','ticker-separator','•');
        point.setAttribute('aria-hidden','true');
        part.append(point);
      }
      track.append(part);
    }
    const seconds=Number(config.ticker_seconds);
    track.style.setProperty('--ticker-duration',`${Number.isFinite(seconds)?Math.min(90,Math.max(14,seconds)):28}s`);
    track.classList.toggle('is-paused',state.tickerPaused||prefersLessMotion());
    control.textContent=state.tickerPaused?'تشغيل الحركة':'إيقاف الحركة';
    control.setAttribute('aria-pressed',String(state.tickerPaused));
    control.disabled=prefersLessMotion();
    control.title=prefersLessMotion()?'الحركة متوقفة حسب إعداد الجهاز':'';
  }
  function stopBannerRotation() {
    if(state.bannerTimer!==null)clearInterval(state.bannerTimer);
    state.bannerTimer=null;
  }
  function startBannerRotation() {
    stopBannerRotation();
    const area=$('banner-area');
    if(state.banners.length<2||document.hidden||prefersLessMotion()||area.matches(':hover')||area.matches(':focus-within'))return;
    state.bannerTimer=setInterval(()=>{
      if(document.hidden||prefersLessMotion()||area.matches(':hover')||area.matches(':focus-within')){stopBannerRotation();return;}
      state.bannerIndex=(state.bannerIndex+1)%state.banners.length;
      renderBanner();
    },6500);
  }
  function switchBanner(index) {
    if(state.banners.length<2)return;
    state.bannerIndex=(index+state.banners.length)%state.banners.length;
    renderBanner();
    startBannerRotation();
  }
  async function loadBanners() {
    const selected=state.branchId;
    stopBannerRotation();
    try {
      const params=new URLSearchParams({brand_id:state.brand.id||'kps'});
      if(selected)params.set('branch_id',selected);
      const result=await api(`/api/banners?${params}`);
      if(state.branchId!==selected)return;
      state.banners=result.banners||[];
      state.bannerIndex=0;
      state.presentation=result.presentation||null;
      renderTicker();
      renderBanner();
      startBannerRotation();
    } catch {
      if(state.branchId===selected){
        state.banners=[];
        state.bannerIndex=0;
        state.presentation=null;
        renderTicker();
        renderBanner();
      }
    }
  }
  function renderBanner() {
    const banner=state.banners[state.bannerIndex];
    const container=$('banner-content'),controls=$('banner-controls');
    controls.replaceChildren();
    controls.hidden=state.banners.length<2;
    container.replaceChildren();
    container.setAttribute('aria-live','off');
    if(!banner){
      container.className='intro-banner';
      const content=node('div');
      content.append(
        node('span','eyebrow',state.brand.name),
        node('h1','',`اطلب مباشرة من ${state.brand.name}`),
        node('p','','تصفح المنيو، ثم حدد الخدمة والفرع المناسب لطلبك.')
      );
      // The formerly oversized KPS watermark is removed; the owner may
      // enable an approved literary phrase in the future without redeploying.
      if(state.presentation?.quote_text)content.append(node('p','hero-quote',state.presentation.quote_text));
      container.append(content);
      return;
    }
    container.className='campaign-banner';
    const image=safeImage(banner.desktop_image_url||banner.image_url);
    const mobileImage=safeImage(banner.mobile_image_url||image);
    if(image){
      const picture=node('picture');
      if(mobileImage){
        const source=node('source');
        source.media='(max-width: 550px)';
        source.srcset=mobileImage;
        picture.append(source);
      }
      const img=node('img');
      img.src=image;
      img.alt=banner.title||`إعلان ${state.brand.name}`;
      img.decoding='async';
      img.addEventListener('error',()=>picture.remove(),{once:true});
      picture.append(img);
      container.append(picture);
    }
    const content=node('div','campaign-content'),copy=node('div');
    copy.append(node('h2','',banner.title||state.brand.name));
    if(banner.body||banner.text)copy.append(node('p','',banner.body||banner.text));
    content.append(copy);
    if(banner.destination){
      const action=node('button','button secondary',banner.button_label||'شاهد التفاصيل');
      action.type='button';
      action.addEventListener('click',()=>{
        const target=banner.destination;
        if(target.kind==='product'){
          const targetId=state.legacyItemMappings[target.id]?.product_id||target.id;
          const p=state.products.find((p)=>p.id===targetId);
          if(p)openProduct(p);
        } else if(target.kind==='category'){
          state.category=target.id||'';
          renderCategories();renderProducts();
          $('menu').scrollIntoView({behavior:'smooth'});
        } else if(target.kind==='menu')$('menu').scrollIntoView({behavior:'smooth'});
      });
      content.append(action);
    }
    container.append(content);
    if(state.banners.length>1)for(let index=0;index<state.banners.length;index++){
      const button=node('button','banner-dot',number.format(index+1));
      button.type='button';
      button.setAttribute('aria-label',`الإعلان ${number.format(index+1)} من ${number.format(state.banners.length)}`);
      button.setAttribute('aria-current',String(index===state.bannerIndex));
      button.addEventListener('click',()=>switchBanner(index));
      controls.append(button);
    }
  }

  function saveLocation(lat,lng) {if(!Number.isFinite(lat)||!Number.isFinite(lng)||Math.abs(lat)>90||Math.abs(lng)>180){toast('أدخل إحداثيات صحيحة للموقع.');return;}state.location={lat,lng};text('location-state','تم اعتماد موقعك المحدد. سيُتحقق من التغطية على الخادم.');$('latitude').value=String(lat);$('longitude').value=String(lng);queueQuote();}
  function applyTheme(theme={}) {
    const valid=(value)=>typeof value==='string'&&/^#[a-f0-9]{6}$/i.test(value);
    const lum=(value)=>{const rgb=[1,3,5].map((start)=>parseInt(value.slice(start,start+2),16)/255).map((c)=>c<=.04045?c/12.92:((c+.055)/1.055)**2.4);return .2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];};
    const contrast=(a,b)=>(Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05);
    const root=document.documentElement;
    if(valid(theme.accent)){root.style.setProperty('--accent',theme.accent);const ink=contrast(theme.accent,'#ffffff')>=4.5?'#ffffff':'#24231f';root.style.setProperty('--accent-ink',ink);root.style.setProperty('--accent-dark',theme.accent);}
    // This storefront keeps the approved light surface and legible panel text.
    const background=valid(theme.background)&&lum(theme.background)>=.75?theme.background:'#fbf9f5';
    root.style.setProperty('--bg',background);
    if(valid(theme.text)&&contrast(theme.text,background)>=4.5&&contrast(theme.text,'#ffffff')>=4.5)root.style.setProperty('--ink',theme.text);
    root.style.setProperty('--font','"Noto Kufi Arabic",Tahoma,Arial,sans-serif');
    window.KPSAppearance?.apply(theme,state.brand.id);
  }
  function bindEvents() {const tickerPause=$('ticker-pause');tickerPause.addEventListener('click',()=>{state.tickerPaused=!state.tickerPaused;renderTicker();});$('banner-area').addEventListener('mouseenter',stopBannerRotation);$('banner-area').addEventListener('mouseleave',startBannerRotation);$('banner-area').addEventListener('focusin',stopBannerRotation);$('banner-area').addEventListener('focusout',()=>setTimeout(startBannerRotation,0));$('banner-area').addEventListener('keydown',(event)=>{if(state.banners.length<2||!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();switchBanner(state.bannerIndex+(event.key==='ArrowLeft'?1:-1));});document.querySelectorAll('[data-close]').forEach((button)=>button.addEventListener('click',()=>closeDialog(button.closest('dialog'))));document.querySelectorAll('dialog').forEach((dialog)=>{dialog.addEventListener('click',(event)=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)closeDialog(dialog);}});dialog.addEventListener('cancel',()=>{if(dialog.id==='confirm-dialog')clearConfirmation();});dialog.addEventListener('close',()=>{if(dialog.open)return;const focus=focusHistory.get(dialog);if(focus?.isConnected&&focus.getClientRects().length)focus.focus({preventScroll:true});else $('menu').focus({preventScroll:true});if(dialog.id==='chat-dialog')clearTimeout(state.chatTimer);if(dialog.id==='orders-dialog'){clearTimeout(state.ordersTimer);state.activeOrder=null;}if(dialog.id==='confirm-dialog')clearConfirmation();});});$('branch-select').addEventListener('change',()=>changeBranch($('branch-select').value));document.querySelectorAll('input[name=service]').forEach((input)=>input.addEventListener('change',()=>{state.service=input.value;renderService();queueQuote();}));document.querySelectorAll('input[name=payment]').forEach((input)=>input.addEventListener('change',queueQuote));['customer-name','customer-phone','neighborhood','order-notes','address','car-model','car-color','car-plate'].forEach((id)=>$(id).addEventListener('input',queueQuote));$('search').addEventListener('input',renderProducts);$('product-qty').addEventListener('change',renderOptions);$('product-minus').addEventListener('click',()=>{$('product-qty').value=String(Number($('product-qty').value)-1);renderOptions();});$('product-plus').addEventListener('click',()=>{$('product-qty').value=String(Number($('product-qty').value)+1);renderOptions();});$('apply-first-option').addEventListener('click',()=>{const first={...state.optionValues[0]};state.optionValues=state.optionValues.map(()=>({...first}));renderOptions();});$('product-add').addEventListener('click',()=>busy($('product-add'),addProduct));$('checkout-button').addEventListener('click',()=>busy($('checkout-button'),beginCheckout));$('confirm-submit').addEventListener('click',()=>{const action=state.confirmation?.action;if(action)busy($('confirm-submit'),async()=>{try{await action();}catch(error){showError('confirm-error',error.message);}});});$('header-cart').addEventListener('click',()=>$('basket').scrollIntoView({behavior:'smooth'}));$('mobile-basket').addEventListener('click',()=>$('basket').scrollIntoView({behavior:'smooth'}));$('account-button').addEventListener('click',()=>{renderAccount();showError('account-error','');openDialog('account-dialog');});$('orders-button').addEventListener('click',()=>openOrders());$('contacts-button').addEventListener('click',openContacts);$('chat-inbox').addEventListener('click',openContacts);$('contact-context-form').addEventListener('submit',startContact);$('contact-order').addEventListener('change',updateContactContext);$('identity-onboarding-form').addEventListener('submit',async event=>{event.preventDefault();await busy($('identity-onboarding-submit'),async()=>{try{if(!state.emailLink)throw new Error('افتح رابط الدعوة مرة أخرى.');const result=await api('/api/identity/email/verify',{method:'POST',data:{...state.emailLink,display_name:$('identity-onboarding-name').value.trim(),phone:$('identity-onboarding-phone').value.trim()}});state.emailLink=null;await establishIdentity(result);}catch(error){showError('account-error',error.message);}});});$('parse-map-url').addEventListener('click',()=>busy($('parse-map-url'),async()=>{try{const result=await api('/api/location/parse-map-url',{method:'POST',data:{url:$('map-url').value.trim()}});saveLocation(result.location.lat,result.location.lng);}catch(error){toast(error.message);}}));$('guest-form').addEventListener('submit',requestGuest);$('email-request-form').addEventListener('submit',requestEmail);['email-login-button','link-email-button'].forEach(id=>$(id).addEventListener('click',()=>{$('email-request-form').hidden=false;$('login-email').focus();}));$('product-image-zoom').addEventListener('click',()=>showImage(state.product));$('product-image').addEventListener('click',()=>showImage(state.product));$('otp-request-form').addEventListener('submit',requestOTP);$('otp-verify-form').addEventListener('submit',verifyOTP);$('change-phone').addEventListener('click',()=>{state.challenge=null;$('otp-request-form').hidden=false;$('otp-verify-form').hidden=true;showError('account-error','');$('phone').focus();});$('logout-button').addEventListener('click',()=>busy($('logout-button'),async()=>{try{await api('/api/auth/logout',{method:'POST',data:{}});state.session={user:null,csrf_token:null};renderAccount();closeDialog('account-dialog');toast('تم تسجيل الخروج.');}catch(error){showError('account-error',error.message);}}));$('manual-location-button').addEventListener('click',()=>{$('manual-location').hidden=!$('manual-location').hidden;if(!$('manual-location').hidden)$('latitude').focus();});$('save-location').addEventListener('click',()=>{if(!$('latitude').value.trim()||!$('longitude').value.trim()){toast('أدخل خط العرض وخط الطول قبل الاعتماد.');return;}saveLocation(Number($('latitude').value),Number($('longitude').value));});$('locate-button').addEventListener('click',()=>{if(!navigator.geolocation){toast('تحديد الموقع غير مدعوم في هذا المتصفح. يمكنك إدخاله يدويًا.');return;}const button=$('locate-button');button.disabled=true;text('location-state','جاري تحديد موقعك بإذنك…');navigator.geolocation.getCurrentPosition((position)=>{button.disabled=false;saveLocation(position.coords.latitude,position.coords.longitude);},()=>{button.disabled=false;text('location-state','تعذر تحديد الموقع. اسمح بالوصول أو أدخله يدويًا.');},{enableHighAccuracy:true,timeout:15000,maximumAge:0});});$('chat-form').addEventListener('submit',sendMessage);$('chat-image').addEventListener('change',()=>text('chat-image-name',$('chat-image').files[0]?.name||''));document.addEventListener('visibilitychange',()=>{if(!document.hidden&&$('chat-dialog').open)pollChat();if(document.hidden)stopBannerRotation();else startBannerRotation();});window.addEventListener('online',()=>{toast('عاد الاتصال. نتحقق من حالة الطلبات المحفوظة.');if($('chat-dialog').open)pollChat();if(state.cart.length)queueQuote();});let touchStart=0;$('banner-area').addEventListener('touchstart',(event)=>{touchStart=event.changedTouches[0].clientX;},{passive:true});$('banner-area').addEventListener('touchend',(event)=>{const delta=event.changedTouches[0].clientX-touchStart;if(Math.abs(delta)>50&&state.banners.length>1){switchBanner(state.bannerIndex+(delta>0?1:-1));}},{passive:true});}
  function renderBrand() {
    const name=state.brand.name||'الطلبات';document.title=`${name} | الطلبات`;
    document.querySelector('meta[name=description]').content=`منيو ${name} وطلبات التوصيل والاستلام من الفرع والسيارة`;
    $('brand-home').href=requestedBrandId&&requestedBrandId!=='kps'?`/?brand_id=${encodeURIComponent(requestedBrandId)}`:'/';$('brand-home').setAttribute('aria-label',`${name} الرئيسية`);
    const label=$('brand-name');label.replaceChildren();label.dir=requestedBrandId==='kps'?'ltr':'auto';
    if(requestedBrandId==='kps'){label.append(document.createTextNode('KPS '),node('span','','COFFEE'));}else label.append(document.createTextNode(name));
    label.append(node('small','','طلبك، على طريقتك'));text('footer-brand',name);$('banner-area').setAttribute('aria-label',`إعلانات ${name}`);
    const logo=safeImage(state.brand.logo_url||(requestedBrandId==='kps'?'/assets/kps-logo-fb911a7b4bb4.png':''));
    $('brand-logo').alt=name;$('brand-logo').hidden=!logo;if(logo){$('brand-logo').src=logo;$('brand-logo').addEventListener('error',()=>{$('brand-logo').hidden=true;},{once:true});}else $('brand-logo').removeAttribute('src');
    renderBanner();
  }
  async function init() {
    bindEvents();renderBrand();
    if(!requestedBrandId){showError('site-notice','رابط البراند غير صالح. استخدم رابطًا صادرًا من إدارة البراند.');$('products').replaceChildren(node('div','empty-state','تعذر تحديد البراند من هذا الرابط.'));['account-button','orders-button','contacts-button','branch-select'].forEach((id)=>$(id).disabled=true);return;}
    const params=new URLSearchParams({brand_id:requestedBrandId});
    const results=await Promise.allSettled([api(`/api/catalog?${params}`),api(`/api/branches?${params}`),api('/api/session'),api(`/api/identity/channels?${params}`)]);
    const [catalogResult,branchResult,sessionResult,channelResult]=results;if(channelResult.status==='fulfilled')state.channels=channelResult.value.channels||channelResult.value;
    if(catalogResult.status==='fulfilled'){
      const data=catalogResult.value;
      if(data.brand?.id!==requestedBrandId)throw new Error('هوية البراند لا تطابق الرابط.');
      state.products=data.products||[];state.legacyItemMappings=data.legacy_item_mappings||{};state.brand=data.brand;renderBrand();applyTheme(state.brand.theme);state.sourceVerified=data.source_verified===true||data.metadata?.runtime_verified===true;
      if(!state.sourceVerified&&state.products.length){showError('catalog-notice',requestedBrandId==='kps'?'بعض الأصناف لا تطابق النسخة المعتمدة أو تحتاج اعتماد تعديلاتها. تبقى الأصناف المعتمدة متاحة حسب الفرع والخدمة.':'الأسعار والخيارات الحالية قيد التحقق؛ لا يُتاح تأكيد طلب قبل اعتمادها.');$('catalog-notice').className='notice';}
      restoreDraft();renderCategories();renderProducts();
    }else{$('products').replaceChildren(node('div','empty-state','تعذر تحميل منيو هذا البراند. حدّث الصفحة للمحاولة مجددًا.'));showError('site-notice',catalogResult.reason.message);}
    if(branchResult.status==='fulfilled'){state.branches=branchResult.value.branches||[];renderBranches();}else showError('service-notice',branchResult.reason.message);
    if(sessionResult.status==='fulfilled')state.session=sessionResult.value;else showError('site-notice','يمكنك تصفح المنيو. تعذر التحقق من جلسة الدخول حاليًا.');
    renderAccount();renderService();renderCart();loadBanners();await verifyEmailLink();
  }
  init().catch(()=>showError('site-notice','تعذر تحميل جزء من الصفحة. حدّث الصفحة للمحاولة مجددًا.'));
})();
