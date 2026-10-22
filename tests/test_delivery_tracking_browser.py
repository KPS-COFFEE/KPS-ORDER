"""Use the actual customer asset with a synthetic API; no live GPS or paid requests."""
import json
import os
from pathlib import Path
import pytest
from test_experience_entry_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')
ROOT=Path(__file__).resolve().parents[1]
TOKEN='a'*43


def test_tracking_reload_freshness_expiry_and_explicit_eta(static_customer):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':390,'height':844})
        requests=[];mode={'state':'live'};directions=[]
        def response(route):
            requests.append(route.request.post_data_json)
            if mode['state']=='ended':data={'status':'ended','location':None,'gps':{'state':'missing'}}
            elif mode['state']=='stale':data={'status':'in_progress','location':None,'gps':{'state':'stale'}}
            else:
                data={'status':'on_the_way','location':{'lat':28.4,'lng':45.9},'gps':{'state':'live','updated_at':'2026-10-22T10:00:00Z','accuracy_m':10},'map':{'enabled':True,'public_token':'pk.synthetic','style':'mapbox://styles/mapbox/streets-v12'}}
                if route.request.post_data_json.get('route'):data['route_destination']={'lat':28.5,'lng':45.8}
            route.fulfill(content_type='application/json',body=json.dumps(data))
        page.route('**/api/delivery-tracking/view',response)
        def direction(route):
            directions.append(route.request.url)
            route.fulfill(content_type='application/json',body=json.dumps({'code':'Ok','routes':[{'duration':900}]}))
        page.route('https://api.mapbox.com/directions/**',direction)
        page.add_init_script('''window.mapboxgl={Map:class{addControl(){}resize(){}remove(){}},Marker:class{setLngLat(){return this}addTo(){return this}remove(){}},NavigationControl:class{}};''')
        page.goto(static_customer+'/delivery-track.html#'+TOKEN)
        expect(page.locator('#tracking-eta-button')).to_be_visible()
        assert not directions
        assert requests[0]=={'token':TOKEN}
        assert '#'+TOKEN in page.url
        page.reload()
        expect(page.locator('#tracking-eta-button')).to_be_visible()
        page.locator('#tracking-eta-button').click()
        expect(page.locator('#tracking-eta')).to_contain_text('15')
        assert len(directions)==1 and requests[-1]=={'token':TOKEN,'route':True}
        mode['state']='stale'
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator('#tracking-map')).to_be_hidden()
        expect(page.locator('#tracking-eta-button')).to_be_hidden()
        expect(page.locator('#tracking-eta')).to_have_text('')
        mode['state']='ended'
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator('#tracking-status')).to_contain_text('Tracking has ended')
        browser.close()


def test_invalid_token_never_requests_location(static_customer):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page();calls=[]
        page.route('**/api/delivery-tracking/view',lambda route:(calls.append(route.request.url),route.abort()))
        page.goto(static_customer+'/delivery-track.html#wrong')
        expect(page.locator('#tracking-status')).to_contain_text('Invalid tracking link')
        assert calls==[]
        browser.close()
