"""Driver contact must retain live task scope and never grant inbox access."""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app import chat


class Connection:
    def __init__(self,task,order,shift):
        self.task=task;self.order=order;self.shift=shift;self.statements=[]
    def execute(self,sql,args):
        self.statements.append(sql)
        self.row=self.task if 'FROM driver_tasks' in sql else self.order if 'FROM orders' in sql else self.shift
        return self
    def fetchone(self):return self.row


def connection(status='accepted',driver='driver',shift='open'):
    return Connection({'id':'task','order_id':'order','driver_id':driver,'status':status,'shift_id':'shift'},
                      {'id':'order','brand_id':'brand','branch_id':'branch','service':'delivery'},
                      {'user_id':'driver','status':shift})


def user():return {'id':'driver','role':'driver','brand_id':'brand','branch_ids':['branch']}


@pytest.mark.parametrize('status',['delivered','cancelled','transferred','reassigned','completed'])
def test_closed_or_reassigned_task_blocks_chat_and_whatsapp(monkeypatch,status):
    monkeypatch.setattr(chat.auth,'can_access_order',lambda *args:True)
    with pytest.raises(HTTPException) as err:chat.driver_contact_scope(connection(status),user(),'task')
    assert err.value.status_code==404


def test_other_driver_and_branch_or_brand_denied(monkeypatch):
    monkeypatch.setattr(chat.auth,'can_access_order',lambda *args:True)
    with pytest.raises(HTTPException):chat.driver_contact_scope(connection(driver='other'),user(),'task')
    monkeypatch.setattr(chat.auth,'can_access_order',lambda *args:False)
    with pytest.raises(HTTPException):chat.driver_contact_scope(connection(),user(),'task')


@pytest.mark.parametrize('state',['closing','submitted','closed'])
def test_shift_must_remain_open(monkeypatch,state):
    monkeypatch.setattr(chat.auth,'can_access_order',lambda *args:True)
    with pytest.raises(HTTPException) as err:chat.driver_contact_scope(connection(shift=state),user(),'task')
    assert err.value.status_code==409


def test_active_assignment_allowed_but_no_general_inbox(monkeypatch):
    monkeypatch.setattr(chat.auth,'can_access_order',lambda *args:True)
    assert chat.driver_contact_scope(connection(),user(),'task')[0]['id']=='task'
    assert not chat.allowed(user(),{'brand_id':'brand','branch_id':'branch','route':'delivery'})


@pytest.mark.parametrize('lock',[False,True])
def test_contact_read_never_locks_but_mutations_keep_transition_lock_order(monkeypatch,lock):
    monkeypatch.setattr(chat.auth,'can_access_order',lambda *args:True)
    c=connection()
    chat.driver_contact_scope(c,user(),'task',lock=lock)
    locked=[statement for statement in c.statements
            if 'FOR UPDATE' in statement or 'FOR NO KEY UPDATE' in statement]
    assert locked==([
        'SELECT * FROM orders WHERE id=%s FOR NO KEY UPDATE',
        'SELECT * FROM driver_tasks WHERE id=%s FOR UPDATE',
        'SELECT user_id,status FROM shifts WHERE id=%s FOR UPDATE',
    ] if lock else [])


@pytest.mark.parametrize('status',['delivered','cancelled','transferred','reassigned'])
def test_contact_read_retains_live_assignment_guards(monkeypatch,status):
    monkeypatch.setattr(chat.auth,'can_access_order',lambda *args:True)
    with pytest.raises(HTTPException) as err:
        chat.driver_contact_scope(connection(status),user(),'task',lock=False)
    assert err.value.status_code==404


def test_contact_get_scope_messages_and_phone_share_readonly_snapshot(monkeypatch):
    from contextlib import contextmanager
    calls=[]
    class ReadConnection:
        def execute(self,sql,args=()):
            calls.append(sql)
            return self
        def fetchone(self):return {'id':'conversation'}
        def fetchall(self):return [{'seq':1,'text':'hello','attachment_id':None}]
    c=ReadConnection()
    @contextmanager
    def transaction():yield c
    monkeypatch.setattr(chat.db,'tx',transaction)
    monkeypatch.setattr(chat.auth,'has_permission',lambda *args:True)
    def scope(conn,actor,tid,*,lock):
        assert conn is c and lock is False
        assert calls==['SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY']
        return {'id':tid},{'customer':{'phone':'0501234567'}}
    monkeypatch.setattr(chat,'driver_contact_scope',scope)
    result=chat.driver_customer_messages('task',after=0,user=user())
    assert result['messages'][0]['text']=='hello'
    assert result['whatsapp_url']=='https://wa.me/966501234567'
    assert all('FOR UPDATE' not in statement for statement in calls)


def test_contact_download_authorizes_in_readonly_snapshot(monkeypatch):
    from contextlib import contextmanager
    calls=[]
    class ReadConnection:
        def execute(self,sql,args=()):calls.append(sql);return self
        def fetchone(self):return {'data':b'png','content_type':'image/png'}
    c=ReadConnection()
    @contextmanager
    def transaction():yield c
    monkeypatch.setattr(chat.db,'tx',transaction)
    monkeypatch.setattr(chat.auth,'has_permission',lambda *args:True)
    def scope(conn,actor,tid,*,lock):
        assert conn is c and lock is False
        assert calls==['SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY']
    monkeypatch.setattr(chat,'driver_contact_scope',scope)
    response=chat.driver_customer_download('task','image',user=user())
    assert response.body==b'png'
    assert response.headers['cache-control']=='private, no-store'


@pytest.mark.parametrize('state',['closing','submitted','closed'])
def test_contact_read_denies_closed_shift(monkeypatch,state):
    monkeypatch.setattr(chat.auth,'can_access_order',lambda *args:True)
    with pytest.raises(HTTPException) as err:
        chat.driver_contact_scope(connection(shift=state),user(),'task',lock=False)
    assert err.value.status_code==409


def test_contact_read_denies_wrong_driver_and_revoked_scope(monkeypatch):
    monkeypatch.setattr(chat.auth,'can_access_order',lambda *args:True)
    with pytest.raises(HTTPException):
        chat.driver_contact_scope(connection(driver='other'),user(),'task',lock=False)
    monkeypatch.setattr(chat.auth,'can_access_order',lambda *args:False)
    with pytest.raises(HTTPException):
        chat.driver_contact_scope(connection(),user(),'task',lock=False)


@pytest.mark.parametrize('phone,expected',[('0501234567','https://wa.me/966501234567'),('+966501234567','https://wa.me/966501234567'),('٠٥٠١٢٣٤٥٦٧','https://wa.me/966501234567'),('javascript:alert(1)',None),('',None)])
def test_whatsapp_exact_phone_link(phone,expected):
    assert chat.whatsapp_contact(phone)==expected


@pytest.mark.parametrize('text',['','   ','x'*2001])
def test_empty_or_oversize_message_rejected(text):
    with pytest.raises(ValidationError):chat.DriverCustomerMessage(text=text)


def test_image_only_message_and_caption():
    assert chat.DriverCustomerMessage(attachment_id='image').text==''
    assert chat.DriverCustomerMessage(text=' المدخل ',attachment_id='image').text=='المدخل'


def test_real_image_validation_and_mime_mismatch():
    import base64,io
    from PIL import Image
    stream=io.BytesIO();Image.new('RGB',(8,8)).save(stream,format='PNG')
    data=stream.getvalue();encoded=base64.b64encode(data).decode()
    assert chat.validated_image(chat.Attachment(content_type='image/png',data_base64=encoded))==data
    with pytest.raises(HTTPException):chat.validated_image(chat.Attachment(content_type='image/jpeg',data_base64=encoded))


@pytest.mark.parametrize('mime,data',[('image/svg+xml','PHN2Zy8+'),('image/png','invalid base64'),('image/png','bm90IGFuIGltYWdl')])
def test_invalid_image_content_rejected(mime,data):
    with pytest.raises(HTTPException):chat.validated_image(chat.Attachment(content_type=mime,data_base64=data))
