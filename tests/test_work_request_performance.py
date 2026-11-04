"""Reproducible local query comparison; no production or physical-device claim.

The baseline restores exactly the removed per-business-read drain lookup while
keeping the same database fixture and current business handler on both sides.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import statistics
import time
import pytest
from app import auth, db, central_workdays
from test_central_workdays import central, open_days
from test_enbishan_dual_brand import dual

@pytest.mark.postgres
def test_business_reads_avoid_session_presentation_drain_queries(central,monkeypatch):
    _,actor,_,_,_=central
    open_days(central)
    client=actor('driver','kps')
    original_tx=db.tx;original_find=auth._find_session
    counter={'queries':0}
    class MeasuredConnection:
        def __init__(self,connection):self.connection=connection
        def __getattr__(self,name):return getattr(self.connection,name)
        def execute(self,*args,**kwargs):
            counter['queries']+=1
            return self.connection.execute(*args,**kwargs)
    @contextmanager
    def measured_tx(**kwargs):
        with original_tx(**kwargs) as connection:yield MeasuredConnection(connection)
    def baseline_find(connection,request):
        user=original_find(connection,request)
        if user and 'operations_brands' in user and request.url.path!='/api/session':
            user['central_workday_drain']=central_workdays.drain_metadata(connection,user)
        return user
    monkeypatch.setattr(db,'tx',measured_tx)
    result={}
    for name,finder in [('baseline_redundant_drain',baseline_find),('candidate',original_find)]:
        monkeypatch.setattr(auth,'_find_session',finder)
        samples=[]
        for _ in range(5):
            counter['queries']=0;start=time.perf_counter()
            response=client.get('/api/tasks')
            elapsed=(time.perf_counter()-start)*1000
            assert response.status_code==200,response.text
            samples.append({'queries':counter['queries'],'elapsed_ms':round(elapsed,3)})
        result[name]={'samples':samples,'median_queries':statistics.median(s['queries'] for s in samples),
                      'median_elapsed_ms':statistics.median(s['elapsed_ms'] for s in samples)}
    assert result['candidate']['median_queries']<result['baseline_redundant_drain']['median_queries']
    result.update(environment='Local PostgreSQL18, real isolated shared manager/driver duty, authenticated TestClient /api/tasks',
                  production_latency=False,baseline='Same current handler + exact redundant auth drain call from 9d4965fa',
                  authorization='Same fresh session, brand membership, branch and task checks on both sides')
    directory=Path(__file__).resolve().parents[1]/'test-artifacts';directory.mkdir(exist_ok=True)
    (directory/'work-request-source-performance.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
