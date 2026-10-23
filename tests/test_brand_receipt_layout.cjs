// KPS golden hashes were captured from exact PR65 base 24d288d128eddccacffea1da17f8568a665a2703.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const source=fs.readFileSync('public/ops.js','utf8');
const helper=source.slice(source.indexOf('  function receiptHTML('),source.indexOf('  function bindReceiptWindow('));
const context={esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),location:{origin:'https://example.test'},orderLabel:o=>o.snapshot.order_reference,branchLabel:()=> 'TEST branch',serviceName:(s,tr)=>tr('توصيل','Delivery'),statusName:String};
const complimentaryHelper=source.split('\n').find(line=>line.startsWith('  const complimentaryName='));
assert.ok(complimentaryHelper,'Use the real operational complimentary helper in the receipt context');
vm.createContext(context);vm.runInContext(complimentaryHelper+'\n'+helper+';this.render=receiptHTML;',context);
const expected=JSON.parse(fs.readFileSync('tests/fixtures/kps-receipt-layout-sha256.json','utf8'));
for(const lang of ['ar','en'])for(const preparation of [false,true]){
 const order={brand_id:'kps',created_at:'2026-10-23T10:00:00Z',snapshot:{order_reference:'HFR1-261023-0001'},service:'delivery',preparation_status:'ready',payment_method:'cash',payment_status:'unpaid',subtotal_halalas:2000,delivery_fee_halalas:1000,total_halalas:3000,items:[{name:'TEST Cookies',quantity:2,line_total_halalas:2000,note:'TEST <note>',units:[{option_snapshot:{flavor:{name:'Flavor',choices:[{name:'Chocolate'}]}}}]}]};
 const kps=context.render(order,preparation,lang);
 assert.equal(crypto.createHash('sha256').update(kps).digest('hex'),expected[`${lang}-${preparation}`], 'KPS original receipt output must remain byte-for-byte identical');
 const enb=context.render({...order,brand_id:'enbishan'},preparation,lang);
 assert.match(enb,/src="https:\/\/example.test\/assets\/enbishan-original.jpeg"/);
 assert.ok(!enb.includes('/assets/kps-receipt-logo.png'));
 assert.match(enb,/<img class="receipt-logo" style="filter:none"/,'Opaque approved JPEG must retain visible foreground rather than brightness(0) black square');
 assert.ok(enb.includes(lang==='ar'?'عنبيشن':'ENBISHAN'));
 const normalized=enb.replace(' style="filter:none"','').replaceAll('/assets/enbishan-original.jpeg','/assets/kps-receipt-logo.png').replaceAll('عنبيشن','كبز قهوة مختصة').replaceAll('ENBISHAN','KPS COFFEE');
 assert.equal(normalized,kps,'Enbishan reuses exactly the KPS receipt layout');
}
console.log('PASS: unchanged KPS AR/EN order/preparation HTML; Enbishan identical layout with its original logo and identity.');
