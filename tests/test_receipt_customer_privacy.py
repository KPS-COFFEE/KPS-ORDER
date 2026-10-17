"""Receipt-only privacy: no changes to stored customer/order identity."""
import copy
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.branch_printing import receipt


@pytest.mark.parametrize('language', ['ar', 'en'])
@pytest.mark.parametrize('kind', ['order', 'preparation'])
def test_branch_receipt_omits_customer_without_mutation(language, kind):
    order = {'snapshot': {'order_reference': 'HFR2-261011-10001', 'items': []},
             'order_no': 10001, 'created_at': datetime.now(timezone.utc),
             'customer': {'name': 'PRIVATE CUSTOMER NAME'},
             'payment_method': 'cash', 'payment_status': 'unpaid'}
    original = copy.deepcopy(order)
    payload = receipt(order, {'name': 'Test branch'}, kind, language)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert 'PRIVATE CUSTOMER NAME' not in serialized
    assert 'العميل' not in serialized and 'Customer' not in serialized
    assert 'HFR2-261011-10001' in serialized
    assert order == original


def test_browser_receipt_all_languages_and_types_excludes_entire_customer_row():
    source = (Path(__file__).resolve().parents[1] / 'public/ops.js').read_text()
    renderer = source[source.index('  function receiptHTML('):source.index('  async function printOrder(')]
    script = '''
const assert = require('node:assert/strict');
const state = {lang:'ar'};
const tr = (ar,en)=>state.lang==='ar'?ar:en;
const esc = s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;');
const money = s=>String(s??0), datetime=s=>s, serviceName=s=>s, statusName=s=>s;
const orderLabel=o=>o.order_reference, branchLabel=o=>o.branch_name;
const location = {origin:'https://example.test'};
''' + renderer + '''
const o={customer_name:'PRIVATE CUSTOMER NAME',customer:{name:'PRIVATE CUSTOMER NAME'},
 order_reference:'HFR2-261011-10001',branch_name:'Branch',items:[],created_at:'2026-10-11',
 service:'delivery',preparation_status:'new',payment_method:'cash',payment_status:'unpaid'};
o.items=[{product_id:'p1',name:'كوب حراري',quantity:1,units:[{option_snapshot:{COLOR:{name:'اللون',choices:[{name:'أزرق'}]}}}]}];
o.print_labels_en={p1:{'كوب حراري':'Travel Mug','اللون':'Color','أزرق':'Blue'}};
const before=JSON.stringify(o);
for(const lang of ['ar','en']) for(const prep of [false,true]) {
 state.lang=lang;
 const html=receiptHTML(o,prep,lang);
 assert(!html.includes('PRIVATE CUSTOMER NAME'));
 assert(!html.includes('>العميل<')&&!html.includes('>Customer<'));
 assert(html.includes('HFR2-261011-10001'));
 assert(html.includes(lang==='en'?'Travel Mug':'كوب حراري'));
 assert(html.includes(lang==='en'?'Color: Blue':'اللون: أزرق'));
 if(lang==='en')assert(!html.includes('كوب حراري'));
 const anonymous={...o};delete anonymous.customer_name;delete anonymous.customer;
 assert.equal(html,receiptHTML(anonymous,prep,lang)); // No blank row or reserved space.
 assert.equal(JSON.stringify(o),before);
 state.lang='ar';const englishDefault=receiptHTML(o,prep);
 assert(englishDefault.includes('lang="en"'));
 assert(englishDefault.includes('Travel Mug'));
 assert(englishDefault.includes('Payment method')||prep);
 assert.equal(state.lang,'ar');
}
'''
    subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
    # Operational detail and driver views intentionally retain the authorized name.
    assert "dataItem(tr('العميل','Customer'),o.customer_name" in source
    assert "${esc(o.customer_name)}</p>" in source


def test_english_menu_translation_and_override_keep_arabic_snapshot():
    order={'snapshot':{'items':[{'product_id':'kps-30','name':'كوب حراري','quantity':1,
        'line_total_halalas':1800,'units':[{'option_snapshot':{'COLOR':{'name':'لون الكوب الحراري',
        'choices':[{'name':'أزرق','code':'BLUE'}]}}}]}]},'order_no':1,
        'created_at':datetime.now(timezone.utc),'payment_method':'card','payment_status':'unpaid'}
    original=copy.deepcopy(order)
    for kind in ['order','preparation']:
        payload=receipt(order,{'name':'Branch'},kind,'en')
        text=' '.join(line['text'] for line in payload['lines'])
        assert 'Thermal Cup' in text and 'Thermal Cup Color — Blue' in text
        changed=receipt(order,{'name':'Branch'},kind,'en',{'kps-30':{'كوب حراري':'Travel Mug','أزرق':'Navy Blue'}})
        text=' '.join(line['text'] for line in changed['lines'])
        assert 'Travel Mug' in text and 'Navy Blue' in text
        arabic=receipt(order,{'name':'Branch'},kind,'ar',{'kps-30':{'كوب حراري':'Travel Mug'}})
        assert any(line['text']=='كوب حراري' for line in arabic['lines'])
    assert order==original
