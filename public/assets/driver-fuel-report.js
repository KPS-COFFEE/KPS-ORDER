'use strict';
window.KPSDriverFuelReport = {async open(ctx) {
 const {api, tr, esc, heading, lang} = ctx;
 const root = document.querySelector('#content');
 const opt = await api('/api/fleet/driver-report/options');
 const t = (ar,en)=>tr?tr(ar,en):ar;
 const n = x => x == null ? '—' : new Intl.NumberFormat(lang==='en'?'en':'ar',{maximumFractionDigits:2}).format(x);
 const money = x => x == null ? '—' : n(x/100)+' '+t('ر.س','SAR');
 const options = (rows,key,label)=>rows.map(r=>`<option value="${esc(r[key])}">${esc(r[label])}</option>`).join('');
 heading(t('البنزين والمسافات حسب السائق','Driver fuel and distance'));
 root.innerHTML=`<section class="panel driver-fuel-report"><p>${esc(t('اختر الفترة والسائق لمراجعة القراءات والتعبئة. البيانات الناقصة تظهر بوضوح ولا تحسب كعجز.','Review readings and refuelling by driver and period. Missing data is explicit and is not a driver shortage.'))}</p><form id="driver-fuel-filters" class="driver-fuel-filters"><label>${t('من','From')}<input name="start" type="date" required value="${esc(opt.today)}"></label><label>${t('إلى','To')}<input name="end" type="date" required value="${esc(opt.today)}"></label><label>${t('السائق','Driver')}<select name="driver_id"><option value="">${t('كل السائقين','All drivers')}</option>${options(opt.drivers,'id','display_name')}</select></label><label>${t('السيارة','Vehicle')}<select name="vehicle_id"><option value="">${t('كل السيارات','All vehicles')}</option>${options(opt.vehicles,'id','plate')}</select></label><label>${t('المدينة','City')}<select name="city"><option value="">${t('كل المدن','All cities')}</option>${opt.cities.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('')}</select></label><button type="submit">${t('عرض التقرير','Show report')}</button></form><div class="driver-fuel-export"><p>${esc(t('تصدير كل نتائج الفلاتر المعروضة، بما فيها الصفحات الأخرى.','Export all results for the displayed filters, including other pages.'))}</p><button type="button" data-fuel-pdf disabled>${t('تصدير PDF','Export PDF')}</button></div><p data-fuel-error role="alert"></p><div data-fuel-result aria-live="polite"></div></section>`;
 const form=root.querySelector('form'), result=root.querySelector('[data-fuel-result]'), error=root.querySelector('[data-fuel-error]');
 let offset=0,sequence=0,exporting=false,loadedFilters=null,submitted=new URLSearchParams(new FormData(form));
 const pdfButton=root.querySelector('[data-fuel-pdf]');
 const labels=[['operating_day',t('اليوم التشغيلي','Operating day')],['driver_name',t('السائق','Driver')],['plate',t('السيارة','Vehicle')],['cities',t('المدينة','City')],['start_odometer',t('عداد البداية','Start odometer')],['end_odometer',t('عداد النهاية','End odometer')],['actual_distance',t('المسافة كم','Distance km')],['liters',t('اللترات المسجلة','Recorded liters')],['fuel_amount_halalas',t('قيمة البنزين','Fuel amount')],['record_status',t('حالة السجل','Record status')]];
 async function load(){
  const current=++sequence;error.textContent='';form.querySelector('button').disabled=true;pdfButton.disabled=true;loadedFilters=null;
  try{
   const q=new URLSearchParams(submitted);q.set('offset',offset);q.set('limit','50');
   const data=await api('/api/fleet/driver-report?'+q);
   if(current!==sequence||!root.contains(result))return;
   loadedFilters=new URLSearchParams(submitted);
   const s=data.summary;
   result.innerHTML=`<div class="driver-fuel-summary"><article><small>${t('المسافة المسجلة','Recorded distance')}</small><strong>${n(s.distance_km)} ${t('كم','km')}</strong><span>${n(s.distance_missing_records)} ${t('سجلات دون مسافة مكتملة','records without complete distance')}</span></article><article><small>${t('البنزين المسجل','Recorded fuel')}</small><strong>${n(s.liters)} ${t('لتر','L')}</strong><span>${n(s.fuel_record_missing)} ${t('سجلات دون تعبئة مسجلة','records without refuelling')}</span></article><article><small>${t('قيمة التعبئة المعروفة','Known fuel amount')}</small><strong>${money(s.fuel_amount_known_halalas)}</strong><span>${s.fuel_amount_halalas==null?t('الإجمالي غير مكتمل — توجد بيانات غير مسجلة','Total incomplete — missing records'):t('القيمة المسجلة مكتملة','Recorded amount complete')}</span></article></div><p>${esc(t('كل صف فترة استلام سيارة في شفت؛ قد يتكرر اليوم عند تعدد الشفتات أو تغيير السيارة. الشرطـة تعني غير مسجل.','Each row is a vehicle interval in a shift. A day may repeat across shifts or vehicle changes. A dash means not recorded.'))}</p><div class="table-wrap" tabindex="0"><table><thead><tr>${labels.map(([,x])=>`<th scope="col">${esc(x)}</th>`).join('')}</tr></thead><tbody>${data.rows.map(r=>`<tr>${labels.map(([k])=>`<td class="${(k==='actual_distance'&&r[k]==null)||(k==='liters'&&r.fuel_record_missing)||(k==='fuel_amount_halalas'&&r[k]==null)?'driver-fuel-missing':''}">${esc(k==='liters'&&r.fuel_record_missing?t('غير مسجل','Not recorded'):k==='fuel_amount_halalas'?money(r[k]):k==='cities'?r[k].join(' / '):k==='record_status'?({closed:t('مغلق','Closed'),open:t('مفتوح','Open'),exception:t('استثناء','Exception')}[r[k]]||r[k]):['start_odometer','end_odometer','actual_distance','liters'].includes(k)?n(r[k]):r[k]??'—')}</td>`).join('')}</tr>`).join('')||`<tr><td colspan="10">${t('لا توجد سجلات ضمن الفترة والفلاتر المختارة','No records in the selected period and filters')}</td></tr>`}</tbody></table></div><div class="driver-fuel-pages"><button type="button" data-fuel-prev ${offset===0?'disabled':''}>${t('السابق','Previous')}</button><span>${n(data.total)} ${t('سجل','records')}</span><button type="button" data-fuel-next ${offset+data.rows.length>=data.total?'disabled':''}>${t('التالي','Next')}</button></div><p class="muted">${esc(data.note)}</p>`;
  }catch(ex){if(current===sequence)error.textContent=ex.message;}finally{if(current===sequence){form.querySelector('button').disabled=false;pdfButton.disabled=exporting||!loadedFilters;}}
 }
 pdfButton.addEventListener('click',async()=>{
  if(exporting||!loadedFilters)return;
  const q=new URLSearchParams(loadedFilters);q.set('lang',lang==='en'?'en':'ar');
  exporting=true;pdfButton.disabled=true;error.textContent='';pdfButton.textContent=t('جار تجهيز PDF…','Preparing PDF…');
  try{
   const headers={'X-KPS-Context':'work'};if(ctx.brand)headers['X-Operations-Brand']=ctx.brand;
   const response=await fetch('/api/fleet/driver-report/export/pdf?'+q,{credentials:'same-origin',headers});
   if(!response.ok){let detail={};try{detail=await response.json();}catch{}throw new Error(detail.detail?.message||t('تعذر تصدير التقرير؛ أعد المحاولة.','Report export failed; retry.'));}
   if(!(response.headers.get('content-type')||'').includes('application/pdf'))throw new Error(t('تعذر تصدير التقرير؛ حدّث الشاشة.','Report export failed; refresh the screen.'));
   const blob=await response.blob();if(!root.contains(pdfButton))return;
   const href=URL.createObjectURL(blob),a=document.createElement('a');a.href=href;a.download=`driver-fuel-${q.get('start')}-${q.get('end')}.pdf`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(href),1000);
  }catch(ex){if(root.contains(error))error.textContent=ex.message;}finally{exporting=false;pdfButton.textContent=t('تصدير PDF','Export PDF');pdfButton.disabled=!loadedFilters;}
 });
 form.addEventListener('submit',e=>{e.preventDefault();submitted=new URLSearchParams(new FormData(form));offset=0;void load();});
 result.addEventListener('click',e=>{if(e.target.closest('[data-fuel-prev]')){offset=Math.max(0,offset-50);void load();}if(e.target.closest('[data-fuel-next]')){offset+=50;void load();}});
 await load();
}};
