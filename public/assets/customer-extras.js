'use strict';
(() => {
  let ctx=null,scope='',loadScope='',notice=null,entries=[],next=null;
  const pending=new Map(),labels=[];
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const tr=(ar,en)=>document.documentElement.lang==='en'?en:ar;
  function wording(n,ar,en){labels.push([n,ar,en]);n.textContent=tr(ar,en);return n;}
  function field(parent,id,ar,en,type='text'){const label=el('label');label.className='field';label.append(wording(el('span'),ar,en));const input=el('input');input.id=id;input.type=type;label.append(input);parent.append(label);return input;}
  function sar(value){const text=value.trim().replace(/[٠-٩]/g,c=>String(c.charCodeAt(0)-1632)).replace(/[۰-۹]/g,c=>String(c.charCodeAt(0)-1776));if(!text)return 0;if(!/^\d{1,6}(?:\.\d{1,2})?$/.test(text))throw new Error(tr('أدخل مبلغًا صحيحًا بمنزلتين عشريتين.','Enter an amount with at most two decimal places.'));const [whole,part='']=text.split('.');return Number(whole)*100+Number(part.padEnd(2,'0'));}
  function create(){
    if(document.getElementById('customer-extra-controls'))return;
    const root=el('section');root.id='customer-extra-controls';
    const benefits=el('div');benefits.id='benefit-fields';
    field(benefits,'benefit-code','رمز العرض — اختياري','Offer code — optional').maxLength=50;
    const amount=field(benefits,'cashback-amount','استخدام رصيد الكاش باك (ر.س)','Use cashback credit (SAR)');amount.inputMode='decimal';
    root.append(benefits);
    const gift=el('div');gift.id='gift-controls';
    const checked=field(gift,'gift-enabled','هذا الطلب إهداء','This order is a gift','checkbox');
    const detail=el('div');detail.id='gift-fields';detail.hidden=true;
    field(detail,'gift-recipient-name','اسم مستلم الهدية','Gift recipient name').maxLength=100;
    field(detail,'gift-recipient-phone','جوال مستلم الهدية','Gift recipient mobile','tel').maxLength=30;
    field(detail,'gift-message','رسالة الإهداء — اختياري','Gift message — optional').maxLength=500;
    detail.append(wording(el('p'),'يبقى الطلب والفاتورة في حسابك؛ رقم المستلم للتسليم فقط.','The order and invoice stay in your account; the recipient number is for fulfilment only.'));
    gift.append(detail);root.append(gift);
    root.addEventListener('input',()=>{detail.hidden=!checked.checked;ctx?.queueQuote();});
    document.getElementById('quote-summary').before(root);
    const account=el('section');account.id='customer-extra-account';
    account.append(wording(el('h3'),'رصيدك والموافقات','Your credit and consent'));
    const balance=el('p');balance.id='cashback-balance';account.append(balance);
    const history=el('div');history.id='cashback-history';account.append(history);
    const more=wording(el('button'),'عرض حركات أخرى','More transactions');more.id='cashback-more';more.type='button';more.addEventListener('click',()=>loadWallet(true));account.append(more);
    const terms=el('p');terms.id='marketing-notice';account.append(terms);
    account.append(wording(el('p'),'التسويق اختياري ومستقل عن رسائل الطلب والخدمة.','Marketing is optional and separate from order and service messages.'));
    for(const channel of ['sms','whatsapp']){
      const input=field(account,'marketing-'+channel,channel==='sms'?'عروض عبر SMS':'عروض عبر واتساب',channel==='sms'?'Offers by SMS':'Offers by WhatsApp','checkbox');
      const save=wording(el('button'),'حفظ اختيار '+(channel==='sms'?'SMS':'واتساب'),'Save '+(channel==='sms'?'SMS':'WhatsApp')+' choice');save.type='button';
      save.addEventListener('click',async()=>{save.disabled=true;try{const data={channel,granted:input.checked,notice_version:notice?.version||'withdrawal'};const key=scope+JSON.stringify(data);if(!pending.has(key))pending.set(key,crypto.randomUUID());await ctx.api('/api/customer/marketing-consent',{method:'POST',data,idempotency:pending.get(key)});pending.delete(key);await loadConsent();status(tr('حُفظ اختيارك.','Your choice was saved.'));}catch(e){status(e.message);}finally{save.disabled=false;}});account.append(save);
    }
    const message=el('p');message.id='customer-extra-status';message.setAttribute('role','status');account.append(message);
    document.getElementById('account-profile').append(account);
  }
  function status(text){document.getElementById('customer-extra-status').textContent=text;}
  const money=v=>new Intl.NumberFormat(document.documentElement.lang==='en'?'en-SA':'ar-SA',{style:'currency',currency:'SAR'}).format(v/100);
  async function loadWallet(more=false){if(!ctx||!ctx.brand.customer_features?.benefits)return;const expected=scope;try{const d=await ctx.api('/api/customer/benefits'+(more&&next?'?after='+encodeURIComponent(next):''));if(expected!==scope)return;entries=more?[...entries,...d.entries]:d.entries;next=d.next_after;document.getElementById('cashback-balance').textContent=tr('رصيدك: ','Your balance: ')+money(d.balance_halalas);const list=el('ul');for(const row of entries){const kind={earn:tr('كاش باك مكتسب','Credit earned'),earn_reversal:tr('عكس مكافأة مستردة','Reward clawback'),redemption:tr('استخدام الرصيد','Credit used'),redemption_return:tr('إعادة الرصيد','Credit returned')}[row.kind]||row.kind;list.append(el('li',kind+' · '+money(row.amount_halalas)+' · '+row.order_id));}document.getElementById('cashback-history').replaceChildren(list);document.getElementById('cashback-more').hidden=!next;}catch(e){if(expected===scope)status(e.message);}}
  async function loadConsent(){const expected=scope;try{const d=await ctx.api('/api/customer/marketing-consent');if(expected!==scope)return;notice=d.notice;document.getElementById('marketing-notice').textContent=notice?(document.documentElement.lang==='en'?notice.text_en:notice.text_ar):tr('الموافقة التسويقية غير مهيأة؛ يمكنك سحب أي موافقة سابقة.','Marketing consent is not configured; you may withdraw a previous choice.');for(const channel of ['sms','whatsapp']){const input=document.getElementById('marketing-'+channel);input.checked=d.marketing[channel]===true;input.disabled=!notice&&!input.checked;}}catch(e){if(expected===scope)status(e.message);}}
  window.KPSCustomerExtras={
    render(context){ctx=context;create();const identity=context.session?.user;const key=(context.brand?.id||'')+':'+(identity?.id||'');const changed=key!==scope;scope=key;const own=identity?.role==='customer'&&identity.brand_id===context.brand?.id&&context.session.customer_verified===true;
      document.getElementById('benefit-fields').hidden=!own||!context.brand?.customer_features?.benefits;
      document.getElementById('gift-controls').hidden=!own||!context.brand?.customer_features?.gifts;
      document.getElementById('customer-extra-account').hidden=!own;
      document.getElementById('cashback-balance').hidden=!context.brand?.customer_features?.benefits;document.getElementById('cashback-history').hidden=!context.brand?.customer_features?.benefits;
      if(changed)this.reset();const active=own?scope+':'+String(context.brand?.customer_features?.benefits):'';if(own&&loadScope!==active){loadScope=active;loadConsent();loadWallet();}for(const [n,ar,en] of labels)n.textContent=tr(ar,en);
    },
    augment(body){if(ctx?.brand?.customer_features?.benefits&&ctx.session?.customer_verified){body.benefit_code=document.getElementById('benefit-code').value.trim().toUpperCase();body.cashback_halalas=sar(document.getElementById('cashback-amount').value);}if(ctx?.brand?.customer_features?.gifts&&document.getElementById('gift-enabled').checked)body.gift={recipient_name:document.getElementById('gift-recipient-name').value.trim(),recipient_phone:document.getElementById('gift-recipient-phone').value.trim(),message:document.getElementById('gift-message').value.trim()};return body;},
    reset(){loadScope='';pending.clear();notice=null;entries=[];next=null;for(const id of ['benefit-code','cashback-amount','gift-recipient-name','gift-recipient-phone','gift-message']){const n=document.getElementById(id);if(n)n.value='';}const gift=document.getElementById('gift-enabled');if(gift)gift.checked=false;const fields=document.getElementById('gift-fields');if(fields)fields.hidden=true;for(const id of ['cashback-balance','cashback-history','marketing-notice','customer-extra-status']){const n=document.getElementById(id);if(n)n.replaceChildren();}for(const ch of ['sms','whatsapp']){const n=document.getElementById('marketing-'+ch);if(n)n.checked=false;}const more=document.getElementById('cashback-more');if(more)more.hidden=true;}
  };
  new MutationObserver(()=>{for(const [n,ar,en] of labels)n.textContent=tr(ar,en);if(ctx?.session.customer_verified){loadConsent();loadWallet();}}).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
})();
