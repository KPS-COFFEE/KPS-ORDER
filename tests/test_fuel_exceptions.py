"""Owner FULL gate: real PostgreSQL authorization, immutable readings and one-use approval."""
import pytest
from app import db
from tests.test_operations import ops
from tests.test_shift_vehicle import inspection

@pytest.mark.postgres
@pytest.mark.parametrize('reason',['aldrees_unavailable','aldrees_balance_empty','aldrees_limit_reached'])
def test_full_gate_manager_only_exact_readings_and_single_use(ops,reason):
    actor,post,order,shifts=ops
    mid=post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})['shift']['id']
    opening={'kind':'driver','manager_shift_id':mid,'vehicle_inspection':inspection(1000,65)}
    request={'phase':'start','manager_shift_id':mid,'reason':reason,'fuel_percent':65,'odometer_km':1000}
    post('driver','/api/fuel-exceptions',{**request,'reason':'other'},status=422)
    r=post('driver','/api/fuel-exceptions',request,key='fuel-request-once')
    assert post('driver','/api/fuel-exceptions',request,key='fuel-request-once')==r
    path='/api/fuel-exceptions/'+r['id']
    post('driver',path,{'decision':'approved'},status=403)
    post('owner',path,{'decision':'approved'},status=403)
    post('other',path,{'decision':'approved'},status=403)
    assert not actor('driver2').get('/api/fuel-exceptions').json()['requests']
    opening['vehicle_inspection']['fuel_exception_id']=r['id']
    post('driver','/api/shifts',opening,status=409)
    post('manager',path,{'decision':'approved'},key='fuel-approve-once')
    post('manager',path,{'decision':'approved'},key='fuel-approve-once')
    opening['vehicle_inspection']['fuel_percent']=64
    post('driver','/api/shifts',opening,status=409)
    opening['vehicle_inspection']['fuel_percent']=65
    sid=post('driver','/api/shifts',opening,key='fuel-open-once')['shift']['id']
    assert post('driver','/api/shifts',opening,key='fuel-open-once')['shift']['id']==sid
    # The fuel scenario starts closing only after explicit early-close approval.
    early=post('driver','/api/driver-closure/'+sid+'/request',{'reason':'Synthetic end-of-fuel scenario'})
    post('manager','/api/driver-closure/'+sid+'/decision',{'decision':'approved','request_id':early['authorization']['id'],'reason':'Approved synthetic closure'})
    post('driver','/api/shifts/'+sid+'/closing')
    close={'terminal_total_halalas':0,'counted_halalas':0,'handover_halalas':0,'vehicle_inspection':inspection(1025,50),'handover':{'recipient_user_id':'ops-manager','to_shift_id':mid,'reference':'FUEL-TEST'}}
    post('driver','/api/shifts/'+sid+'/submit',close,status=409)
    close['vehicle_inspection']['fuel_exception_id']=r['id']
    post('driver','/api/shifts/'+sid+'/submit',close,status=409)
    end=post('driver','/api/fuel-exceptions',{**request,'phase':'end','shift_id':sid,'fuel_percent':50,'odometer_km':1025})
    post('manager','/api/fuel-exceptions/'+end['id'],{'decision':'approved'})
    close['vehicle_inspection']['fuel_exception_id']=end['id']
    post('driver','/api/shifts/'+sid+'/submit',close,key='fuel-submit-once')
    post('driver','/api/shifts/'+sid+'/submit',close,key='fuel-submit-once')
    with db.tx() as c:
        rows=c.execute('SELECT * FROM fuel_exceptions WHERE driver_id=%s',('ops-driver',)).fetchall()
        assert len(rows)==2 and all(x['status']=='consumed' and x['consumed_shift_id']==sid and x['decided_by']=='ops-manager' for x in rows)
        log=c.execute('SELECT vehicle_log FROM shifts WHERE id=%s',(sid,)).fetchone()['vehicle_log']
        assert log['start']['fuel_percent']==65 and log['end']['fuel_percent']==50
        assert log['start']['odometer_km']==1000 and log['end']['odometer_km']==1025

@pytest.mark.postgres
def test_full_needs_no_exception_and_delegation_cannot_bypass(ops):
    actor,post,order,shifts=ops
    mid,sid=shifts()
    post('manager','/api/shifts/'+sid+'/driver-close',{'terminal_total_halalas':0,'counted_halalas':0,'received_halalas':0,'confirmed':True,'exception_reason':'Phone unavailable'},status=422)
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='open'
        assert c.execute('SELECT count(*) n FROM fuel_exceptions').fetchone()['n']==0

import os
from test_browser import _context, _owner_login, _staff, _capture, chromium_browser, synthetic_mail
from tests.browser_sms_helpers import sms_browser_app as live_app

@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Real isolated Chromium only')
@pytest.mark.parametrize('language',['ar','en'])
def test_mobile_full_exception_request_and_manager_approval(live_app,chromium_browser,language):
    from playwright.sync_api import expect
    from app.shift_vehicle import CHECKS
    manager=_staff('delivery_manager');driver=_staff('driver');mid='test-fuel-manager'
    with db.tx() as c:
        c.execute("UPDATE branches SET settings=settings-'delivery_hours' WHERE id='kps-branch-1'")
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps','[\"kps-branch-1\"]')",(mid,manager['id']))
    ctx,page,errors=_context(chromium_browser,live_app);_owner_login(ctx,live_app,driver)
    mctx,mpage,merrors=_context(chromium_browser,live_app);_owner_login(mctx,live_app,manager)
    try:
        page.set_viewport_size({'width':390,'height':844});page.goto(live_app+'/ops')
        if language=='en':page.get_by_role('button',name='English',exact=True).click()
        page.locator('#navigation [data-page=shifts]').click();page.locator('[data-action=open-shift]').click()
        form=page.locator('#modal form');form.locator('[name=manager_shift_id]').select_option(mid)
        form.locator('[name=inspection_odometer]').fill('1000');form.locator('[name=fuel_full]').select_option('no')
        form.locator('[name=inspection_fuel]').fill('70')
        for key in CHECKS:form.locator('[name=inspection_'+key+']').select_option('ok')
        expect(form.locator('.fuel-start-guidance')).to_be_visible()
        form.locator('button[type=submit]').click();expect(page.locator('#modal')).not_to_be_visible()
        page.locator('#navigation [data-page=tasks]').click()
        expect(page.locator('[data-fuel-ready]')).to_be_visible()
        page.locator('[data-fuel-ready]').click()
        form=page.locator('#modal form')
        form.locator('[name=inspection_odometer]').fill('1005');form.locator('[name=fuel_full]').select_option('no')
        form.locator('[name=inspection_fuel]').fill('65');form.locator('[name=fuel_reason]').select_option('aldrees_limit_reached')
        form.locator('[data-action=fuel-request]').click()
        expect(form.locator('.fuel-status')).to_contain_text('بانتظار' if language=='ar' else 'Waiting')
        _capture(page,'TEST_ONLY_fuel_pending_'+language)
        mpage.goto(live_app+'/ops')
        if language=='en':mpage.get_by_role('button',name='English',exact=True).click()
        mpage.locator('[data-action=fuel-inbox]').click();mpage.locator('[data-action=fuel-approve]').click()
        expect(mpage.locator('[data-action=fuel-approve]')).to_have_count(0)
        form.locator('[data-action=fuel-check]').click()
        expect(form.locator('.fuel-status')).to_contain_text('تمت الموافقة' if language=='ar' else 'Approved')
        _capture(page,'TEST_ONLY_fuel_approved_'+language)
        form.locator('button[type=submit]').click();expect(page.locator('#modal')).not_to_be_visible()
        with db.tx() as c:
            r=c.execute('SELECT status,consumed_shift_id FROM fuel_exceptions WHERE driver_id=%s',(driver['id'],)).fetchone()
            assert r['status']=='consumed' and r['consumed_shift_id']
            log=c.execute('SELECT vehicle_log FROM shifts WHERE id=%s',(r['consumed_shift_id'],)).fetchone()['vehicle_log']
            assert log['start']['fuel_percent']==70 and log['fuel_readiness']['fuel_percent']==65
        assert not errors and not merrors
    finally:ctx.close();mctx.close()

@pytest.mark.postgres
@pytest.mark.parametrize('exception',[False,True])
def test_low_fuel_opens_shift_but_blocks_all_assignment_until_ready(ops,exception):
    actor,post,order,shifts=ops
    mid=post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})['shift']['id']
    body={'kind':'driver','manager_shift_id':mid,'vehicle_inspection':inspection(1000,40)}
    s=post('driver','/api/shifts',body,key='open-for-refuel')['shift'];sid=s['id']
    # available is the independent manager pause flag, not fuel readiness.
    assert s['status']=='open' and s['available'] is True and s['vehicle_log']['awaiting_refuel']
    roster=actor('manager').get('/api/operations/drivers').json()['drivers']
    driver=next(row for row in roster if row['id']=='ops-driver')
    assert driver['availability']=='waiting_for_fuel' and driver['assignment_eligible'] is False
    assert driver['eligible_branch_ids']==[]
    assert post('driver','/api/shifts',body,key='open-for-refuel')['shift']['id']==sid
    oid=order()
    post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':sid},status=409)
    post('manager','/api/shifts/'+sid+'/availability',{'available':True},status=409)
    # The fuel gate remains authoritative even if an unrelated workflow flips available.
    with db.tx() as c:c.execute('UPDATE shifts SET available=true WHERE id=%s',(sid,))
    post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':sid},status=409)
    ready={'odometer_km':1005,'fuel_percent':100}
    post('driver2','/api/fuel-exceptions/shifts/'+sid+'/ready',ready,status=403)
    post('driver','/api/fuel-exceptions/shifts/'+sid+'/ready',ready,status=409)
    if exception:
        r=post('driver','/api/fuel-exceptions',{'phase':'activation','shift_id':sid,'manager_shift_id':mid,'reason':'aldrees_unavailable','fuel_percent':35,'odometer_km':1005})
        ready.update(fuel_percent=35,fuel_exception_id=r['id'])
        post('driver','/api/fuel-exceptions/shifts/'+sid+'/ready',ready,status=409)
        post('manager','/api/fuel-exceptions/'+r['id'],{'decision':'approved'})
    else:
        post('driver','/api/shifts/'+sid+'/refuel',{'liters':'20','odometer_km':1005})
    post('driver','/api/fuel-exceptions/shifts/'+sid+'/ready',{**ready,'odometer_km':999},status=409 if exception else 422)
    post('driver','/api/fuel-exceptions/shifts/'+sid+'/ready',ready,key='fuel-ready-once')
    post('driver','/api/fuel-exceptions/shifts/'+sid+'/ready',ready,key='fuel-ready-once')
    post('driver','/api/fuel-exceptions/shifts/'+sid+'/ready',ready,status=200 if exception else 409)
    post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':sid})
    with db.tx() as c:
        log=c.execute('SELECT vehicle_log FROM shifts WHERE id=%s',(sid,)).fetchone()['vehicle_log']
        assert log['start']['fuel_percent']==40 and log['start']['odometer_km']==1000
        assert not log['awaiting_refuel'] and log['fuel_readiness']['odometer_km']==1005
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='fuel.ready' AND entity_id=%s",(sid,)).fetchone()['n']==1

@pytest.mark.postgres
def test_rejection_vehicle_change_and_revoked_scope_fail_closed(ops):
    actor,post,order,shifts=ops
    mid=post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})['shift']['id']
    body={'phase':'start','manager_shift_id':mid,'reason':'aldrees_balance_empty','fuel_percent':40,'odometer_km':1000}
    r=post('driver','/api/fuel-exceptions',body)
    post('manager','/api/fuel-exceptions/'+r['id'],{'decision':'rejected'})
    opening={'kind':'driver','manager_shift_id':mid,'vehicle_inspection':{**inspection(1000,40),'fuel_exception_id':r['id']}}
    post('driver','/api/shifts',opening,status=409)
    r=post('driver','/api/fuel-exceptions',body)
    post('manager','/api/fuel-exceptions/'+r['id'],{'decision':'approved'},key='scope-approved')
    opening['vehicle_inspection']['fuel_exception_id']=r['id']
    with db.tx() as c:c.execute("UPDATE users SET vehicle='{\"plate\":\"1234 ABC\"}' WHERE id='ops-driver'")
    post('driver','/api/shifts',opening,status=409)
    with db.tx() as c:
        c.execute("UPDATE users SET vehicle='{}' WHERE id='ops-driver'")
        c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'")
    post('manager','/api/fuel-exceptions/'+r['id'],{'decision':'approved'},key='scope-approved',status=403)
    post('driver','/api/shifts',opening,status=409)
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM shifts WHERE user_id='ops-driver'").fetchone()['n']==0
        assert c.execute('SELECT status FROM fuel_exceptions WHERE id=%s',(r['id'],)).fetchone()['status']=='approved'
