import pytest
from app import db, reporting
from tests.test_operations import ops, begin_driver_closure
pytestmark=pytest.mark.postgres

@pytest.fixture
def prepared(ops):
    actor,post,order,shifts=ops
    with db.tx() as c:c.execute("UPDATE branches SET city='حفر الباطن' WHERE id='kps-branch-1'")
    manager,driver=shifts()
    return actor,post,manager,driver

BODY={'order_count':3,'cash_halalas':1500,'card_halalas':2500,'revision':0,'reason':'Sapaad daily summary'}

@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_summary_replace_replay_and_scope(prepared):
    actor,post,manager,driver=prepared
    url=f'/api/shifts/{driver}/external-enbishan'
    for who in ('driver','owner','branch'):post(who,url,BODY,status=403)
    saved=post('manager',url,BODY,key='external-once')
    assert post('manager',url,BODY,key='external-once')==saved
    post('manager',url,BODY,status=409)
    post('manager',url,{**BODY,'revision':1,'cash_halalas':1800})
    with db.tx() as c:
        assert c.execute("SELECT sum(amount_halalas) n FROM custody_events WHERE shift_id=%s",(driver,)).fetchone()['n']==1800
        assert c.execute('SELECT count(*) n FROM orders').fetchone()['n']==0
        c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'")
    post('manager',url,BODY,key='external-once',status=403)

@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_closed_driver_and_manager_snapshots_no_double_count(prepared):
    actor,post,manager,driver=prepared
    url=f'/api/shifts/{driver}/external-enbishan'
    post('manager',url,BODY)
    result=post('manager',f'/api/shifts/{driver}/driver-close',{'received_halalas':1500,'counted_halalas':1500,'terminal_total_halalas':2500,'confirmed':True,'exception_reason':'Manager verified Sapaad'})
    post('manager',url,{**BODY,'revision':1},status=409)
    with db.tx() as c:
        driver_snap=c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',(result['report_id'],)).fetchone()['snapshot']
        assert driver_snap['shift']['submission']['difference_halalas']==0
        assert driver_snap['shift']['submission']['card_difference_halalas']==0
        assert driver_snap['summary']['completed_orders']==0
        assert driver_snap['external_enbishan_totals']['total_halalas']==4000
        assert c.execute("SELECT sum(amount_halalas) n FROM custody_events WHERE shift_id=%s",(manager,)).fetchone()['n']==1500
        rid=reporting.create_snapshot(c,manager,'ops-manager')
        snap=c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',(rid,)).fetchone()['snapshot']
    assert len(snap['external_enbishan'])==1
    assert snap['external_enbishan_totals']==driver_snap['external_enbishan_totals']
    for snap in (driver_snap,snap):
        assert 'طلبات عنبيشن' in reporting.make_v5_print_html(snap)
        assert 'طلبات عنبيشن' in reporting.make_csv(snap).decode('utf-8-sig')
        assert reporting.make_pdf(snap).startswith(b'%PDF')
        assert any(t['key']=='external-enbishan' for t in reporting.output_tables(snap,'compact','drivers'))

@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_city_status_and_validation(prepared):
    actor,post,manager,driver=prepared
    url=f'/api/shifts/{driver}/external-enbishan'
    for changed in ({'order_count':0},{'cash_halalas':-1},{'order_count':True}):post('manager',url,{**BODY,**changed},status=422)
    with db.tx() as c:c.execute("UPDATE branches SET city='الرياض' WHERE id='kps-branch-1'")
    post('manager',url,BODY,status=403)
    with db.tx() as c:c.execute("UPDATE branches SET city='حفرالباطن' WHERE id='kps-branch-1'")
    post('manager',url,BODY)
    begin_driver_closure(post,driver)
    post('manager',f'/api/shifts/{driver}/submit',{'counted_halalas':1500,'handover_halalas':1500,'terminal_total_halalas':2500,'exception_reason':'checked'})
    post('manager',url,{**BODY,'revision':1},status=409)
