"""Authenticated manager endpoint benchmark on large isolated operational data."""
from contextlib import contextmanager
import json,statistics,time
from pathlib import Path
from app import auth,db,central_workdays,driver_readiness
from test_central_workdays import central,open_days
from test_enbishan_dual_brand import dual

def seed_large_workspace(central):
    _,actor,_,_,_=central
    manager,driver=open_days(central)
    manager_id=next(s['id'] for s in manager['shifts'] if s['brand_id']=='kps')
    branches=['kps-branch-1']+[f'TEST-manager-branch-{i}' for i in range(2,21)]
    with db.tx() as c:
        for i,bid in enumerate(branches[1:],2):
            c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES(%s,%s,%s,'kps',%s,'حفر الباطن','{}')",(bid,100+i,'TEST-source-'+bid,'TEST '+bid))
        c.execute("UPDATE users SET branch_ids=%s WHERE id='dual-manager'",(db.json(branches),))
        c.execute('UPDATE shifts SET branch_ids=%s WHERE id=%s',(db.json(branches),manager_id))
        # Twenty idle drivers with twenty allowed branches each expose repeated
        # manager/user/physical-shift readiness reads at realistic roster size.
        for i in range(20):
            uid=f'TEST-manager-driver-{i}';sid=f'TEST-manager-driver-shift-{i}'
            c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES(%s,%s,'driver','kps',%s)",(uid,uid,db.json(branches)))
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,manager_shift_id) VALUES(%s,'driver',%s,'kps',%s,'open',%s)",(sid,uid,db.json(branches),manager_id))
        for i in range(500):
            bid=branches[i%len(branches)]
            c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps',%s,'dual-kps-customer','delivery','accepted','pending','cash',2500,0,2500,%s,%s)",
                (f'TEST-manager-order-{i}',bid,db.json({'items':[],'branch_name':bid,'delivery_workflow':'manager_dispatch','order_reference':f'TEST-{i}'}),db.json({'name':'TEST ONLY'})))
    return actor('manager','kps')

def test_manager_endpoints_before_after(central,monkeypatch):
    client=seed_large_workspace(central)
    original_tx=db.tx;original_find=auth._find_session;observation=driver_readiness.ReadinessObservation
    counter={'queries':0}
    class Count:
        def __init__(self,c):self.c=c
        def __getattr__(self,name):return getattr(self.c,name)
        def execute(self,*args,**kwargs):counter['queries']+=1;return self.c.execute(*args,**kwargs)
    @contextmanager
    def measured_tx(**kwargs):
        with original_tx(**kwargs) as c:yield Count(c)
    def published_find(c,request):
        user=original_find(c,request)
        if user and 'operations_brands' in user and request.url.path!='/api/session':
            user['central_workday_drain']=central_workdays.drain_metadata(c,user)
        return user
    monkeypatch.setattr(db,'tx',measured_tx)
    paths=['/api/operations/drivers','/api/orders?service=delivery&limit=100&dispatch_workspace=true&dispatch_scope=current',
           '/api/operations/alerts','/api/conversations','/api/central-operations/driver-locations','/api/session']
    results={}
    signatures={}
    for name,finder,wrapper in [('published_9d4965f',published_find,lambda c:c),('candidate_4145453',original_find,lambda c:c),('manager_followup',original_find,observation)]:
        monkeypatch.setattr(auth,'_find_session',finder);monkeypatch.setattr(driver_readiness,'ReadinessObservation',wrapper)
        report={}
        for path in paths:
            samples=[]
            for _ in range(3):
                counter['queries']=0;start=time.perf_counter();response=client.get(path);elapsed=(time.perf_counter()-start)*1000
                assert response.status_code==200,response.text
                payload=response.json()
                if path=='/api/operations/drivers':
                    signature=[(d['id'],d['availability'],d['eligible_branch_ids'],d['live_status']) for d in payload['drivers']]
                    if name=='published_9d4965f':signatures[path]=signature
                    else:assert signature==signatures[path]
                samples.append({'elapsed_ms':round(elapsed,2),'queries':counter['queries'],'json_bytes':len(response.content)})
            report[path]={'samples':samples,'median_ms':round(statistics.median(s['elapsed_ms'] for s in samples),2),'queries':samples[0]['queries']}
        results[name]=report
    results['scope']='Real authenticated manager, local isolated PostgreSQL18; 500 pending orders,20 extra idle drivers×20branches plus dual-brand seeddriver. Published baseline restores exact removed auth drain and unwrapped readiness; candidate4145453 unwrapped readiness; followup snapshot-local point reads. All business APIs genuine; not production/device latency.'
    Path('test-artifacts').mkdir(exist_ok=True)
    Path('test-artifacts/manager-workspace-performance.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps(results,ensure_ascii=False))
