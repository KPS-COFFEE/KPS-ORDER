"""Real manager workspace timing; only ops.js asset selects published baseline."""
import json,os,statistics,subprocess,time,socket,threading
import httpx,uvicorn
from pathlib import Path
import pytest
from app import auth,central_workdays,driver_readiness
from test_central_workdays import central
from test_enbishan_dual_brand import dual
from test_browser import chromium_browser
from manual_manager_workspace_performance import seed_large_workspace

@pytest.fixture
def manager_http(central,monkeypatch):
    # The dual fixture supplies actual scoped sessions in isolated PostgreSQL.
    # No OTP/provider configuration or business API response is fabricated.
    client=seed_large_workspace(central)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    monkeypatch.setenv('PUBLIC_BASE_URL',base);monkeypatch.setenv('APP_ALLOWED_ORIGINS',base)
    from app.main import app
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',lifespan='off'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(100):
        try:
            if httpx.get(base+'/health',trust_env=False,timeout=1).status_code==200:break
        except httpx.HTTPError:pass
        time.sleep(.05)
    else:pytest.fail('Isolated manager HTTP startup failed')
    cookies=[{'name':c.name,'value':c.value,'url':base} for c in client.cookies.jar]
    try:yield base,cookies
    finally:
        server.should_exit=True;thread.join(timeout=10)
        assert not thread.is_alive()

@pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser measurement')
@pytest.mark.parametrize('width',[390,1440])
def test_large_manager_workspace_baseline_candidate(manager_http,chromium_browser,monkeypatch,width):
    from playwright.sync_api import expect
    base,cookies=manager_http
    original_find=auth._find_session;observation=driver_readiness.ReadinessObservation
    baseline_script=subprocess.check_output(['git','show','9d4965fa9505e1c151fa80ea57be844ab6738455:public/ops.js'],text=True)
    def published_find(c,request):
        user=original_find(c,request)
        if user and 'operations_brands' in user and request.url.path!='/api/session':
            user['central_workday_drain']=central_workdays.drain_metadata(c,user)
        return user
    results={}
    for name,finder,wrapper in [('published_9d4965f',published_find,lambda c:c),('candidate_4145453',original_find,lambda c:c),('manager_followup',original_find,observation)]:
        monkeypatch.setattr(auth,'_find_session',finder);monkeypatch.setattr(driver_readiness,'ReadinessObservation',wrapper)
        runs=[]
        for sample in range(3):
            context=chromium_browser.new_context(viewport={'width':width,'height':1000})
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            try:
                context.add_cookies(cookies)
                if name=='published_9d4965f':
                    page.route('**/ops.js*',lambda route:route.fulfill(status=200,content_type='application/javascript',body=baseline_script))
                started=time.perf_counter();page.goto(base+'/ops',wait_until='domcontentloaded')
                expect(page.locator('#dispatch-orders article')).to_have_count(100,timeout=15000)
                expect(page.locator('#dispatch-drivers article')).to_have_count(21,timeout=15000)
                elapsed=(time.perf_counter()-started)*1000
                page.wait_for_timeout(150)
                requests=page.evaluate("performance.getEntriesByType('resource').filter(r=>r.name.includes('/api/')).map(r=>({path:new URL(r.name).pathname,duration_ms:Math.round(r.duration*100)/100,bytes:r.transferSize}))")
                runs.append({'workspace_ready_ms':round(elapsed,2),'api_request_count':len(requests),'api_requests':requests,'orders_visible':100,'drivers_visible':21})
                assert not errors,errors
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            finally:context.close()
        results[name]={'runs':runs,'median_ready_ms':round(statistics.median(r['workspace_ready_ms'] for r in runs),2)}
    results['scope']='Real Chromium emulation with real HTTP/auth/query responses;500 pending orders,21idle drivers,20KPSbranches+2Enbishanbranches. No business responses mocked; baseline replaces only actual published ops.js asset and removed auth drain. Local synthetic workload,not production/mobile device latency.'
    Path('test-artifacts').mkdir(exist_ok=True)
    Path(f'test-artifacts/manager-workspace-browser-{width}.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps({name:data['median_ready_ms'] for name,data in results.items() if isinstance(data,dict)}))
