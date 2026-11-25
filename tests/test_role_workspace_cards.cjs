'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const context={window:{},Intl,URLSearchParams,AbortController,setTimeout,clearTimeout};
vm.runInNewContext(fs.readFileSync('public/assets/owner-dashboard.js','utf8'),context);
const cards=context.window.KPSOwnerDashboard.workspaceCards;
test('manager cards expose only granted destinations and no owner activity',()=>{
  const html=cards({canNavigate:page=>['driverFuel','productSales','employeeActivity'].includes(page)});
  assert.match(html,/data-page="driverFuel"/);
  assert.match(html,/data-page="productSales"/);
  assert.doesNotMatch(html,/employeeActivity|accounting|interventionInbox|ownerDashboard/);
});
test('ungranted workspace is empty and owner routes use widget-specific events',()=>{
  assert.equal(cards(), '');
  const html=cards({owner:true,canNavigate:()=>true});
  assert.equal((html.match(/data-owner-page=/g)||[]).length,5);
  assert.doesNotMatch(html,/ data-page=/);
  assert.match(html,/employeeActivity/);
});
test('translated card content is escaped',()=>{
  const html=cards({canNavigate:page=>page==='driverFuel',tr:()=>'<img onerror="bad">'});
  assert.doesNotMatch(html,/<img/);
  assert.match(html,/&lt;img/);
});
