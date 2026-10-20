const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const helper=source.slice(source.indexOf('  function printAlertRow('),source.indexOf('  async function refreshOperationalAlerts('));
for(const lang of ['ar','en']){
  const context={tr:(a,b)=>lang==='ar'?a:b,esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),branchLabel:j=>j.branch_name,orderLabel:j=>j.order_reference,datetime:String};
  vm.createContext(context);vm.runInContext(helper+';this.render=printAlertRow;',context);
  for(const status of ['queued','claimed','uncertain']){
    const html=context.render({id:'job-1',order_id:'order-7',branch_id:'branch-4',branch_name:'Branch <4>',order_reference:'REF-7',printer_label:'Printer <1>',kind:'order',status,created_at:'2026-10-19'});
    assert.match(html,/data-action="order-detail" data-id="order-7"/);
    assert.match(html,/data-action="branch-printers" data-id="branch-4"/);
    assert.match(html,/REF-7/);assert.match(html,/Branch &lt;4>/);assert.match(html,/Printer &lt;1>/);
    assert.ok(html.includes(status==='uncertain'?(lang==='ar'?'قد تكون الورقة خرجت':'paper may have printed'):(lang==='ar'?'دقيقتين':'two minutes')));
  }
  const test=context.render({kind:'test',status:'uncertain',branch_id:'branch-4',branch_name:'Branch 4'});
  assert.ok(!test.includes('data-action="order-detail"'));
}
console.log('PASS: AR/EN print alerts identify order, branch, printer, status and scoped actions; escape labels; handle test jobs.');
