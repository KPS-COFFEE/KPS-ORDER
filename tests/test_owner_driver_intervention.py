import pytest
from app import db
from test_operations import ops
from test_driver_duty_city import add_city
from test_central_workdays import central, open_days
from test_enbishan_dual_brand import dual
from test_driver_duty_city import central_city

BASE='/api/owner-driver-interventions'


@pytest.mark.postgres
def test_owner_task_exception_restores_pause_and_never_waives_city(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    oid=order()
    post('owner',f'/api/shifts/{driver}/availability',{'available':False})
    assert any(s['id']==driver for s in actor('owner').get(f'{BASE}/orders/{oid}/candidates').json()['shifts'])
    body={'driver_shift_id':driver,'reason':'معالجة طلب محدد بأمر المالك','resume_for_task':True}
    post('manager',f'{BASE}/orders/{oid}/assign',body,status=403)
    result=post('owner',f'{BASE}/orders/{oid}/assign',body,key='owner-exception')
    assert result['exception_applies_to_task_only'] is True
    assert post('owner',f'{BASE}/orders/{oid}/assign',body,key='owner-exception')==result
    with db.tx() as c:
        assert c.execute('SELECT available FROM shifts WHERE id=%s',(driver,)).fetchone()['available'] is False
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='owner.driver.assignment_exception'").fetchone()['n']==1
    second=order()
    post('manager',f'/api/orders/{second}/assign',{'driver_shift_id':driver},status=409)
    # Owner exception cannot waive branch/city authorization.
    add_city()
    foreign=order(branch='nar')
    post('owner',f'{BASE}/orders/{foreign}/assign',body,status=409)
    with db.tx() as c:
        assert c.execute('SELECT available FROM shifts WHERE id=%s',(driver,)).fetchone()['available'] is False


@pytest.mark.postgres
def test_empty_shift_owner_move_preserves_finance_and_rejects_task_history(ops):
    _,post,order,shifts=ops
    manager,driver=shifts()
    add_city()
    with db.tx() as c:
        c.execute("UPDATE shifts SET branch_ids='[\"ops-branch\",\"nar\"]' WHERE id=%s",(manager,))
        before=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
    body={'branch_ids':['nar'],'target_manager_shift_id':manager,'reason':'نقل السائق لمدينة واحدة بأمر المالك'}
    post('manager',f'{BASE}/shifts/{driver}/move',body,status=403)
    result=post('owner',f'{BASE}/shifts/{driver}/move',body)
    assert result['financial_changes'] is False
    with db.tx() as c:
        after=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
        assert after['branch_ids']==['nar']
        for field in ('opening_halalas','opened_at','submission','vehicle_log','delivery_operational_date'):
            assert before[field]==after[field]
    oid=order(branch='nar')
    post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})
    post('owner',f'{BASE}/shifts/{driver}/move',dict(body,branch_ids=['ops-branch']),status=409)


@pytest.mark.postgres
def test_group_move_retains_foreign_ledger_and_restores_both_paused_anchors(central):
    _,actor,post,_,order=central
    manager,driver=open_days(central)
    central_city()
    root=driver['shift']['id']
    with db.tx() as c:
        c.execute("UPDATE shifts SET branch_ids=branch_ids||'[\"nar\"]'::jsonb WHERE id=%s",(manager['shift']['id'],))
        before=c.execute('SELECT id,opened_at,opening_halalas,vehicle_log FROM shifts WHERE user_id=%s ORDER BY id',('dual-driver',)).fetchall()
    body={'branch_ids':['nar'],'target_manager_shift_id':manager['shift']['id'],'reason':'نقل وردية النعيرية بأمر المالك'}
    post('owner',f'{BASE}/shifts/{root}/move',body,'kps')
    with db.tx() as c:
        after=c.execute('SELECT id,opened_at,opening_halalas,vehicle_log FROM shifts WHERE user_id=%s ORDER BY id',('dual-driver',)).fetchall()
        assert before==after
        rows=c.execute("SELECT brand_id,branch_ids,available FROM shifts WHERE user_id='dual-driver'").fetchall()
        assert next(r for r in rows if r['brand_id']=='kps')['branch_ids']==['nar']
        foreign=next(r for r in rows if r['brand_id']=='enbishan')
        assert set(foreign['branch_ids'])=={'enbishan-test-1','enbishan-test-2'} and foreign['available'] is False
    assert actor('owner','kps').get('/api/central-operations/workdays/'+root).status_code==200


@pytest.mark.postgres
def test_move_changes_manager_only_after_current_scope_checks_and_rolls_back_failures(ops):
    _,post,_,shifts=ops
    manager,driver=shifts()
    add_city()
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('ops-new-manager','New manager','delivery_manager','ops-brand','[\"nar\"]')")
        original=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
    target=post('owner','/api/shifts',{'kind':'manager','user_id':'ops-new-manager','branch_ids':['nar']})['shift']['id']
    base={'target_manager_shift_id':target,'reason':'نقل مع تحقق صلاحيات المدير'}
    post('owner',f'{BASE}/shifts/{driver}/move',dict(base,branch_ids=['ops-branch','nar']),status=409)
    post('owner',f'{BASE}/shifts/{driver}/move',dict(base,branch_ids=['ops-branch']),status=403)
    with db.tx() as c:
        assert c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()==original
    post('owner',f'{BASE}/shifts/{driver}/move',dict(base,branch_ids=['nar']))
    with db.tx() as c:
        changed=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
        assert changed['manager_shift_id']==target and changed['branch_ids']==['nar']
        assert changed['opened_at']==original['opened_at']


@pytest.mark.postgres
def test_task_exception_restores_paused_physical_anchor_for_other_brand(central):
    _,_,post,_,order=central
    _,driver=open_days(central)
    legs={s['brand_id']:s['id'] for s in driver['shifts']}
    root=driver['shift']['id']
    oid=order('enbishan')
    with db.tx() as c:c.execute('UPDATE shifts SET available=false WHERE id=ANY(%s)',(list(legs.values()),))
    result=post('owner',f'{BASE}/orders/{oid}/assign',
                {'driver_shift_id':legs['enbishan'],'reason':'استثناء مهمة واحدة فقط','resume_for_task':True},'enbishan')
    assert result['task']['shift_id']==legs['enbishan']
    with db.tx() as c:
        rows=c.execute('SELECT available FROM shifts WHERE id=ANY(%s)',(list(legs.values()),)).fetchall()
        assert len(rows)==2 and all(r['available'] is False for r in rows)


@pytest.mark.postgres
def test_move_never_reattributes_opening_money(ops):
    _,post,_,shifts=ops
    manager,driver=shifts()
    add_city()
    with db.tx() as c:
        c.execute("UPDATE shifts SET branch_ids='[\"ops-branch\",\"nar\"]' WHERE id=%s",(manager,))
        c.execute('UPDATE shifts SET opening_halalas=20000 WHERE id=%s',(driver,))
        before=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
    post('owner',f'{BASE}/shifts/{driver}/move',
         {'branch_ids':['nar'],'target_manager_shift_id':manager,'reason':'لا تنسب العهدة لفرع آخر'},status=409)
    with db.tx() as c:assert c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()==before
