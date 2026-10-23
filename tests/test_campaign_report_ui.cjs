'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const ui=require('../public/assets/campaign-report.js');

test('Riyadh-selected calendar dates preserve exact bounded period and beneficiary pagination',()=>{
  const q=ui.queryFor({start:'2026-10-01',end:'2026-10-23',branch_id:'enb-branch & 2',policy_version:'12'},50);
  const daily=new URLSearchParams(q.daily),beneficiaries=new URLSearchParams(q.beneficiaries);
  assert.equal(daily.get('branch_id'),'enb-branch & 2');
  assert.equal(daily.get('policy_version'),'12');assert.equal(daily.get('start'),'2026-10-01');
  assert.equal(beneficiaries.get('offset'),'50');assert.equal(beneficiaries.get('limit'),'51');
  assert.equal(daily.has('brand_id'),false);
});

test('invalid dates, excessive ranges, decimal revisions and offsets do not reach reports',()=>{
  for(const values of [
    {start:'2026-02-30',end:'2026-03-01'},
    {start:'2026-10-23',end:'2026-10-22'},
    {start:'2026-01-01',end:'2026-04-04'},
    {start:'9999-12-31',end:'9999-12-31'},
    {start:'2026-10-01',end:'2026-10-23',policy_version:'1.5'},
    {start:'2026-10-01',end:'2026-10-23',policy_version:'0'}
  ])assert.throws(()=>ui.queryFor(values));
  assert.throws(()=>ui.queryFor({start:'2026-10-01',end:'2026-10-23'},-1));
});

test('existing additional-brand grant is required before sending an operations brand header',()=>{
  const kps={brand_id:'kps',role:'owner',operations_brands:[{id:'kps'},{id:'enbishan'}]};
  assert.equal(ui.grantedBrand(kps,'enbishan'),true);
  assert.equal(ui.grantedBrand(kps,'another'),false);
  assert.equal(ui.grantedBrand(kps,'kps'),false);
  assert.equal(ui.grantedBrand({brand_id:'kps'},'enbishan'),false);
});

test('role, withdrawn report access and morning context cannot open broad report history',()=>{
  assert.equal(ui.hasReportAccess({role:'owner'}),true);
  assert.equal(ui.hasReportAccess({role:'delivery_manager',effective_permissions:['reports_view']}),true);
  for(const user of [{role:'customer'},{role:'driver',effective_permissions:['reports_view']},{role:'delivery_manager',effective_permissions:[]},{role:'delivery_manager',effective_permissions:['reports_view'],delivery_context:{shift_id:'morning'}}])assert.equal(ui.hasReportAccess(user),false);
});
