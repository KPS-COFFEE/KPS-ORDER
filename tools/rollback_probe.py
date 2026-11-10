"""Subprocess application-version probe against an existing isolated TEST schema."""
import contextlib,hashlib,json,os,sys
from pathlib import Path
from urllib.parse import urlsplit
from starlette.requests import Request
from fastapi import HTTPException
parts=urlsplit(os.environ.get('DATABASE_URL',''))
if os.environ.get('APP_ENV')!='testing' or parts.scheme not in {'postgresql','postgres'} or parts.hostname not in {'localhost','127.0.0.1','::1','postgres'} or not (parts.path.lstrip('/').startswith('test_') or parts.path.endswith('_test')):
 raise RuntimeError('Rollback probe requires an explicitly isolated local TEST database.')
source=Path(sys.argv[1]);mode=sys.argv[2];spec=json.loads(sys.argv[3])
sys.path.insert(0,str(source))
from app import db,auth,operations,exports
from app.main import app
from fastapi.testclient import TestClient
class UndoProbe(Exception):pass
out={'source':str(source),'mode':mode,'app_orders_sha256':hashlib.sha256((source/'app/orders.py').read_bytes()).hexdigest(),'ops_js_sha256':hashlib.sha256((source/'public/ops.js').read_bytes()).hexdigest()}
identity=source/'app/rollback_source_identity.json'
if identity.exists():out['source_identity']=json.loads(identity.read_text())
db.migrate()
with TestClient(app,base_url='http://testserver') as client:
 out['health']=client.get('/health').status_code
 out['ready']=client.get('/ready').status_code
with db.tx() as conn:
 owner=conn.execute("SELECT * FROM users WHERE id='ops-owner'").fetchone()
 driver=conn.execute("SELECT * FROM users WHERE id='ops-driver'").fetchone()
try:
 with db.tx() as conn:
  original=db.tx;db.tx=lambda **kwargs:contextlib.nullcontext(conn)
  try:
   result=operations.request_close(spec['shift'],Request({'type':'http','headers':[(b'idempotency-key',b'rollback-close-transaction-probe')]}),user=owner)
   out['pending_close']={'status':200,'shift_status':conn.execute('SELECT status FROM shifts WHERE id=%s',(spec['shift'],)).fetchone()['status']}
  except HTTPException as e:out['pending_close']={'status':e.status_code,'code':e.detail.get('code')}
  finally:db.tx=original
  raise UndoProbe()
except UndoProbe:pass
try:
 with db.tx() as conn:
  conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
  archive,manifest=exports.build_archive(conn,full=True)
  out['export']={'status':'ok','counts':{t:manifest['counts'].get(t) for t in ('driver_work_trips','driver_work_trip_orders','driver_work_trip_events','orders','branch_print_devices','branch_print_jobs','driver_accounting_settlements')}}
except HTTPException as e:out['export']={'status':'rejected','code':e.detail.get('code')}
if mode=='compat':
 from app import driver_work_hours
 result=driver_work_hours.return_trip(spec['trip'],driver_work_hours.Movement(branch_id='ops-branch'),Request({'type':'http','headers':[(b'idempotency-key',b'rollback-test-return')]}),user=driver)
 assert result['trip']['returned_at']
 out['return_confirmed']=True
 with db.tx() as conn:
  out['preservation_tables']=list(db.preservation_snapshot(conn))
  out['trip_events']=conn.execute('SELECT count(*) n FROM driver_work_trip_events WHERE trip_id=%s',(spec['trip'],)).fetchone()['n']
print(json.dumps(out,default=str))
db.close_pools()
