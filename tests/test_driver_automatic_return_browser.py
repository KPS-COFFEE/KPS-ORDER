"""Real Chromium rendering of the shipped driver automatic-return panel.

Only the authenticated API boundary is synthetic; DOM, selects, disclosure,
navigation links and the manual fallback execute the actual ops.js function.
"""
import os
from pathlib import Path

import pytest
from test_driver_gps_browser import gps_page

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit Chromium run')


@pytest.mark.parametrize('lang,width', [('ar', 390), ('en', 1440)])
@pytest.mark.parametrize('status', ['awaiting_deliveries', 'confirming_return', 'awaiting_location', 'awaiting_return', 'needs_review', 'missing_pin'])
def test_real_driver_automatic_return_panel(gps_page, lang, width, status):
    from playwright.sync_api import expect
    page = gps_page
    page.set_viewport_size({'width': width, 'height': 900})
    page.evaluate("""({lang,status})=>{
      document.body.innerHTML='<main id="content"></main>';
      document.documentElement.lang=lang;document.documentElement.dir=lang==='ar'?'rtl':'ltr';
      window.$=s=>document.querySelector(s);window.tr=(ar,en)=>lang==='ar'?ar:en;
      window.esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
      window.branchLabel=b=>typeof b==='string'?b:b.name;window.datetime=String;
      window.notice=(s,kind='')=>'<p class="notice '+kind+'">'+esc(s)+'</p>';
      window.state={user:{id:'driver',branch_ids:['departure','return']},branches:[{id:'departure',name:'Departure'},{id:'return',name:'Selected Return'},{id:'foreign',name:'SECRET'}]};
      window.trip={id:'automatic-trip',departure_branch_id:'departure',departed_at:'2026-10-29T13:00:00Z',planned_return_branch_id:'return',auto_return:{enabled:true,status,return_branch_id:'return',return_branch_name:'Selected Return',...(status==='missing_pin'?{}:{return_location:{lat:28.4,lng:45.9}})}};
      window.api=async()=>({trip:window.trip});window.requests=[];
      window.mutation=async(path,body)=>{window.requests.push({path,body});return {trip:{...window.trip,returned_at:'2026-10-29T14:00:00Z'}};};
      window.setStatus=()=>{};window.loadPage=async()=>{window.reloaded=true;};
      Object.defineProperty(navigator,'geolocation',{value:undefined,configurable:true});
    }""", {'lang': lang, 'status': status})
    source = (ROOT / 'public/ops.js').read_text()
    helper = source[source.index('  async function renderDriverTripPanel'):source.index('  async function driverContact')]
    page.add_script_tag(content=helper)
    page.evaluate("renderDriverTripPanel({id:'shift',status:'open',branch_ids:['departure','return']},[])")
    host = page.locator('.driver-trip-panel')
    expect(host).to_contain_text('Selected Return')
    expect(host).not_to_contain_text('SECRET')
    if status == 'missing_pin':
        expect(host.locator('a')).to_have_count(0)
    else:
        expect(host.locator('a')).to_have_attribute('href', 'https://www.google.com/maps/dir/?api=1&destination=28.4%2C45.9')
    expected = {
        'awaiting_deliveries': ('أكمل الطلبات أولًا', 'Complete your deliveries first'),
        'confirming_return': ('لم تُسجل العودة بعد', 'return is not recorded yet'),
        'awaiting_location': ('تعذر تأكيد العودة تلقائيًا', 'Automatic return cannot be confirmed'),
        'needs_review': ('تعذر تأكيد العودة تلقائيًا', 'Automatic return cannot be confirmed'),
    }.get(status, ('تُسجل العودة تلقائيًا', 'Return is recorded automatically'))
    expect(host).to_contain_text(expected[0 if lang == 'ar' else 1])
    # Merely rendering a GPS status must never mark a return on the server.
    assert page.evaluate('window.requests.length') == 0
    expect(host.locator('form')).not_to_be_visible()
    host.locator('summary').click()
    expect(host.locator('select option')).to_have_count(1)
    expect(host.locator('select')).to_have_value('return')
    host.locator('button[type=submit]').click()
    page.wait_for_function('window.reloaded===true')
    assert page.evaluate('window.requests') == [{'path': '/api/driver-work-hours/automatic-trip/return', 'body': {'branch_id': 'return'}}]
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
