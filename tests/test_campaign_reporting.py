from datetime import date, datetime, timezone
import pytest
from fastapi import HTTPException
from app import campaign_reporting as reports


def test_riyadh_calendar_period_is_half_open():
    start,end=reports.period(date(2026,10,22),date(2026,10,22))
    assert start.astimezone(timezone.utc)==datetime(2026,10,21,21,tzinfo=timezone.utc)
    assert end.astimezone(timezone.utc)==datetime(2026,10,22,21,tzinfo=timezone.utc)


@pytest.mark.parametrize('start,end',[(date(2026,10,23),date(2026,10,22)),(date(2026,1,1),date(2026,4,4)),(date.max,date.max)])
def test_invalid_or_unbounded_period_rejected(start,end):
    with pytest.raises(HTTPException) as error:reports.period(start,end)
    assert error.value.status_code==422


@pytest.mark.parametrize('role',['customer','driver','branch_manager','delivery_staff','admin'])
def test_non_reporting_roles_fail_before_database_read(role):
    with pytest.raises(HTTPException) as error:
        reports.report_scope(None,{'role':role,'brand_id':'kps'},None)
    assert error.value.status_code==403


def test_denied_manager_and_morning_history_fail_closed():
    for fields in ({'permission_denials':['reports_view']},{'_morning_manager_context':{'shift_id':'morning'}}):
        with pytest.raises(HTTPException) as error:
            reports.report_scope(None,{'role':'delivery_manager','brand_id':'kps',**fields},None)
        assert error.value.status_code==403


def test_final_period_digest_and_pagination_never_return_changed_content():
    snapshot={'brand_id':'kps','start':'2026-10-22','end':'2026-10-22','beneficiaries':[{'order_id':'a'},{'order_id':'b'}]}
    row={'id':'final-test','brand_id':'kps','actor_id':'owner','reason':'اختبار','created_at':'2026-10-23',
         'snapshot':snapshot,'sha256':reports.db.digest(snapshot)}
    view=reports._closure_view(row,1,1)
    assert view['beneficiary_count']==2 and view['beneficiaries']==[{'order_id':'b'}]
    assert 'beneficiaries' not in view['report'] and view['immutable']
    snapshot['beneficiaries'][0]['order_id']='changed'
    with pytest.raises(HTTPException) as invalid:reports._closure_view(row)
    assert invalid.value.detail['code']=='REPORT_INTEGRITY_INVALID'


def test_complete_csv_has_all_rows_bilingual_definitions_and_safe_cells():
    snapshot={'brand_id':'kps','start':'2026-10-22','end':'2026-10-22','policy_version':None,
              'branches':[{'id':'b','name':'=HYPERLINK("unsafe")'}],
              'summary':{'online_orders':2,'gift_claims':2},'daily':[],
              'beneficiaries':[{'order_no':1,'order_id':'o1','branch_id':'b','phone_suffix':'••••5678','created_at':'2026-10-22',
                  'claimed_at':'2026-10-22','policy_version':1,'cancelled':False,'first_online_order':True},
                  {'order_no':2,'order_id':'o2','branch_id':'b','phone_suffix':'••••1111','created_at':'2026-10-22',
                  'claimed_at':'2026-10-22','policy_version':1,'cancelled':True,'first_online_order':None}]}
    row={'id':'final-test','brand_id':'kps','reason':'\t=unsafe()','created_at':datetime(2026,10,23,tzinfo=timezone.utc),
         'snapshot':snapshot,'sha256':reports.db.digest(snapshot)}
    for language,label in [('ar','جميع المستفيدين'),('en','All beneficiaries')]:
        encoded=reports._csv_report(row,language);text=encoded.decode('utf-8-sig')
        assert encoded.startswith(b'\xef\xbb\xbf') and label in text
        assert "'=HYPERLINK" in text and "'\t=unsafe()" in text
        assert '••••5678' in text and '••••1111' in text and '+966' not in text
        assert row['sha256'] in text
