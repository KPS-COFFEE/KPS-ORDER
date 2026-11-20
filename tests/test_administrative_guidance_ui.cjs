const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
for(const lang of ['ar','en']){
  const context={window:{},document:{documentElement:{lang}}};vm.createContext(context);
  vm.runInContext(fs.readFileSync('public/assets/order-amendments.js','utf8'),context);
  const html=context.window.KPSAdministrativeGuidance.render({operational_status:'completed',financial_status:'pending',state_note:{ar:'تشغيل فقط',en:'Operations only'},stages:[{responsible:{ar:'المحاسبة',en:'Accounting'},action:{ar:'<img src=x onerror=alert(1)>',en:'<img src=x onerror=alert(1)>'},evidence:{ar:'مرجع فعلي',en:'Actual reference'},next_step:{ar:'مراجعة',en:'Review'},fallback:{ar:'تبقى معلقة',en:'Remains pending'}}]});
  assert(html.includes(lang==='ar'?'المعالجة المالية معلقة':'Financial resolution pending'));
  assert(html.includes('&lt;img'));
  assert(!html.includes('<img'));
  assert(!html.includes('<button'));
  assert(!html.includes('href='));
  assert.equal(context.window.KPSAdministrativeGuidance.render(null),'');
  const ticket=context.window.KPSAdministrativeGuidance.render({review_only:true,responsible:{ar:'المالك',en:'Owner'},action:{ar:'مراجعة',en:'Review'},next_step:{ar:'التنفيذ مستقل',en:'Execution is separate'}});
  assert(ticket.includes(lang==='ar'?'التنفيذ غير مثبت':'does not establish execution'));
}
console.log('PASS: AR/EN administrative state guidance, independent pending finance, review-only tickets and escaped source text without unsupported controls');
