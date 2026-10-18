"""Targeted real-database checks for operational safeguards."""
import pytest
from app import db
from tests.test_operations import ops
from test_browser import _context, _owner_login, _staff, chromium_browser, synthetic_mail
from tests.browser_sms_helpers import sms_browser_app as live_app

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
    post('driver',path,{'status':'accepted','reason':'Driver cannot resolve own escalated problem'},status=403)
    post('manager',path,{'status':'accepted'},status=422)
    post('manager',path,{'status':'accepted','reason':'Manager contacted customer and approved continuing'})
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
@pytest.mark.parametrize('kind',['order','chat'])
def test_alert_sound_requires_gesture_repeats_and_stops(live_app,chromium_browser,language,kind):
    from playwright.sync_api import expect
    owner=_staff();context,page,errors=_context(chromium_browser,live_app)
    _owner_login(context,live_app,owner)
    context.add_init_script("localStorage.setItem('kps_ops_lang', '"+language+"');window.testGestureSeen=false;document.addEventListener('pointerdown',e=>{if(e.isTrusted)window.testGestureSeen=true},true);document.addEventListener('keydown',e=>{if(e.isTrusted)window.testGestureSeen=true},true);window.testBeeps=0;window.testContinuousStopped=0;window.AudioContext=class{constructor(){this.state='suspended';this.currentTime=0;this.destination={}}async resume(){if(!window.testGestureSeen||!navigator.userActivation.isActive)throw Error('Synthetic browser autoplay block');this.state='running'}createOscillator(){return {connect(){},disconnect(){},frequency:{value:0},start(){window.testBeeps++},stop(at){if(at===undefined){window.testContinuousStopped++;this.onended?.()}else queueMicrotask(()=>this.onended?.())}}}createGain(){return {connect(){},disconnect(){},gain:{setValueAtTime(){},exponentialRampToValueAtTime(){}}}}};")
    pending=[{'id':'synthetic-alert','order_no':123}]
    page.route('**/api/operations/alerts',lambda route:route.fulfill(json={'unassigned':pending if kind=='order' else [],'problems':[],'printing':[]}))
    page.route('**/api/conversations/alerts',lambda route:route.fulfill(json={'awaiting_reply':pending if kind=='chat' else []}))
    marker='123' if kind=='order' else ('Messages awaiting reply' if language=='en' else 'رسائل تنتظر الرد')
    tones=2
    try:
        page.clock.install();page.goto(live_app+'/ops')
        expect(page.locator('#operational-alerts')).to_contain_text(marker)
        # Playwright evaluates with CDP userGesture=true. Count only trusted
        # pointer/key input, not activation introduced by assertion polling.
        assert page.evaluate('window.testGestureSeen') is False
        assert page.evaluate('window.testBeeps')==0
        expect(page.locator('[data-action=alert-sound]')).to_be_hidden()
        # Any normal page gesture enables sound: no employee sound settings.
        page.locator('#page-heading').click()
        assert page.evaluate('window.testGestureSeen') is True
        expect(page.locator('#alert-audio-blocked')).to_be_hidden()
        page.wait_for_function('(expected) => window.testBeeps === expected', arg=tones)
        page.clock.run_for(31000)
        page.wait_for_function('(expected) => window.testBeeps === expected', arg=tones*2)
        page.clock.run_for(31000)
        page.wait_for_function('(expected) => window.testBeeps === expected', arg=tones*2+1)
        pending.clear();page.clock.run_for(16000)
        expect(page.locator('#operational-alerts')).not_to_contain_text(marker)
        assert page.evaluate('window.testContinuousStopped')==1
        count=page.evaluate('window.testBeeps');page.clock.run_for(61000)
        assert page.evaluate('window.testBeeps')==count
        assert not errors
    finally:context.close()


@pytest.mark.postgres
def test_message_alerts_require_reply_not_read_and_follow_scope(ops):
    actor,post,order,_=ops
    cid=post('customer','/api/conversations',{'order_id':order()})['conversation']['id']
    path=f'/api/conversations/{cid}'
    def pending(who):
        r=actor(who).get('/api/conversations/alerts')
        assert r.status_code==200,r.text
        return [v['id'] for v in r.json()['awaiting_reply']]
    assert pending('manager')==[]
    sent=post('customer',path+'/messages',{'text':'Need help'})['message']
    assert pending('manager')==[cid]
    assert pending('owner')==[cid]
    assert pending('branch')==pending('other')==[]
    assert actor('driver').get('/api/conversations/alerts').status_code==403
    assert actor('customer').get('/api/conversations/alerts').status_code==403
    post('manager',path+'/read',{'through_seq':sent['seq']})
    assert pending('manager')==[cid]
    post('manager',path+'/messages',{'text':'We are checking'})
    assert pending('manager')==pending('owner')==[]
    post('customer',path+'/messages',{'text':'One more question'})
    assert pending('manager')==[cid]
    with db.tx() as c:
        c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'")
    assert pending('manager')==[]


@pytest.mark.postgres
def test_message_alert_follows_branch_to_delivery_transfer(ops):
    actor,post,order,_=ops
    cid=post('customer','/api/conversations',{'order_id':order('pickup')})['conversation']['id']
    post('customer',f'/api/conversations/{cid}/messages',{'text':'Delivery complaint'})
    assert actor('branch').get('/api/conversations/alerts').json()['awaiting_reply']
    assert not actor('manager').get('/api/conversations/alerts').json()['awaiting_reply']
    post('branch',f'/api/conversations/{cid}/transfer',{'target':'delivery','reason':'Delivery complaint'})
    assert not actor('branch').get('/api/conversations/alerts').json()['awaiting_reply']
    assert actor('manager').get('/api/conversations/alerts').json()['awaiting_reply']
