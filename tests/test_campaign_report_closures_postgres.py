"""Finalized periods preserve complete observations without closing an offer."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from fastapi import Response
from psycopg import sql
from psycopg.rows import dict_row

from app import campaign_reporting as reports, db, exports
from test_campaign_reporting_postgres import campaign_data
from test_operations import ops
from test_backup import independent_restore_database
from test_rollback_schema_compatibility import compat

pytestmark=pytest.mark.postgres
PERIOD={'start':'2026-10-22','end':'2026-10-22','reason':'اختبار إقفال الفترة'}
PATH='/api/reports/promotions/closures'


def close(client,**changes):
    response=client().post(PATH,json=PERIOD|changes,headers={'Idempotency-Key':'final-report-0001'})
    assert response.status_code==200,response.text
    return response.json()


def test_finalization_is_complete_consistent_permanent_and_does_not_stop_offers(campaign_data):
    client,ids=campaign_data
    with db.tx() as c:
        settings=c.execute('SELECT * FROM app_settings ORDER BY brand_id').fetchall()
        claims=c.execute('SELECT * FROM first_order_gift_claims ORDER BY brand_id,phone').fetchall()
    created=close(client);rid=created['closure']['id']
    assert created['beneficiary_count']==3 and created['campaign_state_changed'] is False
    saved=client().get(PATH+'/'+rid+'?limit=1').json()
    assert saved['immutable'] and saved['beneficiary_count']==3 and len(saved['beneficiaries'])==1
    assert saved['report']['summary']['gift_claims']==3
    assert saved['report']['summary']['online_orders']==3
    assert saved['report']['attribution']=='observed_online_history_only'
    assert saved['report']['selected_branch_id'] is None
    assert saved['closure']['sha256']==created['closure']['sha256']
    assert '+966' not in json.dumps(saved) and 'customer_id' not in json.dumps(saved)
    second=client().get(PATH+'/'+rid+'?offset=1&limit=2').json()
    assert len(second['beneficiaries'])==2
    assert {x['order_id'] for x in saved['beneficiaries']+second['beneficiaries']}=={ids['first'],ids['repeat'],ids['cancelled']}
    replay=close(client)
    assert replay==created
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM promotion_report_closures').fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='campaign.report.finalized'").fetchone()['n']==1
        assert c.execute('SELECT * FROM app_settings ORDER BY brand_id').fetchall()==settings
        assert c.execute('SELECT * FROM first_order_gift_claims ORDER BY brand_id,phone').fetchall()==claims
        assert c.execute('SELECT count(*) n FROM otp_challenges').fetchone()['n']==0
        c.execute("UPDATE orders SET preparation_status='cancelled' WHERE id=%s",(ids['first'],))
    latest=client().get('/api/reports/promotions/daily?start=2026-10-22&end=2026-10-22').json()
    assert latest['summary']['cancelled_gift_orders']==2
    frozen=client().get(PATH+'/'+rid+'?limit=1').json()
    assert frozen==saved
    with db.tx() as c:
        row=c.execute('SELECT * FROM promotion_report_closures WHERE id=%s',(rid,)).fetchone()
        assert db.digest(row['snapshot'])==row['sha256']
    for statement in ['UPDATE promotion_report_closures SET reason=reason WHERE id=%s','DELETE FROM promotion_report_closures WHERE id=%s']:
        with pytest.raises(psycopg.errors.RaiseException,match='append-only'):
            with db.tx() as c:c.execute(statement,(rid,))


def test_finalized_report_scopes_replay_filters_roles_and_immediate_revocation(campaign_data):
    client,_=campaign_data
    created=close(client,branch_id='ops-branch',policy_version=1);rid=created['closure']['id']
    view=client().get(PATH+'/'+rid).json()
    assert view['report']['selected_branch_id']=='ops-branch' and view['report']['policy_version']==1
    assert view['report']['summary']['online_orders']==3 and view['beneficiary_count']==2
    assert client('manager').get(PATH+'/'+rid).status_code==200
    assert client('manager',branch_ids=[]).get(PATH+'/'+rid).status_code==404
    assert client('manager',branch_ids=[]).get(PATH).json()['closures']==[]
    assert client('other').get(PATH+'/'+rid).status_code==404
    assert client('other').get(PATH+'/'+rid+'/download').status_code==404
    assert client('other').get(PATH).json()['closures']==[]
    for who in ('customer','driver','branch'):
        assert client(who).get(PATH).status_code==403
        assert client(who).get(PATH+'/'+rid).status_code==403
        assert client(who).post(PATH,json=PERIOD,headers={'Idempotency-Key':'final-report-0002'}).status_code==403
    assert client('manager').post(PATH,json=PERIOD,headers={'Idempotency-Key':'final-report-0002'}).status_code==403
    assert client('manager',permission_denials=['reports_view']).get(PATH+'/'+rid).status_code==403
    assert client('manager',_morning_manager_context={'shift_id':'morning'}).get(PATH+'/'+rid).status_code==403
    # Owner capabilities are unconditional in the retained role policy; real
    # loss of finalization access is a role change, including before replay.
    assert client('owner',role='delivery_manager').post(PATH,json=PERIOD|{'branch_id':'ops-branch','policy_version':1},headers={'Idempotency-Key':'final-report-0001'}).status_code==403
    assert client().post(PATH,json=PERIOD|{'branch_id':'ops-branch-other'},headers={'Idempotency-Key':'final-report-0003'}).status_code==404
    assert client().post(PATH,json=PERIOD,headers={'Idempotency-Key':'final-report-0001'}).status_code==409


def test_finalization_rejects_unbounded_invalid_requests_and_never_saves_a_partial_report(campaign_data,monkeypatch):
    client,_=campaign_data
    assert client().post(PATH,json=PERIOD).status_code==400
    for change in ({'reason':'   '},{'reason':'a\x00b'},{'start':'2026-01-01','end':'2026-04-04'},
                   {'policy_version':1.5},{'enabled':False},{'end':'2026-10-21'}):
        assert client().post(PATH,json=PERIOD|change,headers={'Idempotency-Key':'invalid-report-0001'}).status_code==422
    monkeypatch.setattr(reports,'MAX_FINAL_BENEFICIARIES',2)
    response=client().post(PATH,json=PERIOD,headers={'Idempotency-Key':'large-report-0001'})
    assert response.status_code==409 and response.json()['detail']['code']=='REPORT_TOO_LARGE'
    monkeypatch.setattr(reports,'MAX_FINAL_BENEFICIARIES',20000)
    monkeypatch.setattr(reports,'MAX_FINAL_BYTES',100)
    assert client().post(PATH,json=PERIOD,headers={'Idempotency-Key':'large-report-0002'}).status_code==409
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM promotion_report_closures').fetchone()['n']==0
        assert c.execute("SELECT count(*) n FROM idempotency_keys WHERE operation LIKE '%%campaign.report.finalize'").fetchone()['n']==0


def test_concurrent_same_key_finalization_commits_one_complete_snapshot(campaign_data):
    _,_=campaign_data
    with db.tx() as c:user=c.execute("SELECT * FROM users WHERE id='ops-owner'").fetchone()
    barrier=Barrier(4)
    body=reports.FinalizePeriod(**PERIOD)
    def run(_):
        barrier.wait(timeout=15)
        return reports.finalize_period(body,Response(),user,idem='concurrent-final-report-0001')
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(run,range(4)))
    assert all(result==results[0] for result in results)
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM promotion_report_closures').fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='campaign.report.finalized'").fetchone()['n']==1


@pytest.mark.parametrize('lang',['ar','en'])
def test_complete_csv_download_is_masked_scoped_and_spreadsheet_safe(campaign_data,lang):
    client,_=campaign_data
    with db.tx() as c:c.execute("UPDATE branches SET name='=HYPERLINK(\"unsafe\")' WHERE id='ops-branch'")
    created=close(client,reason=' =UNTRUSTED()');rid=created['closure']['id']
    response=client().get(PATH+'/'+rid+'/download?lang='+lang)
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='private, no-store'
    assert response.headers['content-type'].startswith('text/csv')
    assert response.content.startswith(b'\xef\xbb\xbf')
    assert "'=HYPERLINK" in response.text and "'=UNTRUSTED()" in response.text
    assert '+966' not in response.text
    assert '••••5678' in response.text and '••••1111' in response.text and '••••2222' in response.text
    assert created['closure']['sha256'] in response.text
    assert ('جميع المستفيدين' if lang=='ar' else 'All beneficiaries') in response.text
    assert client().get(PATH+'/'+rid+'/download?lang=xx').status_code==422


def test_prior_archive_and_current_restore_preserve_populated_final_report(campaign_data,independent_restore_database):
    client,_=campaign_data
    created=close(client);rid=created['closure']['id']
    with db.tx() as c:
        original=c.execute('SELECT * FROM promotion_report_closures WHERE id=%s',(rid,)).fetchone()
        raw,manifest=compat.build_archive(c,full=True)
        archive,verified=exports.verify_archive(raw,require_full=True)
        assert verified==manifest and manifest['counts']['promotion_report_closures']==1
        assert json.loads(archive.read('data/promotion_report_closures.json'))[0]['sha256']==original['sha256']
        business,scope=compat.build_archive(c,brand_id='ops-other')
        scoped,_=exports.verify_archive(business)
        assert json.loads(scoped.read('data/promotion_report_closures.json'))==[]
    target='kps_restore_'+uuid4().hex
    result=exports.restore_archive(raw,target_url=independent_restore_database,target_schema=target)
    assert result['verified']
    with psycopg.connect(independent_restore_database,row_factory=dict_row) as c:
        c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
        assert c.execute('SELECT * FROM promotion_report_closures WHERE id=%s',(rid,)).fetchone()==original
        assert c.execute('SELECT count(*) n FROM first_order_gift_claims').fetchone()['n']==4
    with pytest.raises(psycopg.errors.RaiseException,match='append-only'):
        with psycopg.connect(independent_restore_database,row_factory=dict_row) as c:
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
            c.execute('DELETE FROM promotion_report_closures WHERE id=%s',(rid,))
