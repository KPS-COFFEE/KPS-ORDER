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
    post('driver','/api/shifts',opening,status=409)
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
from test_browser import _context, _owner_login, _staff, _capture, chromium_browser, live_app, synthetic_mail

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
        form.locator('[name=inspection_fuel]').fill('70');form.locator('[name=fuel_reason]').select_option('aldrees_limit_reached')
        for key in CHECKS:form.locator('[name=inspection_'+key+']').select_option('ok')
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
        assert not errors and not merrors
    finally:ctx.close();mctx.close()

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
