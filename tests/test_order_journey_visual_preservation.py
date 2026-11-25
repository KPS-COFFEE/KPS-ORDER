"""Baseline/current manager shell comparison; API-free synthetic order only."""
import os
from pathlib import Path
import re
import subprocess

import pytest

from test_intervention_supervision_browser import supervision_browser, payload

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser acceptance')
ROOT = Path(__file__).resolve().parents[1]
BASELINE = 'b2f1ddc434f2093e0423ced655ecd7ac1448fe71'


def baseline(path):
    return subprocess.check_output(['git', 'show', f'{BASELINE}:{path}'], cwd=ROOT, text=True)


@pytest.mark.parametrize('width', [390, 1280])
@pytest.mark.parametrize('morning', [False, True], ids=['evening', 'morning'])
def test_manager_screen_preserves_baseline(supervision_browser, tmp_path, width, morning):
    """Added workflow controls must not redesign the existing manager screen."""
    output = Path(os.getenv('KPS_SUPERVISION_SCREENSHOTS', str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    images, geometry = [], []
    for version in ('before', 'after'):
        page = supervision_browser.new_page(viewport={'width': width, 'height': 900}, locale='ar-SA')
        try:
            read = baseline if version == 'before' else lambda path: (ROOT / path).read_text()
            html = re.sub(r'<script[\s\S]*?</script>|<link[^>]*>', '', read('public/ops.html'))
            page.route('**/*', lambda route: route.fulfill(content_type='text/html', body=html))
            page.goto('https://qa.invalid/ops')
            page.add_style_tag(content=read('public/ops.css'))
            page.evaluate("""()=>{
                window.setInterval=()=>0;
                window.KPSDriverLocation={init:()=>({refresh:async()=>{},reset(){}})};
                window.KPSDriverChecks={init:()=>({refresh:async()=>{},reset(){}})};
            }""")
            source, count = re.subn(r'  init\(\);\s*\}\)\(\);\s*$',
                '  window.qa={state,orderCard,navigation};\n})();', read('public/ops.js'))
            assert count == 1
            page.add_script_tag(content=source)
            page.evaluate("""morning=>{
                qa.state.lang='ar';qa.state.page='orders';qa.state.branches=[{id:'kps-2',branch_no:2,name:'فرع 2',city:'حفر الباطن'}];
                qa.state.user={id:'TEST-manager',role:'delivery_manager',brand_id:'kps',branch_ids:['kps-2'],
                    effective_permissions:['orders_view','orders_amend','orders_cancel','delivery_assign','tasks_view','chat_view'],
                    ...(morning?{delivery_context:{}}:{})};
                qa.navigation();
                document.querySelector('#login-shell').hidden=true;
                document.querySelector('#app-shell').hidden=false;
                document.querySelector('#session-label').textContent='مدير التوصيل — حساب اختبار';
                const order={id:'TEST-order',order_no:'TEST-0001',brand_id:'kps',branch_id:'kps-2',branch_name:'فرع 2',
                    service:'delivery',status:'accepted',preparation_status:'accepted',delivery_status:'pending',
                    payment_status:'unpaid',payment_method:'cash',version:7,total_halalas:3500,subtotal_halalas:3500,delivery_fee_halalas:0,
                    created_at:'2026-11-01T12:00:00Z',customer_name:'عميل اختبار',customer:{name:'عميل اختبار',address:'عنوان اختبار',phone:'0500000000'},
                    items:[{name:'قهوة اليوم',quantity:1,price_halalas:3500,total_halalas:3500}],
                    snapshot:{delivery_period:morning?'morning':'evening',delivery_workflow:morning?'branch_fulfillment':'manager_dispatch',items:[]}};
                document.querySelector('#content').innerHTML=qa.orderCard(order);
            }""", morning)
            page.evaluate('()=>document.fonts.ready')
            card = page.locator('.order-detail-card')
            assert card.evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
            geometry.append(card.evaluate('(el)=>({width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height,html:el.innerHTML})'))
            images.append(card.screenshot(path=str(output / f'journey-card-{version}-{morning}-{width}.png'), animations='disabled'))
            page.screenshot(path=str(output / f'journey-manager-{version}-{morning}-{width}.png'), full_page=True, animations='disabled')
            # Keep workflow before/after evidence beside the unchanged manager screen.
            page.add_style_tag(content='\n'.join(read(p) for p in ('public/assets/order-amendments.css','public/assets/order-interventions.css')))
            page.add_script_tag(content=read('public/assets/order-interventions.js'))
            data = payload('returned')
            data.update(requests=[],available_create_kinds=['cancel','problem'])
            page.evaluate("""async d=>{await KPSInterventions.open({oid:'TEST-order',user:{id:'TEST-manager',role:'delivery_manager'},api:async()=>d,mutation:async()=>({}),uploadEvidence:async()=>({id:'TEST-evidence'})});}""", data)
            assert page.locator('.intervention-dialog').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
            if version == 'after':
                assert page.get_by_role('button',name='تعذر الإرفاق — تصعيد للمدير',exact=True).count() == 1
            page.screenshot(path=str(output / f'journey-workflow-{version}-{morning}-{width}.png'), full_page=True, animations='disabled')
        finally:
            page.close()
    assert geometry[0] == geometry[1], 'Existing order card structure or geometry changed'
    assert images[0] == images[1], 'Existing manager order card pixels changed'
