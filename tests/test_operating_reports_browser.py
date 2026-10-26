"""Exercise the actual reporting widget on phone and desktop, including export."""
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from test_operating_reports import example
from app.operating_reports import make_pdf


@pytest.mark.parametrize('width',[390,1440])
def test_widget_mobile_accounting_status_refresh_and_export_filename(width,tmp_path):
    public=Path(__file__).resolve().parents[1]/'public'
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(public)))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    data=example();data.update(can_day=True,generated_at='2026-10-26T01:00:00+03:00')
    data['accounting'].update(percent=60,completed_steps=3,total_steps=5,overdue=1,late=0)
    source=make_pdf(data)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':width,'height':950},accept_downloads=True)
            page.route('**/qa-report',lambda route:route.fulfill(content_type='text/html',body='''<!doctype html><html lang="ar" dir="rtl"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/assets/operating-reports.css"><main id="report"></main><script src="/assets/operating-reports.js"></script></html>'''))
            page.route('**/api/operating-reports?*',lambda route:route.fulfill(content_type='application/json',body=json.dumps(data)))
            page.route('**/api/operating-reports/export/pdf?*',lambda route:route.fulfill(content_type='application/pdf',body=source))
            page.goto(f'http://127.0.0.1:{server.server_port}/qa-report')
            page.evaluate('''async()=>{await window.KPSOperatingReports.mount(document.querySelector('#report'),{api:async url=>await(await fetch(url)).json()});}''')
            page.get_by_text('60٪',exact=True).wait_for()
            assert page.locator('.or-stamp').count()==0
            assert page.locator('.or-late').count()==1
            page.locator('summary').first.click()
            assert page.locator('details[open] tbody tr').count()==2
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            with page.expect_download() as download:
                page.get_by_role('button',name='حفظ أو مشاركة PDF').click()
            assert download.value.suggested_filename==data['filename']
            data['accounting']=example(True)['accounting']
            page.get_by_role('button',name='تحديث',exact=True).click()
            page.get_by_text('100٪',exact=True).wait_for()
            assert 'محمد أحمد' in page.locator('.or-stamp').inner_text()
            assert 'AC-1025' in page.locator('.or-stamp').inner_text()
            assert page.locator('.or-late').count()==0
            assert 'أُنجزت بعد الموعد' in page.locator('.or-accounting').inner_text()
            page.screenshot(path=str(tmp_path/f'operating-report-{width}.png'),full_page=True)
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
