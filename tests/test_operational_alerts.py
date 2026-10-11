"""Targeted real-database checks for operational safeguards."""
import pytest
from app import db
from tests.test_operations import ops
from test_browser import _context, _owner_login, _staff, chromium_browser, live_app

@pytest.mark.postgres
def test_unassigned_contact_attempts_and_scope(ops):
    actor,post,order,shifts=ops
    _,driver=shifts();oid=order()
    assert actor('manager').get('/api/operations/alerts').json()['unassigned'][0]['id']==oid
    assert not actor('other').get('/api/operations/alerts').json()['unassigned']
    assert actor('driver').get('/api/operations/alerts').status_code==403
    tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
    assert not actor('manager').get('/api/operations/alerts').json()['unassigned']
    path=f'/api/tasks/{tid}/transition'
    data={'status':'problem','contact_method':'call','reason':'Customer did not answer actual call'}
    post('driver',path,data,key='one-attempt');post('driver',path,data,key='one-attempt')
    post('driver',path,dict(data,contact_method='message'),key='second-attempt')
    assert actor('manager').get('/api/operations/alerts').json()['problems'][0]['id']==oid
    post('driver',path,{'status':'cancelled','reason':'No answer'},status=403)
    post('driver2',path,data,status=403)
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='customer.no_answer'",(oid,)).fetchone()['n']==2
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='ready'
    post('driver',path,{'status':'accepted'})
    assert not actor('manager').get('/api/operations/alerts').json()['problems']

@pytest.mark.postgres
def test_print_alerts_keep_order_and_isolate_scope(ops):
    actor,post,order,shifts=ops;oid=order()
    with db.tx() as c:
        c.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,created_by) VALUES('alert-printer','ops-brand','ops-branch','Test','ops-owner')")
        for status in ('queued','claimed','uncertain','submitted'):
            c.execute("INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,order_id,kind,status,payload,requested_by,created_at,claimed_at) VALUES(%s,'ops-brand','ops-branch','alert-printer',%s,'order',%s,'{}','ops-owner',now()-interval '3 minutes',now()-interval '2 minutes')",('alert-'+status,oid,status))
    result=actor('manager').get('/api/operations/alerts')
    assert result.status_code==200,result.text
    assert {r['status'] for r in result.json()['printing']}=={'queued','claimed','uncertain'}
    assert not actor('other').get('/api/operations/alerts').json()['printing']
    with db.tx() as c:
        assert c.execute('SELECT id FROM orders WHERE id=%s',(oid,)).fetchone()

@pytest.mark.postgres
def test_stock_pause_is_branch_scoped_and_driver_forbidden(ops):
    actor,post,order,shifts=ops
    with db.tx() as c:
        c.execute("INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES('alert-product','ops-brand','Test','Test',1000,'{}')")
    path='/api/branches/ops-branch/products/alert-product/availability'
    post('driver',path,{'available':False,'reason':'Test'},status=403)
    post('branch',path,{'available':False,'reason':'Sold out'})
    with db.tx() as c:
        assert c.execute("SELECT settings FROM products WHERE id='alert-product'").fetchone()['settings']['unavailable_branch_ids']==['ops-branch']
    post('other',path,{'available':True},status=404)
    post('branch',path,{'available':True,'reason':'Restocked'})
    with db.tx() as c:
        assert c.execute("SELECT settings FROM products WHERE id='alert-product'").fetchone()['settings']['unavailable_branch_ids']==[]

@pytest.mark.postgres
@pytest.mark.parametrize('language',['ar','en'])
def test_alert_sound_requires_gesture_repeats_and_stops(live_app,chromium_browser,language):
    from playwright.sync_api import expect
    owner=_staff();context,page,errors=_context(chromium_browser,live_app)
    _owner_login(context,live_app,owner)
    context.add_init_script("localStorage.setItem('kps_ops_lang', '"+language+"');window.testBeeps=0;window.AudioContext=class{constructor(){this.state='suspended';this.currentTime=0;this.destination={}}async resume(){this.state='running'}createOscillator(){return {connect(){},frequency:{value:0},start(){window.testBeeps++},stop(){}}}createGain(){return {connect(){},gain:{setValueAtTime(){},exponentialRampToValueAtTime(){}}}}};")
    pending=[{'id':'synthetic-alert','order_no':123}]
    page.route('**/api/operations/alerts',lambda route:route.fulfill(json={'unassigned':pending,'problems':[],'printing':[]}))
    try:
        page.clock.install();page.goto(live_app+'/ops')
        expect(page.locator('#operational-alerts')).to_contain_text('123')
        assert page.evaluate('window.testBeeps')==0
        page.locator('[data-action=alert-sound]').click()
        expect(page.locator('[data-action=alert-sound]')).to_contain_text('Sound enabled' if language=='en' else 'الصوت مفعل')
        assert page.evaluate('window.testBeeps')==1
        page.clock.run_for(31000)
        assert page.evaluate('window.testBeeps')>=2
        pending.clear();page.clock.run_for(16000)
        expect(page.locator('#operational-alerts')).not_to_contain_text('123')
        count=page.evaluate('window.testBeeps');page.clock.run_for(31000)
        assert page.evaluate('window.testBeeps')==count
        assert not errors
    finally:context.close()
