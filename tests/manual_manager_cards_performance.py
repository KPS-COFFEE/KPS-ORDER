"""Manual reproducible baseline comparison in isolated TEST_DATABASE_URL only."""
import json,subprocess,time,types,statistics
from pathlib import Path
from app import db
from app.accounting_statements import manager_cards
from test_accounting import accountant
from test_operations import ops
from test_driver_accounting_settlements import make_report
from test_accounting_manager_shift_day import duty

def test_measure_manager_card_page(ops):
    accountant();snapshot,_=make_report()
    with db.tx() as c:
        for i in range(100):
            shift=duty(c,'benchmark-'+str(i),'2026-10-28')
            c.execute("INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES(%s,%s,'driver','ops-brand','[\"ops-branch\"]','ops-driver',%s)",('benchmark-report-'+str(i),shift,db.json(snapshot)))
        user=c.execute("SELECT * FROM users WHERE id='ops-accountant'").fetchone()
    baseline=types.ModuleType('app._baseline_accounting_statements');baseline.__package__='app'
    exec(subprocess.check_output(['git','show','9d4965fa9505e1c151fa80ea57be844ab6738455:app/accounting_statements.py'],text=True),baseline.__dict__)
    class Counting:
        def __init__(self,c):self.c=c;self.queries=0
        def execute(self,*a,**kw):self.queries+=1;return self.c.execute(*a,**kw)
    measurements={}
    for name,fn in [('before_all_history',lambda c:baseline.manager_cards(c,user,['ops-branch'])),('after_first_page',lambda c:manager_cards(c,user,['ops-branch'],page_size=20,offset=0))]:
        runs=[]
        for _ in range(3):
            with db.tx() as c:
                measured=Counting(c);started=time.perf_counter();result=fn(measured);elapsed=(time.perf_counter()-started)*1000
                runs.append({'elapsed_ms':round(elapsed,2),'queries':measured.queries,'json_bytes':len(json.dumps(result,ensure_ascii=False).encode()),'cards':len(result['cards']),'driver_rows':sum(len(g['drivers']) for g in result['cards'])})
        measurements[name]={'runs':runs,'median_ms':round(statistics.median(r['elapsed_ms'] for r in runs),2)}
    measurements['scope']='Synthetic local PostgreSQL18, 101 manager shifts and 101 driver closure reports, pending accounting; before returns all history; after returns20 shifts. No production load extrapolation.'
    Path('test-artifacts').mkdir(exist_ok=True)
    Path('test-artifacts/accounting-manager-card-performance.json').write_text(json.dumps(measurements,ensure_ascii=False,indent=2))
    print(json.dumps(measurements))
