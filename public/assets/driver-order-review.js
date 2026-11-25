/* Read-only driver review, embedded so closure entries remain untouched. */
(() => {
  'use strict';
  async function mount({host,api,tr,esc,money,datetime,statusName,driverId,shiftId}) {
    let data=null,brand='',view=shiftId?'closure':'current',offset=0,sequence=0;
    const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Riyadh',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
    let start=today,end=today;
    const name=id=>id==='kps'?tr('كبز','KPS'):id==='enbishan'?tr('عنبيشن','Enbishan'):id;
    const payment=o=>({cash:tr('كاش','Cash'),card:tr('شبكة','Card'),online:tr('إلكتروني','Online'),mixed:tr('مختلط','Mixed')})[o.payment_method]||statusName(o.payment_method);
    const status=o=>['cancelled','rejected'].includes(o.preparation_status)||o.delivery_status==='cancelled'?tr('ملغي','Cancelled'):o.delivery_status==='delivered'?tr('تم التوصيل','Delivered'):o.delivery_status==='problem'?tr('يحتاج متابعة','Needs attention'):statusName(o.delivery_status==='pending'?o.preparation_status:o.delivery_status);
    const label=o=>o.snapshot?.order_reference||o.order_no||o.id;
    function render() {
      const brands=data?.brands||[],selected=brands.find(b=>b.id===brand);
      host.innerHTML=`<div class="driver-order-review stack"><header><h3>${esc(data?.driver?.display_name||tr('طلبات السائق','Driver orders'))}</h3><p class="muted">${esc(view==='closure'?tr('طلبات هذه التقفيلة فقط','Only orders belonging to this closure'):view==='history'?tr('طلبات الفترة المحددة','Orders in the selected period'):tr('طلبات الوردية الحالية','Current workday orders'))}${data?.shift?.opened_at?' · '+esc(datetime(data.shift.opened_at)):''}</p></header>${!shiftId?`<div class="actions"><button type="button" class="quiet" data-review-current>${esc(tr('الشفت الحالي','Current shift'))}</button><button type="button" class="quiet" data-review-history>${esc(tr('طلبات سابقة','Previous orders'))}</button></div><div class="review-dates" ${view==='history'?'':'hidden'}><label>${esc(tr('من','From'))}<input type="date" data-review-start value="${esc(start)}"></label><label>${esc(tr('إلى','Until'))}<input type="date" data-review-end value="${esc(end)}"></label><button type="button" data-review-apply>${esc(tr('عرض الفترة','Show period'))}</button></div>`:''}<div class="review-brands" role="group" aria-label="${esc(tr('طلبات البراند','Brand orders'))}">${brands.map(b=>`<button type="button" data-review-brand="${esc(b.id)}" aria-pressed="${b.id===brand}" class="${b.id===brand?'':'quiet'}"><strong>${esc(name(b.id))}</strong><span>${esc(tr('عدد الطلبات','Order count'))}: ${esc(b.count)}</span><b>${esc(money(b.total_halalas))}</b></button>`).join('')}</div><p class="small-text muted">${esc(tr('الإجمالي قيمة طلبات البراند بجميع حالاتها، وليس صافي التحصيل أو مبلغ التسليم.','Total is the value of this brand’s orders in all statuses, not net collections or the cash handover amount.'))}</p><div class="review-orders">${(data?.orders||[]).filter(o=>o.brand_id===brand).map(o=>`<article class="review-order"><div class="card-head"><button type="button" class="quiet" data-review-order="${esc(o.id)}">${esc(label(o))}</button><strong>${esc(money(o.total_halalas))}</strong></div><div class="review-order-meta"><span class="pill">${esc(status(o))}</span><span>${esc(payment(o))}</span><span>${esc(statusName(o.payment_status))}</span></div><p class="small-text muted">${esc(datetime(o.created_at))}</p><div data-review-detail="${esc(o.id)}" hidden></div></article>`).join('')||`<p class="notice">${esc(selected?.count?tr('جارٍ عرض طلبات البراند…','Loading brand orders…'):tr('لا توجد طلبات ضمن هذا العرض.','No orders in this view.'))}</p>`}</div>${data?.next_offset!=null?`<button type="button" class="quiet" data-review-more>${esc(tr('الصفحة التالية','Next page'))}</button>`:''}${offset?`<button type="button" class="quiet" data-review-first>${esc(tr('العودة لأول صفحة','Back to first page'))}</button>`:''}<p class="form-error" data-review-error role="alert"></p></div>`;
    }
    async function load() {
      const request=++sequence;
      host.setAttribute('aria-busy','true');
      const query=new URLSearchParams(shiftId?{shift_id:shiftId}:{driver_id:driverId,view});
      if(!shiftId&&view==='history'){query.set('start',start);query.set('end',end);}
      if(brand)query.set('brand_id',brand);
      query.set('offset',String(offset));query.set('limit','30');
      try {
        const response=await api('/api/driver-order-review?'+query);
        if(request!==sequence)return;
        data=response;
        if(!brand||!data.brands.some(b=>b.id===brand)){
          brand=data.brands.find(b=>b.count)?.id||data.brands[0]?.id||'';
          if(brand){offset=0;await load();return;}
        }
        render();
      } catch(error) {
        if(request!==sequence)return;
        if(!data)host.innerHTML=`<p class="form-error" data-review-error role="alert"></p><button type="button" class="quiet" data-review-retry>${esc(tr('إعادة المحاولة','Try again'))}</button>`;
        const target=host.querySelector('[data-review-error]');if(target)target.textContent=error.message||String(error);
      } finally {if(request===sequence)host.setAttribute('aria-busy','false');}
    }
    host.addEventListener('click',async event=>{
      const button=event.target.closest('button');if(!button||!host.contains(button))return;
      if(button.hasAttribute('data-review-retry')){await load();}
      else if(button.hasAttribute('data-review-brand')){brand=button.dataset.reviewBrand;offset=0;await load();}
      else if(button.hasAttribute('data-review-history')&&!shiftId){view='history';offset=0;await load();}
      else if(button.hasAttribute('data-review-current')&&!shiftId){view='current';offset=0;await load();}
      else if(button.hasAttribute('data-review-apply')&&!shiftId){
        start=host.querySelector('[data-review-start]').value;end=host.querySelector('[data-review-end]').value;
        if(!start||!end||start>end){host.querySelector('[data-review-error]').textContent=tr('حدد فترة صحيحة.','Choose a valid date range.');return;}
        offset=0;await load();
      } else if(button.hasAttribute('data-review-more')){offset=data.next_offset;await load();}
      else if(button.hasAttribute('data-review-first')){offset=0;await load();}
      else if(button.hasAttribute('data-review-order')){
        const order=data.orders.find(o=>o.id===button.dataset.reviewOrder);if(!order)return;
        const detail=[...host.querySelectorAll('[data-review-detail]')].find(el=>el.dataset.reviewDetail===order.id);if(!detail)return;
        if(detail.dataset.loaded){detail.hidden=!detail.hidden;return;}
        button.disabled=true;
        try{
          const result=await api('/api/orders/'+encodeURIComponent(order.id),{brand:order.brand_id});
          if(!host.contains(detail))return;
          const o=result.order,items=o.snapshot?.items||[];
          detail.innerHTML=`<dl class="data-grid"><div><dt>${esc(tr('العميل','Customer'))}</dt><dd>${esc(o.customer?.name||'—')}</dd></div><div><dt>${esc(tr('الجوال','Mobile'))}</dt><dd dir="ltr">${esc(o.customer?.phone||'—')}</dd></div></dl><ul>${items.map(item=>`<li>${esc(item.name||item.product_name||'—')} × ${esc(item.quantity??item.qty??1)}</li>`).join('')}</ul>${o.notes?`<p>${esc(o.notes)}</p>`:''}`;
          detail.dataset.loaded='true';detail.hidden=false;
        }catch(error){const target=host.querySelector('[data-review-error]');if(target)target.textContent=error.message||String(error);}
        finally{button.disabled=false;}
      }
    });
    await load();
  }
  window.KPSDriverOrderReview={mount};
})();
