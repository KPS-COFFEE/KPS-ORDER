"""One physical operating history over fresh authorized brand-ledger scopes."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query

from . import auth, db, fleet, multibrand, service_hours
from .central_workdays import require_enabled

router=APIRouter(prefix='/fleet',tags=['central-fleet'])


def principals(c,user):
    from .central_operations import context
    values=[]
    for scope in context(c,user)['brands']:
        principal=multibrand.project(c,user,scope['id'])
        if not principal:
            raise db.error(403,'OPERATIONS_SCOPE_CHANGED','تغيرت صلاحيات الفروع؛ حدّث الشاشة.')
        values.append(principal)
    return values


def union_query(c,user,selected):
    selected=dict(selected)
    selected['start']=selected.get('start') or service_hours.delivery_operational_date()
    selected['end']=selected.get('end') or selected['start']
    statements=[];args=[];scopes={};period=None
    for principal in principals(c,user):
        lo,hi,statement,parameters=fleet.query_statement(principal,**selected)
        period=(lo,hi);scopes[principal['brand_id']]=principal
        statements.append('SELECT source.*,%s::text AS brand_id FROM ('+statement+') source')
        args.extend([principal['brand_id'],*parameters])
    if not statements:
        raise db.error(403,'OPERATIONS_SCOPE_CHANGED','لا توجد صلاحية لفروع الأسطول.')
    return ' UNION ALL '.join(statements),args,scopes,period


def totals(c,statement,args):
    # Aggregate the entire authorized union before paging. Comparable fuel rows
    # have their own denominator; incomplete readings never dilute efficiency.
    return c.execute("""SELECT count(*) records,count(DISTINCT vehicle_id) vehicles,
      coalesce(sum((metrics->>'actual_distance')::numeric) FILTER(WHERE coalesce((metrics->>'distance_eligible')::boolean,true)),0) actual_distance,
      coalesce(sum((metrics->>'liters')::numeric),0) liters,
      round(sum((metrics->>'actual_distance')::numeric) FILTER(WHERE metrics->>'efficiency' IS NOT NULL)
       / nullif(sum((metrics->>'liters')::numeric) FILTER(WHERE metrics->>'efficiency' IS NOT NULL),0),3) efficiency,
      count(*) FILTER(WHERE metrics->>'fuel_status'='green') green,
      count(*) FILTER(WHERE metrics->>'fuel_status'='orange') orange,
      count(*) FILTER(WHERE metrics->>'fuel_status'='red') red,
      count(*) FILTER(WHERE metrics->>'fuel_status'='grey') grey,
      coalesce(sum(faults),0) faults,count(*) FILTER(WHERE (metrics->>'exception')::boolean) exceptions
      FROM ("""+statement+') physical_records',args).fetchone()


def present(c,rows,scopes):
    # Canonical presentation resolves branch labels inside each owning ledger.
    for brand,principal in scopes.items():
        fleet.present_rows(c,principal,[row for row in rows if row['brand_id']==brand])
    return rows


@router.get('/options')
def options(user=Depends(auth.current_user)):
    require_enabled(user,fleet.PERMISSION)
    with db.tx() as c:
        people={};branches=[]
        for principal in principals(c,user):
            clauses,args=fleet.scoped(principal)
            for row in c.execute('SELECT DISTINCT u.id,u.display_name'+fleet.BASE+' WHERE '+' AND '.join(clauses)+' ORDER BY u.display_name,u.id',args).fetchall():
                people[row['id']]=row
            rows=c.execute('SELECT id,name,branch_no FROM branches WHERE brand_id=%s ORDER BY branch_no',(principal['brand_id'],)).fetchall()
            branches.extend(dict(row,brand_id=principal['brand_id']) for row in rows if fleet.unrestricted_owner(principal) or row['id'] in principal.get('branch_ids',[]))
        return {'drivers':sorted(people.values(),key=lambda row:(row['display_name'],row['id'])),
                'branches':branches,'definitions':fleet.DEFINITIONS,'today':service_hours.delivery_operational_date(),
                'columns':[{'key':k,'ar':a,'en':e} for k,a,e in [('brand_id','البراند','Brand'),*fleet.COLUMNS]]}


@router.get('/report')
def report(start:date|None=None,end:date|None=None,vehicle_id:str|None=None,driver_id:str|None=None,branch_id:str|None=None,
           fuel_status:fleet.Color|None=None,faults_only:bool=False,exceptions_only:bool=False,metric:fleet.Metric|None=None,
           limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0,le=1000000),user=Depends(auth.current_user)):
    require_enabled(user,fleet.PERMISSION)
    selected=dict(start=start,end=end,vehicle_id=vehicle_id,driver_id=driver_id,branch_id=branch_id,
                  fuel_status=fuel_status,faults_only=faults_only,exceptions_only=exceptions_only,metric=metric)
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        statement,args,scopes,(lo,hi)=union_query(c,user,selected)
        summary=totals(c,statement,args)
        rows=c.execute('SELECT * FROM ('+statement+') physical_records ORDER BY operating_day DESC,received_at DESC,shift_id DESC,segment_no DESC LIMIT %s OFFSET %s',args+[limit,offset]).fetchall()
        return {'rows':present(c,rows,scopes),'total':summary['records'],'summary':summary,
                'period':{'start':lo,'end':hi},'limit':limit,'offset':offset}


@router.get('/export')
def export(format:Literal['pdf','xlsx'],lang:Literal['ar','en']='ar',start:date|None=None,end:date|None=None,
           vehicle_id:str|None=None,driver_id:str|None=None,branch_id:str|None=None,fuel_status:fleet.Color|None=None,
           faults_only:bool=False,exceptions_only:bool=False,metric:fleet.Metric|None=None,user=Depends(auth.current_user)):
    require_enabled(user,fleet.PERMISSION)
    selected=dict(start=start,end=end,vehicle_id=vehicle_id,driver_id=driver_id,branch_id=branch_id,
                  fuel_status=fuel_status,faults_only=faults_only,exceptions_only=exceptions_only,metric=metric)
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        statement,args,scopes,(lo,hi)=union_query(c,user,selected)
        rows=c.execute('SELECT * FROM ('+statement+') physical_records ORDER BY operating_day DESC,received_at DESC,shift_id DESC,segment_no DESC',args).fetchall()
        rows=present(c,rows,scopes)
        db.audit(c,user['id'],'central.fleet.export','fleet','central',{'brands':sorted(scopes),'format':format,'start':str(lo),'end':str(hi),'rows':len(rows)})
    return fleet.render_export(rows,lo,hi,format,lang,central=True)
