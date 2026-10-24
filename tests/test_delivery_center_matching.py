"""Policy boundaries for staff orders and separate operational/financial approval."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app import orders, accounting, operations, reporting, db, employee_permissions
from app.branch_printing import receipt

class Result:
    def __init__(self,row=None):self.row=row
    def fetchone(self):return self.row

@pytest.mark.parametrize('value',[{}, {'name':''},{'name':'   '},{'name':'عميل'}])
def test_optional_customer_name(value):
    assert orders.DeliveryCenterCustomer(**value).name=='عميل'
    assert orders.DeliveryCenterCustomer(name='  أحمد  ').name=='أحمد'
    with pytest.raises(ValidationError):orders.catalog.CustomerInfo(name='')

@pytest.mark.parametrize('role,scope,shift,allowed',[
    ('delivery_manager',['b'],True,True),('delivery_manager',[],True,False),
    ('driver',['b'],True,False),('delivery_staff',['b'],True,False),
    ('delivery_manager',['b'],False,False),('owner',[],True,True)])
def test_staff_order_requires_scope_and_own_open_shift(role,scope,shift,allowed):
    class Conn:
        def execute(self,sql,args):
            assert "user_id=%s" in sql and "status='open'" in sql
            assert args==('brand','actor','b')
            return Result({'id':'shift'} if shift else None)
    actor={'id':'actor','role':role,'brand_id':'brand','branch_ids':scope}
    if allowed:assert orders.delivery_center_scope(Conn(),actor,'b')=='shift'
    else:
        with pytest.raises(HTTPException):orders.delivery_center_scope(Conn(),actor,'b')
    assert ('orders_create_customer' in employee_permissions.effective(actor))==(role in ['owner','delivery_manager'])

@pytest.mark.parametrize('lang',['ar','en'])
def test_receipt_center_only_customer_copy_keeps_fulfillment(lang):
    o={'snapshot':{'order_source':'delivery_center','order_reference':'REF','items':[]},'created_at':datetime.now(timezone.utc),'order_no':1,'payment_method':'cash','payment_status':'unpaid'}
    before=deepcopy(o)
    result=receipt(o,{'name':'INTERNAL BRANCH'},'order',lang)
    assert ('مركز التوصيل' if lang=='ar' else 'Delivery Center') in str(result)
    assert 'INTERNAL BRANCH' not in str(result)
    assert 'INTERNAL BRANCH' in str(receipt(o,{'name':'INTERNAL BRANCH'},'preparation',lang))
    assert o==before

@pytest.mark.parametrize('role,uid,branches,ok',[('admin','accountant',['b'],True),('admin','accountant',[],False),('owner','manager',[],False)])
def test_financial_scope_and_separate_reviewer(role,uid,branches,ok):
    r={'brand_id':'brand','kind':'manager','user_id':'manager','branch_ids':['b']}
    u={'brand_id':'brand','role':role,'id':uid,'branch_ids':branches}
    if ok:accounting.matching_report_scope(u,r)
    else:
        with pytest.raises(HTTPException):accounting.matching_report_scope(u,r)

@pytest.mark.parametrize('bank,cash,note,revision,previous,status',[
    (900,500,'',0,None,'matched'),(800,600,'offset does not match',0,None,'difference'),
    (800,600,'',0,None,422),(900,500,'',0,{'revision':1,'status':'difference'},409),
    (900,500,'',1,{'revision':1,'status':'matched'},409)])
def test_bank_cash_matching_are_independent_and_revision_protected(monkeypatch,bank,cash,note,revision,previous,status):
    snap={'summary':{'card_collected_halalas':1000,'card_refunded_halalas':100},'shift':{'submission':{'handover_halalas':500}}}
    original=deepcopy(snap)
    r={'id':'report','brand_id':'brand','kind':'manager','user_id':'manager','branch_ids':['b'],'snapshot':snap}
    writes=[]
    class Conn:
        def execute(self,sql,args):
            if sql.startswith('SELECT * FROM closure_reports'):return Result(r)
            if sql.startswith('SELECT * FROM manager_financial'):return Result(previous)
            if sql.lstrip().startswith('INSERT INTO manager_financial'):
                writes.append(args);return Result({'id':args[0],'status':args[-2]})
            raise AssertionError(sql)
    @contextmanager
    def tx():yield Conn()
    monkeypatch.setattr(db,'tx',tx);monkeypatch.setattr(db,'idempotent',lambda c,u,k,a,b,fn:fn())
    monkeypatch.setattr(db,'audit',lambda *a:None)
    body=accounting.FinancialMatch(bank_halalas=bank,cash_halalas=cash,bank_reference='BANK',cash_reference='CASH',note=note,expected_revision=revision)
    args=('report',body,SimpleNamespace(headers={}),{'id':'accountant','brand_id':'brand','role':'admin','branch_ids':['b']})
    if isinstance(status,int):
        with pytest.raises(HTTPException) as e:accounting.match_manager_closure(*args)
        assert e.value.status_code==status;assert not writes
    else:
        assert accounting.match_manager_closure(*args)['status']==status
        assert writes[0][4:8]==(900,500,bank,cash)
    assert snap==original

@pytest.mark.parametrize('owner,kind,expected',[('manager','manager',True),('other','manager',False),('driver','driver',False)])
def test_manager_own_operational_approval_not_fake_cash_receipt(monkeypatch,owner,kind,expected):
    s={'id':'s','user_id':owner,'brand_id':'brand','branch_ids':['b'],'kind':kind,'status':'submitted','submission':{'difference_halalas':0,'retained_halalas':0,'handover_halalas':500}}
    captured=[]
    class Conn:
        def execute(self,sql,args):
            if sql.startswith('SELECT user_id'):return Result({'user_id':'manager'} if kind=='driver' else None)
            if sql.startswith('SELECT coalesce(sum(amount'):return Result({'n':0,'nrows':0})
            if sql.startswith('SELECT 1'):return Result()
            if sql.startswith('UPDATE shifts'):captured.append(args);return Result()
            raise AssertionError(sql)
    monkeypatch.setattr(operations,'lock_shift',lambda *a:s)
    monkeypatch.setattr(operations,'unresolved',lambda *a:False)
    from app import driver_incentives
    monkeypatch.setattr(driver_incentives,'require_decisions',lambda *a:None)
    monkeypatch.setattr(reporting,'create_snapshot',lambda *a:'report')
    monkeypatch.setattr(db,'audit',lambda *a:None)
    actor={'id':'manager','role':'delivery_manager','brand_id':'brand','branch_ids':['b']}
    if expected:
        assert operations.approve_close_in_tx(Conn(),actor,'s',operations.Review())['status']=='closed'
        assert captured[0][2].obj['operational_approved'] is True
    else:
        with pytest.raises(HTTPException):operations.approve_close_in_tx(Conn(),actor,'s',operations.Review())
        assert not captured


def test_staff_order_preserves_actor_but_uses_opaque_customer_and_server_price(monkeypatch):
    import hashlib
    inserted=[];contact_locks=[]
    expected_id='customer_contact_'+hashlib.sha256('brand\0+966501234567'.encode()).hexdigest()
    expected_lock='operational-contact:brand:'+expected_id
    class Conn:
        def execute(self,sql,args):
            if sql=='SELECT pg_advisory_xact_lock(hashtextextended(%s,0))':
                assert args==(expected_lock,)
                assert not inserted, 'Serialize the opaque contact before inserting it'
                contact_locks.append(args[0]);return Result()
            if sql.startswith('SELECT settings'):return Result({'settings':{}})
            if sql.startswith('SELECT id FROM shifts'):return Result({'id':'manager-shift'})
            if sql.startswith('INSERT INTO users'):
                assert contact_locks==[expected_lock] and args[0]==expected_id
                inserted.append(args);return Result({'id':args[0],'active':True,'identity_kind':'guest','brand_id':args[3]})
            raise AssertionError(sql)
    @contextmanager
    def tx(**kwargs):yield Conn()
    monkeypatch.setattr(db,'tx',tx)
    monkeypatch.setattr(db,'idempotent',lambda c,u,k,a,b,fn,**kw:fn())
    monkeypatch.setattr(db,'rate_limit',lambda *a:True)
    monkeypatch.setattr(orders,'_ensure_manager_duty',lambda *a:None)
    monkeypatch.setattr(orders.catalog,'quote_order',lambda *a,**kw:{'branch_id':'b','branch_city':'CITY','branch_no':2,'total_halalas':1200,'subtotal_halalas':1000,'discount_halalas':0,'delivery_fee_halalas':200,'items':[]})
    monkeypatch.setattr(orders.invoice_profiles,'capture',lambda *a,**kw:{'seller':'ORIGINAL'})
    monkeypatch.setattr(orders.performance,'capture_policy',lambda *a,**kw:{})
    body=orders.DeliveryCenterOrder(brand_id='brand',branch_id='b',service='delivery',payment_method='cash',items=[{'product_id':'p','quantity':1}],customer={'phone':'0501234567','neighborhood':'الحي','address':'عنوان','location':{'lat':28.0,'lng':45.0}},expected_total_halalas=1200)
    actor={'id':'manager','brand_id':'brand','role':'delivery_manager','branch_ids':['b']}
    result=orders.create_delivery_center_order(body,None,actor,'request-key')
    assert isinstance(result,db.AtomicIdempotentWrite)
    assert inserted[0][0].startswith('customer_') and inserted[0][0]!='manager'
    assert inserted[0][1]=='عميل'
    assert result.params[4]==inserted[0][0] # Ownership is not the employee or phone account.
    assert result.params[19]=='manager' and result.params[22]=='manager' # Real event/audit actor, after explicit payment_status.
    assert result.params[12]==0 # Ordinary staff orders never gain a benefit.
    snap=result.params[13].obj
    assert snap['order_source']=='delivery_center' and snap['manager_shift_id']=='manager-shift'
    assert snap['receipt_profile']=={'seller':'ORIGINAL'}
    assert result.params[15].obj['phone_verified'] is False
    body.expected_total_halalas=1
    with pytest.raises(HTTPException) as e:orders.create_delivery_center_order(body,None,actor,'other-key')
    assert e.value.status_code==409 and len(inserted)==1
    assert contact_locks==[expected_lock], 'A rejected server price must not enter contact creation'
