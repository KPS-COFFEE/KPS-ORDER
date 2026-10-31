"""Real Chromium UI-only synthetic fixture acceptance, never database evidence.
Run from the repository root. No production endpoints are contacted.
"""
import functools
import json
import os
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=Path(os.environ.get('KPS_HOURS_SCREENSHOTS','/tmp/kps-driver-hours-ui'))


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    metric=dict(shift_seconds=28800, actual_seconds=25200, delivery_seconds=10800,
                waiting_seconds=14400, duty_seconds=28800, non_delivery_seconds=3600,
                work_utilization_percent=87.5, active_work_seconds=0, drivers_needing_review=0,
                break_seconds=3600, other_work_seconds=0,
                unclassified_seconds=0,trip_count=3,order_count=7,order_activity_seconds=21600,active_delivery_seconds=0,remaining_shift_seconds=7200,order_utilization_percent=75,missing_order_timing_count=0)
    fixture=dict(start='2026-10-18',end='2026-10-18',operational_date='2026-10-18',
                 operational_day_start='05:30',timezone='Asia/Riyadh',totals=metric,
                 branches=[dict(id='fixture-branch',name='فرع اختباري')],
                 drivers=[dict(driver_id='fixture-driver',name='سائق تجريبي',shift_status='open',**metric,
                               trips=[dict(id='fixture-trip',departure_branch_id='fixture-branch',
                                           return_branch_id='fixture-branch',departed_at='2026-10-18T13:00:00Z',
                                           returned_at='2026-10-18T14:00:00Z',duration_seconds=3600,order_count=2)])])
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(SimpleHTTPRequestHandler,directory=str(ROOT/'public')))
    Thread(target=server.serve_forever,daemon=True).start()
    checks=[]
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('KPS_CHROMIUM') or None,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
            page=browser.new_page(viewport=dict(width=1440,height=1050))
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            session_checks=[]
            def session(route):
                session_checks.append(route.request.url)
                route.fulfill(json={'user':{'id':'fixture-owner','role':'owner','brand_id':'kps',
                    'branch_ids':['fixture-branch'],'effective_permissions':['driver_hours_reports']},
                    'csrf_token':'synthetic-ui-fixture-csrf'})
            # This remains an explicit UI-only fixture. The real fail-closed access
            # heartbeat must receive a valid synthetic session before rendering;
            # the companion DB/browser tests exercise actual server revocation.
            page.route('**/api/session',session)
            page.route('**/api/driver-work-hours/report?*',lambda route:route.fulfill(json=fixture))
            page.goto(f'http://127.0.0.1:{server.server_port}/driver-hours.html')
            page.locator('#hours-kpis article').first.wait_for()
            page.evaluate("""() => {let e=document.createElement('p'); e.textContent='بيانات صناعية لاختبار الواجهة فقط · Synthetic UI fixture';e.style='background:#fff0e5;padding:10px;text-align:center';document.querySelector('main').prepend(e)}""")
            assert page.locator('#hours-kpis article').count()==4
            boxes=page.locator('#hours-kpis article').all()
            assert len({round(e.bounding_box()['y']) for e in boxes})==1
            checks.append('desktop_four_cards_one_row')
            page.screenshot(path=str(OUT/'driver-hours-desktop-ar.png'),full_page=True)
            page.set_viewport_size(dict(width=800,height=1100))
            assert len({round(e.bounding_box()['y']) for e in boxes})==2
            checks.append('tablet_two_cards_per_row')
            page.screenshot(path=str(OUT/'driver-hours-tablet-ar.png'),full_page=True)
            page.set_viewport_size(dict(width=390,height=1000))
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            checks.append('mobile_no_horizontal_overflow')
            page.screenshot(path=str(OUT/'driver-hours-mobile-ar.png'),full_page=True)
            page.click('#hours-language')
            page.wait_for_function("document.documentElement.lang==='en' && document.querySelector('#hours-kpis h2').textContent==='Completed delivery work hours'")
            assert page.locator('html').get_attribute('dir')=='ltr'
            checks.append('english_ltr')
            page.screenshot(path=str(OUT/'driver-hours-mobile-en.png'),full_page=True)
            page.select_option('select[name=period]','week')
            page.click('button[type=submit]')
            page.wait_for_timeout(150)
            assert 'end=2026-10-24' in page.locator('#hours-xlsx').get_attribute('href')
            checks.append('weekly_seven_operational_dates')
            page.select_option('select[name=period]','month')
            page.click('button[type=submit]')
            page.wait_for_timeout(150)
            assert 'start=2026-10-01' in page.locator('#hours-xlsx').get_attribute('href')
            assert 'end=2026-10-31' in page.locator('#hours-xlsx').get_attribute('href')
            checks.append('monthly_calendar_period')
            assert session_checks, 'The real report access heartbeat must execute'
            assert not errors, errors
            browser.close()
        print(json.dumps(dict(kind='UI synthetic fixture; no database integration claim',PASS=checks,screenshots=str(OUT))))
    finally:server.shutdown()


if __name__=='__main__':main()
