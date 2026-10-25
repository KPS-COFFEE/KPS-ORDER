"""Synthetic current DB -> genuine prior app -> compatibility app -> current app.
No database archive is restored and no production connection is accepted.
"""
import hashlib,json,os,re,subprocess,sys
from pathlib import Path
import pytest
from app import db
from test_operations import ops

ROOT=Path(__file__).resolve().parents[1]
BASE=Path(os.environ.get('ROLLBACK_BASELINE_DIR', ROOT/'test-artifacts/rollback-baseline-source'))
COMPAT=Path(os.environ.get('ROLLBACK_COMPAT_DIR', ROOT/'test-artifacts/rollback-compat-source'))
SPECIAL=('driver_work_trips','driver_work_trip_orders','driver_work_trip_events','branch_print_devices','branch_print_jobs','driver_accounting_settlements','prior_delivery_grants','prior_delivery_revocations','prior_delivery_records')


def assert_candidate_source_identity(identity):
 # PR events retain the synthetic merge SHA in GITHUB_SHA even when the job
 # explicitly checks out the reviewed head. Bind evidence to actual git inputs.
 checked_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
 checked_tree=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=ROOT,text=True).strip()
 assert re.fullmatch(r'[0-9a-f]{40}',checked_sha)
 assert identity['candidate_sha']==checked_sha
 assert identity['candidate_tree']==checked_tree
 if os.environ.get('RUNTIME_REVISION'):
  assert os.environ['RUNTIME_REVISION']==checked_sha


def test_candidate_identity_uses_checked_out_head_not_event_merge(monkeypatch):
 sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
 tree=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=ROOT,text=True).strip()
 monkeypatch.setenv('GITHUB_SHA','f'*40 if sha!='f'*40 else 'e'*40)
 monkeypatch.setenv('RUNTIME_REVISION',sha)
 assert_candidate_source_identity({'candidate_sha':sha,'candidate_tree':tree})


@pytest.mark.parametrize('field',['candidate_sha','candidate_tree','runtime'])
def test_candidate_identity_rejects_stale_source_tree_or_runtime(monkeypatch,field):
 sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
 tree=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=ROOT,text=True).strip()
 identity={'candidate_sha':sha,'candidate_tree':tree}
 monkeypatch.setenv('RUNTIME_REVISION',sha)
 if field=='runtime':monkeypatch.setenv('RUNTIME_REVISION','0'*40)
 else:identity[field]='0'*40
 with pytest.raises(AssertionError):assert_candidate_source_identity(identity)


def probe(path,mode,spec):
 image=os.environ.get('ROLLBACK_COMPAT_IMAGE') if mode=='compat' else None
 if image:
  assert re.fullmatch(r'(?:[^\s]+@)?sha256:[0-9a-f]{64}',image),'Use an immutable rollback image digest or image ID.'
  command=['docker','run','--rm','--network','host','--entrypoint','python',
           '--volume',str(ROOT/'tools/rollback_probe.py')+':/probe.py:ro',
           '--env','DATABASE_URL','--env','APP_ENV=testing',
           '--env','PUBLIC_BASE_URL=http://testserver','--env','APP_ALLOWED_HOSTS=testserver,localhost,127.0.0.1',
           '--env','SESSION_COOKIE_SECURE=false','--env','OTP_PROVIDER=disabled','--env','EMAIL_PROVIDER=disabled',
           '--env','REPORT_WORKER_ENABLED=false','--env','EMAIL_WORKER_ENABLED=false',
           '--env','RECOVERY_POLL_ENABLED=false','--env','DISPATCH_OFFER_ACCEPT_ENABLED=false',
           '--env','DISPATCH_AUTO_ASSIGN_ENABLED=false',image,'/probe.py','/app','compat',json.dumps(spec)]
 else:
  command=[sys.executable,str(ROOT/'tools/rollback_probe.py'),str(path),mode,json.dumps(spec)]
 r=subprocess.run(command,cwd=ROOT,env=os.environ.copy(),capture_output=True,text=True,timeout=90)
 assert r.returncode==0,r.stdout+'\n'+r.stderr
 return json.loads(r.stdout.strip().splitlines()[-1])


def exact_rows(c,tables):
 return {t:c.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in tables}


@pytest.mark.skipif(not os.environ.get('ROLLBACK_BASELINE_DIR') or not (os.environ.get('ROLLBACK_COMPAT_DIR') or os.environ.get('ROLLBACK_COMPAT_IMAGE')), reason='Dedicated rollback drill requires explicitly prepared prior source and compatible source/image')
@pytest.mark.postgres
def test_prior_application_failure_and_compatibility_rollback_without_database_restore(ops):
 actor,post,order,shifts=ops
 _,sid=shifts()
 old=order(name='rollback-before-upgrade-order')
 new=order(name='rollback-after-upgrade-order')
 with db.tx() as c:
  c.execute("UPDATE shifts SET opened_at=now()-interval '2 minutes',scheduled_end_at=now()-interval '1 minute' WHERE id=%s",(sid,))
  for index,oid in enumerate((old,new)):
   c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,picked_up_at) VALUES(%s,%s,'ops-driver',%s,'picked_up','ops-manager',now())",('rollback-task-'+str(index),oid,sid))
 trip=post('driver','/api/driver-work-hours/depart',{'shift_id':sid,'branch_id':'ops-branch','order_ids':[old,new]})['trip']['id']
 with db.tx() as c:
  c.execute("UPDATE driver_tasks SET status='delivered',delivered_at=now() WHERE order_id=ANY(%s)",([old,new],))
  c.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,token_hash,created_by) VALUES('rollback-printer','ops-brand','ops-branch','Synthetic rollback device','synthetic-hash','ops-owner')")
  c.execute("INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,order_id,kind,status,payload,requested_by) VALUES('rollback-print-job','ops-brand','ops-branch','rollback-printer',%s,'order','queued','{}','ops-owner')",(new,))
  c.execute("INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES('rollback-settlement-report',%s,'driver','ops-brand','[\"ops-branch\"]','ops-driver','{}')",(sid,))
  c.execute("INSERT INTO driver_accounting_settlements(id,report_id,brand_id,revision,stage,bank_halalas,cash_due_halalas,note,actor_id) VALUES('rollback-settlement','rollback-settlement-report','ops-brand',1,'balance_approved',500,1000,'TEST pending cash survives rollback','ops-owner')")
  before=exact_rows(c,('orders','users','shifts','driver_tasks',*SPECIAL))
 spec={'shift':sid,'trip':trip}
 raw=probe(BASE,'baseline',spec)
 assert raw['health']==raw['ready']==200
 assert raw['export']=={'status':'rejected','code':'EXPORT_SCHEMA_UNSUPPORTED'}
 assert raw['pending_close']=={'status':200,'shift_status':'closing'}
 with db.tx() as c:assert exact_rows(c,tuple(before))==before
 result=probe(COMPAT,'compat',spec)
 manifest=json.loads((ROOT/'tools/rollback_application_overlay/manifest.json').read_text())
 assert result['app_orders_sha256']==manifest['files']['app/orders.py']
 assert result['ops_js_sha256']==manifest['files']['public/ops.js']
 if os.environ.get('ROLLBACK_COMPAT_IMAGE'):
  assert result['source_identity']['baseline_sha']==manifest['baseline_sha']
  assert result['source_identity']['overlay_manifest_sha256']==hashlib.sha256((ROOT/'tools/rollback_application_overlay/manifest.json').read_bytes()).hexdigest()
  assert_candidate_source_identity(result['source_identity'])
 assert result['health']==result['ready']==200
 assert result['pending_close']=={'status':409,'code':'TRIP_RETURN_REQUIRED'}
 assert result['return_confirmed'] and result['trip_events']==2
 assert result['export']['status']=='ok'
 assert result['export']['counts']['driver_work_trips']==1
 assert result['export']['counts']['driver_work_trip_orders']==2
 assert result['export']['counts']['driver_accounting_settlements']==1
 assert set(SPECIAL)<=set(result['preservation_tables'])
 with db.tx() as c:
  for table in ('orders','users','shifts','driver_tasks','driver_work_trip_orders','branch_print_devices','branch_print_jobs','driver_accounting_settlements'):
   assert exact_rows(c,(table,))[table]==before[table]
  saved=c.execute('SELECT * FROM driver_work_trips WHERE id=%s',(trip,)).fetchone()
  assert saved['returned_at'] and saved['departure_evidence']==before['driver_work_trips'][0]['departure_evidence']
  assert c.execute('SELECT count(*) n FROM orders WHERE id=ANY(%s)',([old,new],)).fetchone()['n']==2
 # Re-upgrade against the same live schema; create another order, never restore DB.
 newest=order(name='rollback-after-reupgrade-order')
 db.migrate()
 with db.tx() as c:
  assert c.execute('SELECT count(*) n FROM orders WHERE id=ANY(%s)',([old,new,newest],)).fetchone()['n']==3
  assert c.execute('SELECT count(*) n FROM driver_work_trip_events WHERE trip_id=%s',(trip,)).fetchone()['n']==2
 evidence={'synthetic_only':True,'database_restored':False,'baseline':raw,'compat':result,'reupgrade_orders_preserved':3}
 artifacts=ROOT/'test-artifacts';artifacts.mkdir(exist_ok=True)
 (artifacts/'rollback-app-compatibility-drill.json').write_text(json.dumps(evidence,indent=2,default=str)+'\n')
