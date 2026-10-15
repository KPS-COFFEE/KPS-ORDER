"""Real shift inspection, refuelling, authorization and saved-report acceptance."""
from uuid import uuid4
import pytest
from app import db, shift_vehicle
from tests.test_operations import ops, delivered, close_driver


def inspection(km=1000,fuel=100):
    return dict(odometer_km=km,fuel_percent=fuel,checks={k:'ok' for k in shift_vehicle.CHECKS},notes='')

@pytest.mark.postgres
def test_inspection_required_readings_refuel_replay_and_report(ops):
    actor,post,order,shifts=ops
    manager=post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})['shift']['id']
    payload={'kind':'driver','manager_shift_id':manager}
    result=actor('driver').post('/api/shifts',json=payload,headers={'Idempotency-Key':uuid4().hex})
    assert result.status_code==422 and result.json()['detail']['code']=='VEHICLE_INSPECTION_REQUIRED'
    payload['vehicle_inspection']=inspection()
    driver=post('driver','/api/shifts',payload)['shift']['id']
    path='/api/shifts/'+driver+'/refuel'
    first=post('driver',path,{'liters':'10.25','odometer_km':1005},key='fuel-once')
    assert post('driver',path,{'liters':'10.25','odometer_km':1005},key='fuel-once')==first
    post('driver',path,{'liters':'4.50','odometer_km':1010})
    for data in ({'liters':0,'odometer_km':1010},{'liters':'NaN','odometer_km':1010},{'liters':5,'odometer_km':999}):
        post('driver',path,data,status=422)
    post('driver2',path,{'liters':5,'odometer_km':1010},status=403)
    post('other',path,{'liters':5,'odometer_km':1010},status=404)
    delivered(post,order,driver)
    post('driver','/api/shifts/'+driver+'/closing')
    close={'terminal_total_halalas':0,'counted_halalas':1000,'handover_halalas':1000,
           'handover':{'recipient_user_id':'ops-manager','to_shift_id':manager,'reference':'TEST-END'}}
    result=actor('driver').post('/api/shifts/'+driver+'/submit',json=close,headers={'Idempotency-Key':uuid4().hex})
    assert result.status_code==422
    post('driver','/api/shifts/'+driver+'/submit',{**close,'vehicle_inspection':inspection(1008)},status=422)
    result=close_driver(post,driver,manager)
    detail=actor('driver').get('/api/shifts/'+driver).json()
    assert detail['vehicle_summary']=={'distance_km':25,'fuel_liters':'14.75','refuel_count':2}
    post('driver',path,{'liters':5,'odometer_km':1030},status=409)
    with db.tx() as c:
        snap=c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',(result['report_id'],)).fetchone()['snapshot']
        assert len(snap['shift']['vehicle_log']['refuels'])==2
    from app.reporting import v5_tables, make_v5_print_html, make_v5_pdf
    tables={r['key']:r for r in v5_tables(snap)}
    assert dict(tables['vehicle']['rows'])['المسافة المقطوعة (كم)']==25
    assert dict(tables['vehicle']['rows'])['إجمالي التعبئة (لتر)']=='14.75'
    assert len(tables['refuels']['rows'])==2 and len(tables['inspections']['rows'])==7
    assert '14.75' in make_v5_print_html(snap)
    assert make_v5_pdf(snap).startswith(b'%PDF')

@pytest.mark.postgres
def test_inspection_validation_and_legacy_shift_preservation(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    for changed in ({'fuel_percent':101},{'odometer_km':-1},{'checks':{}},{'checks':{**inspection()['checks'],'brakes':'issue'}}):
        with pytest.raises(ValueError):shift_vehicle.Inspection(**{**inspection(),**changed})
    with db.tx() as c:
        c.execute("UPDATE shifts SET vehicle_log='{}' WHERE id=%s",(driver,))
    post('driver','/api/shifts/'+driver+'/closing')
    post('driver','/api/shifts/'+driver+'/submit',{'terminal_total_halalas':0,'counted_halalas':0,'handover_halalas':0,'vehicle_inspection':inspection(2000,100),'handover':{'recipient_user_id':'ops-manager','to_shift_id':manager,'reference':'TEST-LEGACY'}})
    detail=actor('driver').get('/api/shifts/'+driver).json()
    assert detail['vehicle_summary']['distance_km'] is None
    assert 'start' not in detail['shift']['vehicle_log']

import os
from test_browser import _context, _owner_login, _staff, _capture, _acknowledge_driver_end, chromium_browser, live_app, synthetic_mail

@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Real isolated Chromium only')
@pytest.mark.parametrize('language',['ar','en'])
def test_driver_inspection_refuel_and_closure_browser(live_app,chromium_browser,language):
    from playwright.sync_api import expect
    manager=_staff('delivery_manager');driver=_staff('driver');mid='test-vehicle-manager'
    with db.tx() as c:
        c.execute("UPDATE branches SET settings=settings-'delivery_hours' WHERE id='kps-branch-1'")
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps','[\"kps-branch-1\"]')",(mid,manager['id']))
    context,page,errors=_context(chromium_browser,live_app);_owner_login(context,live_app,driver)
    def fill_check(km,fuel):
        form=page.locator('#modal form')
        form.locator('[name=inspection_odometer]').fill(str(km))
        form.locator('[name=inspection_fuel]').fill(str(fuel))
        for key in shift_vehicle.CHECKS:form.locator('[name=inspection_'+key+']').select_option('ok')
        return form
    try:
        page.goto(live_app+'/ops')
        if language=='en':page.get_by_role('button',name='English',exact=True).click()
        page.locator('#navigation [data-page=shifts]').click()
        page.locator('[data-action=open-shift]').click()
        form=fill_check(1000,75)
        expect(form.locator('.fuel-needle')).to_have_attribute('style','transform: rotate(135deg);')
        form.locator('[name=fuel_full]').select_option('yes')
        for width in (390,1280):
            page.set_viewport_size({'width':width,'height':900})
            _capture(page,'TEST_ONLY_shift_inspection_'+language+'_'+str(width))
        form.locator('button[type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible()
        with db.tx() as c:sid=c.execute("SELECT id FROM shifts WHERE user_id=%s",(driver['id'],)).fetchone()['id']
        page.locator('[data-action=shift-refuel]').click()
        page.locator('#modal [name=liters]').fill('12.50')
        page.locator('#modal [name=odometer_km]').fill('1010')
        page.locator('#modal [name=amount]').fill('25')
        page.locator('#modal [name=station]').fill('TEST fuel station')
        page.locator('#modal [name=fueled_at]').fill('2026-10-15T18:00')
        import io
        from PIL import Image
        receipt=io.BytesIO();Image.new('RGB',(12,12),'white').save(receipt,'PNG')
        page.locator('#modal [name=receipt]').set_input_files({'name':'receipt.png','mimeType':'image/png','buffer':receipt.getvalue()})
        page.locator('#modal button[type=submit]').click()
        expect(page.locator('#modal .vehicle-shift-summary')).to_be_visible()
        page.locator('#modal-close').click()
        # The guidance shortcut and the canonical shift card intentionally
        # expose the same action. Verify both target this driver's shift, and
        # exercise each entry point across the bilingual journeys.
        card_close=page.locator('.shift-card [data-action=start-closing][data-id="'+sid+'"]')
        guide_close=page.locator('.driver-shift-guidance [data-action=start-closing][data-id="'+sid+'"]')
        expect(card_close).to_have_count(1)
        expect(guide_close).to_have_count(1)
        (guide_close if language=='ar' else card_close).click()
        page.locator('[data-confirm-driver-close]').click()
        form=fill_check(1025,100)
        form.locator('[name=counted]').fill('0')
        form.locator('[name=terminal_total]').fill('0')
        form.locator('[name=confirm_closure]').check()
        form.locator('button[type=submit]').click()
        _acknowledge_driver_end(page, language)
        page.locator('#navigation [data-page=shifts]').click()
        page.locator('.shift-card [data-action=shift-detail][data-id="'+sid+'"]').click()
        expect(page.locator('#closure-form')).to_have_count(0)
        expect(page.locator('#modal .vehicle-shift-summary')).to_be_visible()
        with db.tx() as c:
            row=c.execute('SELECT status,vehicle_log FROM shifts WHERE id=%s',(sid,)).fetchone()
            assert row['status']=='submitted'
            assert shift_vehicle.summary(row['vehicle_log'])=={'distance_km':25,'fuel_liters':'12.50','refuel_count':1}
        _capture(page,'TEST_ONLY_shift_vehicle_summary_'+language)
        assert not errors
    finally:context.close()

@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Real isolated Chromium only')
def test_two_hundred_tasks_use_three_compact_columns(live_app,chromium_browser):
    from playwright.sync_api import expect
    owner=_staff();driver=_staff('driver');customer=_staff('customer')
    with db.tx() as c:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES('layout-shift','driver',%s,'kps','[\"kps-branch-1\"]')",(driver['id'],))
        for i in range(200):
            oid='layout-order-'+str(i)
            c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps','kps-branch-1',%s,'delivery','ready','assigned','cash',1000,0,1000,%s,'{}')",(oid,customer['id'],db.json({'items':[{'name':'Test coffee','quantity':1,'line_total_halalas':1000}]})))
            c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,assigned_by) VALUES(%s,%s,%s,'layout-shift',%s)",('layout-task-'+str(i),oid,driver['id'],owner['id']))
    context,page,errors=_context(chromium_browser,live_app);_owner_login(context,live_app,owner)
    try:
        page.set_viewport_size({'width':1440,'height':900});page.goto(live_app+'/ops')
        page.locator('#navigation [data-page=tasks]').click()
        cards=page.locator('.task-card');expect(cards).to_have_count(200)
        boxes=[cards.nth(i).bounding_box() for i in range(4)]
        assert abs(boxes[0]['y']-boxes[1]['y'])<2 and abs(boxes[0]['y']-boxes[2]['y'])<2
        assert boxes[3]['y']>boxes[0]['y']
        assert boxes[0]['height']<500
        page.set_viewport_size({'width':390,'height':844})
        assert cards.nth(1).bounding_box()['y']>cards.nth(0).bounding_box()['y']
        assert not errors
    finally:context.close()
