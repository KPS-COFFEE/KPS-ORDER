import pytest
from fastapi import HTTPException
from app import db
from app.driver_shift_city import select
from test_operations import ops
from test_central_workdays import central, open_days, inspection
from test_enbishan_dual_brand import dual


def test_city_selection_requires_explicit_choice_only_for_mixed_cities():
    rows = [{'id':'h1','city':'حفرالباطن'}, {'id':'h2','city':'حفر الباطن'},
            {'id':'n','city':'النعيرية'}]
    with pytest.raises(HTTPException) as error:
        select(rows)
    assert error.value.detail['code'] == 'DRIVER_SHIFT_CITY_REQUIRED'
    assert [r['id'] for r in select(rows, 'النعيرية')] == ['n']
    assert [r['id'] for r in select(rows[:2])] == ['h1','h2']
    with pytest.raises(HTTPException):
        select(rows, 'الخفجي')


def add_city():
    with db.tx() as c:
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) "
                  "VALUES('nar',2,'nar','ops-brand','النعيرية','النعيرية','{}')")
        c.execute("UPDATE users SET branch_ids='[\"ops-branch\",\"nar\"]' "
                  "WHERE id IN ('ops-manager','ops-driver')")


@pytest.mark.postgres
def test_opening_multi_city_driver_requires_city_and_stays_with_selected_manager(ops):
    _, post, _, _ = ops
    add_city()
    manager = post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch','nar']})['shift']['id']
    body = {'kind':'driver','manager_shift_id':manager}
    denied = post('driver','/api/shifts',body,status=409)
    assert denied['detail']['code'] == 'DRIVER_SHIFT_CITY_REQUIRED'
    shift = post('driver','/api/shifts',dict(body,duty_city='النعيرية'))['shift']
    assert shift['branch_ids'] == ['nar']
    assert shift['manager_shift_id'] == manager


@pytest.mark.postgres
def test_empty_legacy_shift_city_correction_preserves_money_and_admission(ops):
    actor,post,make_order,shifts = ops
    manager,driver = shifts()
    add_city()
    with db.tx() as c:
        c.execute("UPDATE shifts SET branch_ids='[\"ops-branch\",\"nar\"]' WHERE id=ANY(%s)", ([manager,driver],))
        before = c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
    response = post('owner',f'/api/shifts/{driver}/duty-city',{'city':'النعيرية','reason':'قرار المالك تخصيص النعيرية'})
    assert response['branch_ids'] == ['nar'] and response['financial_changes'] is False
    with db.tx() as c:
        after = c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
        for field in ('id','opened_at','manager_shift_id','opening_halalas','vehicle_log','status','delivery_operational_date'):
            assert after[field] == before[field]
        assert c.execute("SELECT 1 FROM audit_events WHERE action='shift.duty_city.corrected' AND entity_id=%s",(driver,)).fetchone()
    roster = actor('owner').get('/api/operations/drivers').json()['drivers']
    row = next(r for r in roster if r['id']=='ops-driver')
    assert row['availability']=='available' and row['eligible_branch_ids']==['nar']
    oid=make_order(branch='nar')
    post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})
    post('owner',f'/api/shifts/{driver}/duty-city',{'city':'حفر الباطن','reason':'لا يغير سجل المهمة'},status=409)


@pytest.mark.postgres
def test_city_correction_cannot_grant_scope_or_be_called_by_driver(ops):
    _,post,_,shifts=ops
    _,driver=shifts()
    post('driver',f'/api/shifts/{driver}/duty-city',{'city':'حفر الباطن','reason':'غير مخول'},status=403)
    post('owner',f'/api/shifts/{driver}/duty-city',{'city':'النعيرية','reason':'مدينة خارج الشفت'},status=409)


def central_city():
    with db.tx() as c:
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) SELECT 'nar',99,'nar','kps','النعيرية','النعيرية',settings FROM branches WHERE id='kps-branch-1'")
        c.execute("UPDATE users SET branch_ids=branch_ids||'[\"nar\"]'::jsonb WHERE id IN ('dual-manager','dual-driver')")


@pytest.mark.postgres
def test_grouped_city_repair_preserves_all_ledgers_and_closure_access(central):
    _,actor,post,_,order=central
    manager,driver=open_days(central)
    central_city()
    root=driver['shift']['id']
    with db.tx() as c:
        c.execute("UPDATE shifts SET branch_ids=branch_ids||'[\"nar\"]'::jsonb WHERE id=ANY(%s)",([root,manager['shift']['id']],))
        before=c.execute('SELECT id,opening_halalas,opened_at,vehicle_log FROM shifts WHERE user_id=%s ORDER BY id',('dual-driver',)).fetchall()
    post('owner',f'/api/shifts/{root}/duty-city',{'city':'النعيرية','reason':'بأمر المالك النعيرية فقط'},'kps')
    with db.tx() as c:
        after=c.execute('SELECT id,opening_halalas,opened_at,vehicle_log FROM shifts WHERE user_id=%s ORDER BY id',('dual-driver',)).fetchall()
        assert after==before
        rows=c.execute("SELECT brand_id,branch_ids,available FROM shifts WHERE user_id='dual-driver'").fetchall()
        assert next(r for r in rows if r['brand_id']=='kps')['branch_ids']==['nar']
        enb=next(r for r in rows if r['brand_id']=='enbishan')
        assert enb['branch_ids']==[] and enb['available'] is False
    assert actor('driver','kps').get(f'/api/central-operations/workdays/{root}').status_code==200
    with db.tx() as c:
        assert c.execute("SELECT city FROM driver_duty_cities WHERE driver_id='dual-driver'").fetchone()['city']=='النعيرية'
    post('manager',f'/api/shifts/{root}/duty-city',{'city':'حفر الباطن','reason':'لا يتجاوز قرار المالك'},'kps',status=409)
    oid='nar-order'
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES('nar-order','kps','nar','dual-kps-customer','delivery','ready','pending','cash',1000,0,1000,'{\"items\":[],\"delivery_workflow\":\"manager_dispatch\"}','{}')")
    post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':root},'kps')


@pytest.mark.postgres
def test_next_group_opening_excludes_other_city_brand_ledgers(central):
    _,_,post,_,_=central
    central_city()
    manager=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    driver=post('driver','/api/central-operations/workdays',{'kind':'driver','duty_city':'النعيرية',
        'manager_shift_id':manager['shift']['id'],'vehicle_inspection':inspection()})
    assert len(driver['shifts'])==1
    assert driver['shift']['branch_ids']==['nar'] and driver['shift']['brand_id']=='kps'
