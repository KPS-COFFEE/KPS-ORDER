"""New admission days and one reviewed correction over immutable cash evidence."""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from psycopg.errors import RaiseException
from app import db, manager_admission, manager_day_split, service_hours
from test_operations import ops
from test_enbishan_dual_brand import dual
from test_central_workdays import central, open_days, inspection
from test_aggregate_driver_closure import declare_separate_bag, rows as allocation_rows


def test_stale_manager_admission_is_separate_from_unfinished_work():
    clock = datetime(2026, 11, 6, 16, tzinfo=service_hours.RIYADH)
    manager = {'delivery_operational_date': '2026-11-05', 'status': 'open'}
    with pytest.raises(HTTPException) as failure:
        manager_admission.require_current(manager, at=clock)
    assert failure.value.detail['code'] == 'MANAGER_NEW_DAY_REQUIRED'
    assert manager == {'delivery_operational_date': '2026-11-05', 'status': 'open'}
    manager['delivery_operational_date'] = '2026-11-06'
    manager_admission.require_current(manager, at=clock)
    manager['manager_admission_ended_at'] = clock
    with pytest.raises(HTTPException):
        manager_admission.require_current(manager, at=clock)


def test_correction_windows_are_copied_without_mutating_original_evidence():
    from datetime import date
    source = {'operational_date': '2026-11-05', 'period': 'evening', 'branches': {
        'branch': {'window_start': '2026-11-05T16:00:00+03:00', 'window_end': '2026-11-06T04:00:00+03:00'}}}
    target = manager_day_split.shifted_window(source, date(2026, 11, 5), date(2026, 11, 6))
    assert target['branches']['branch']['window_end'] == '2026-11-07T04:00:00+03:00'
    assert source['branches']['branch']['window_end'] == '2026-11-06T04:00:00+03:00'
    assert target['operational_date'] == '2026-11-06'


@pytest.mark.postgres
def test_new_manager_day_opens_with_old_driver_task_still_pending(central):
    _, actor, post, _, make_order = central
    manager, driver = open_days(central)
    oid = make_order('kps')
    post('manager', '/api/orders/'+oid+'/assign', {'driver_shift_id': driver['shift']['id']}, 'kps')
    yesterday = service_hours.delivery_operational_date() - timedelta(days=1)
    with db.tx() as c:
        c.execute('UPDATE shifts SET delivery_operational_date=%s WHERE id=ANY(%s)',
                  (yesterday, [s['id'] for s in manager['shifts']+driver['shifts']]))
        originals = c.execute('SELECT id,status,submission,opening_halalas,staffing_ended_at FROM shifts '
                              'WHERE user_id=%s ORDER BY id', ('dual-manager',)).fetchall()
    new = post('manager', '/api/central-operations/workdays', {'kind': 'manager'})
    assert new['shift']['id'] != manager['shift']['id']
    post('manager', '/api/central-operations/workdays', {'kind': 'manager'}, status=409)
    with db.tx() as c:
        after = c.execute('SELECT id,status,submission,opening_halalas,staffing_ended_at FROM shifts '
                          'WHERE id=ANY(%s) ORDER BY id', ([s['id'] for s in manager['shifts']],)).fetchall()
        assert after == originals
        assert all(s['status']=='open' and s['staffing_ended_at'] is None for s in after)
        assert c.execute('SELECT status FROM driver_tasks WHERE order_id=%s', (oid,)).fetchone()['status']=='assigned'
    assert actor('manager','kps').get('/api/orders/'+oid).status_code==200
    options = actor('manager','kps').get('/api/shifts/current').json()
    assert not ({s['id'] for s in manager['shifts']} & {s['id'] for s in options['available_manager_shifts']})
    assert {s['id'] for s in manager['shifts'] if s['brand_id']=='kps'} <= {s['id'] for s in options['closure_manager_shifts']}
    # New-order admission is rejected, while the original assigned task survives.
    post('manager', '/api/orders/'+make_order('kps')+'/assign',
         {'driver_shift_id':driver['shift']['id']}, 'kps', status=409)


def mixed_closed_vault(central):
    _, _, post, _, _ = central
    manager, first, path, declared = declare_separate_bag(central, 'حفر الباطن')
    post('manager', path+'/physical-receipt', {'declaration_id':declared['declaration_id'],
         'received_halalas':700, 'confirmed':True, 'reference':'Synthetic first-day receipt'})
    second = post('driver','/api/central-operations/workdays',{'kind':'driver',
        'manager_shift_id':manager['shift']['id'],'vehicle_inspection':inspection(1010)})
    path2='/api/central-operations/workdays/'+second['shift']['id']
    request=post('driver',path2+'/request',{'reason':'Synthetic second physical day ended'})
    post('manager',path2+'/decision',{'request_id':request['shift']['closure_authorization']['id'],
         'decision':'approved','reason':'Synthetic manager duty completion'})
    post('driver',path2+'/closing',{})
    declared2=post('driver',path2+'/physical-submit',{'cash_halalas':800,'card_halalas':1100,
        'vehicle_inspection':inspection(1020),'handover_branch_id':'kps-branch-1',
        'bag_reference':'Synthetic second day sealed bag','bag_delivered':True})
    post('manager',path2+'/physical-receipt',{'declaration_id':declared2['declaration_id'],
        'received_halalas':800,'confirmed':True,'reference':'Synthetic second-day receipt'})
    # Reproduce the published defect: a second actual admission date inherited
    # the previous manager day/window. No financial or receipt row is edited.
    old=service_hours.delivery_operational_date()-timedelta(days=2)
    new=old+timedelta(days=1)
    with db.tx() as c:
        for shift in manager['shifts']+first['shifts']+second['shifts']:
            actual=new if shift['id'] in {s['id'] for s in second['shifts']} else old
            opened=datetime.combine(actual,datetime.min.time(),service_hours.RIYADH)+timedelta(hours=16)
            window=manager_day_split.shifted_window(shift['delivery_window_snapshot'],
                manager_admission.stored_day(shift),old)
            c.execute('UPDATE shifts SET opened_at=%s,delivery_operational_date=%s,delivery_window_snapshot=%s WHERE id=%s',
                      (opened,old,db.json(window),shift['id']))
    post('manager','/api/cash-bags/workdays/'+manager['shift']['id']+'/vault-close',{'confirmed':True})
    return manager, first, second, old, new


@pytest.mark.postgres
def test_split_preserves_bags_receipts_money_and_both_accounting_cards(central, monkeypatch):
    from app import reporting
    monkeypatch.setattr(reporting, 'generate_files', lambda *_: None)
    _, actor, post, _, _ = central
    manager, first, second, old, new=mixed_closed_vault(central)
    source=manager['shift']['id']
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        owner=c.execute("SELECT * FROM users WHERE id='dual-owner'").fetchone()
        preview=manager_day_split.plan(c,owner,source)
        assert {k:len(v) for k,v in preview['groups'].items()}=={old.isoformat():2,new.isoformat():2}
        result=manager_day_split.apply(c,owner,source,preview['fingerprint'],'Owner correction of mixed admission days')
        assert len(result['corrections'])==2 and result['financial_bytes_verified']
        assert result['validation']['bag_count']==2
        assert result['validation']['bag_cash_halalas']==1500
        assert result['validation']['bag_card_halalas']==2000
        assert result['validation']['days'][old.isoformat()]['bag_cash_halalas']==700
        assert result['validation']['days'][new.isoformat()]['bag_cash_halalas']==800
        assert result['validation']['days'][new.isoformat()]['bag_card_halalas']==1100
        assert c.execute('SELECT count(*) n FROM manager_vault_closures').fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM shared_driver_closure_receipts').fetchone()['n']==2
        assert c.execute('SELECT count(*) n FROM custody_events').fetchone()['n']==0
        for entry in result['corrections']:
            moved=c.execute('SELECT id,reporting_operational_date FROM shifts WHERE manager_shift_id=%s',
                             (entry['target_shift_id'],)).fetchall()
            assert len(moved)==1 and moved[0]['reporting_operational_date']==new
        new_anchor=next(e['target_shift_id'] for e in result['corrections']
                        if e['source_shift_id']==source)
        new_group=c.execute('SELECT workday_id FROM central_workday_legs WHERE shift_id=%s',(new_anchor,)).fetchone()
        assert len(manager_day_split.vault_closure(c,new_group['workday_id'])['bag_ids'])==1
        assert len(manager_day_split.vault_closure(c,preview['source_workday']['id'])['bag_ids'])==1
        with pytest.raises(RaiseException,match='documented manager day split'):
            with c.transaction():
                c.execute("INSERT INTO custody_transfers(id,from_shift_id,to_shift_id,amount_halalas,reference,closure,created_by) "
                          "VALUES('synthetic-stale-allocation',%s,%s,800,'old writer proof',true,'dual-owner')",
                          (second['shift']['id'],source))

    response=actor('owner','kps').get('/api/accounting/manager-cards').json()['cards']
    cards={row['id']:row for row in response}
    assert cards[source]['day']==old.isoformat() and cards[source]['expected_driver_count']==1
    assert cards[new_anchor]['day']==new.isoformat() and cards[new_anchor]['expected_driver_count']==1
    assert cards[source]['operationally_closed'] and cards[new_anchor]['operationally_closed']
    assert not cards[source]['ready'] and not cards[new_anchor]['ready']
    with db.tx() as c:
        owner=c.execute("SELECT * FROM users WHERE id='dual-owner'").fetchone()
        with pytest.raises(ValueError,match='already'):
            manager_day_split.plan(c,owner,source)
        second_anchor=second['shift']['id']
        receipt=c.execute("SELECT r.id,d.id declaration_id,b.id bag_id FROM shared_driver_closure_receipts r "
            "JOIN shared_driver_closure_declarations d ON d.id=r.declaration_id "
            "JOIN driver_cash_bags b ON b.declaration_id=d.id "
            "JOIN central_workdays w ON w.id=b.workday_id WHERE w.anchor_shift_id=%s",(second_anchor,)).fetchone()
    post('accountant','/api/cash-bags/'+receipt['bag_id']+'/count',
         {'received_cash_halalas':800,'card_halalas':1100,'reference':'Verified second-day bag','confirmed':True})
    post('accountant','/api/central-operations/workdays/'+second_anchor+'/physical-allocation',
         {'declaration_id':receipt['declaration_id'],'receipt_id':receipt['id'],
          'legs':allocation_rows(second['shifts'],cash=(600,200),card=(500,600)),
          'reason':'Verified allocation of second day bag','confirmed':True})
    with db.tx() as c:
        credited=c.execute('SELECT to_shift_id,amount_halalas FROM custody_transfers ORDER BY to_shift_id').fetchall()
        assert {r['to_shift_id'] for r in credited}=={r['target_shift_id'] for r in result['corrections']}
        assert sum(r['amount_halalas'] for r in credited)==800



@pytest.mark.postgres
def test_unreviewed_split_rolls_back_without_any_correction(central):
    manager, _, _, _, _=mixed_closed_vault(central)
    with pytest.raises(ValueError,match='fingerprint'):
        with db.tx() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            owner=c.execute("SELECT * FROM users WHERE id='dual-owner'").fetchone()
            manager_day_split.apply(c,owner,manager['shift']['id'],'stale','Reviewed owner correction')
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM manager_day_splits').fetchone()
        assert c.execute("SELECT count(*) n FROM shifts WHERE kind='manager'").fetchone()['n']==2
