"""Synthetic read-only UI acceptance; no production service or identity."""
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright, expect
from test_owner_dashboard_browser import browser_launch
ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('width',[390,1440])
def test_driver_fuel_report_filters_unknown_values_and_readable_mobile(width):
    with sync_playwright() as p:
        browser=browser_launch(p)
        page=browser.new_page(viewport={'width':width,'height':900})
        page.set_content('<!doctype html><html lang="ar" dir="rtl"><meta name="viewport" content="width=device-width, initial-scale=1"><div id="page-heading"></div><main id="content"></main></html>')
        page.add_style_tag(content=(ROOT/'public/ops.css').read_text()+'\n'+(ROOT/'public/assets/driver-fuel-report.css').read_text())
        page.add_script_tag(content=(ROOT/'public/assets/driver-fuel-report.js').read_text())
        page.evaluate('''async () => {
         window.calls=[];
         const opt={today:'2026-10-12',drivers:[{id:'d1',display_name:'سائق تجريبي'}],vehicles:[{id:'v1',plate:'TEST 1'}],cities:['حفر الباطن']};
         const row={operating_day:'2026-10-12',driver_name:'سائق تجريبي',plate:'TEST 1',cities:['حفر الباطن'],start_odometer:1000,end_odometer:null,actual_distance:null,liters:0,fuel_record_missing:true,fuel_amount_halalas:null,record_status:'open'};
         const data={rows:[row],total:1,summary:{distance_km:0,distance_missing_records:1,liters:0,fuel_record_missing:1,fuel_amount_known_halalas:0,fuel_amount_halalas:null},note:'القيمة من مبالغ التعبئة المسجلة فقط'};
         await KPSDriverFuelReport.open({api:async url=>{calls.push(url);return url.endsWith('/options')?opt:data},tr:(ar,en)=>ar,esc:x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),heading:title=>document.querySelector('#page-heading').textContent=title,lang:'ar'});
        }''')
        expect(page.get_by_text('غير مسجل',exact=True)).to_be_visible()
        expect(page.get_by_text('الإجمالي غير مكتمل — توجد بيانات غير مسجلة')).to_be_visible()
        page.locator('select[name=driver_id]').select_option('d1')
        page.locator('select[name=city]').select_option('حفر الباطن')
        page.get_by_role('button',name='عرض التقرير').click()
        expect(page.get_by_role('button',name='عرض التقرير')).to_be_enabled()
        calls=page.evaluate('window.calls')
        assert 'driver_id=d1' in calls[-1] and 'city=' in calls[-1]
        assert page.locator('html').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
        assert all('/api/fleet/driver-report' in url for url in calls)
        browser.close()
