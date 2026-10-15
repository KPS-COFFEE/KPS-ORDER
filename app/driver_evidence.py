"""Scoped durable fuel/report images, separate from short-lived delivery photos."""
import io
from PIL import Image,ImageOps
from fastapi import APIRouter,Depends,Request,Response
from . import auth,db
from .chat import Attachment,validated_image
from .operations import shift_access,mutate,uid,roles
router=APIRouter(prefix='/api/driver-evidence',tags=['driver-evidence'])

def validate(c,eid,user,sid):
    r=c.execute('SELECT * FROM driver_evidence WHERE id=%s AND brand_id=%s AND shift_id=%s',(eid,user['brand_id'],sid)).fetchone()
    if not r or r['driver_id']!=user['id']:raise db.error(404,'EVIDENCE_SCOPE','الصورة خارج نطاقك.')
    return r

@router.post('/shifts/{sid}')
def upload(sid:str,body:Attachment,request:Request,user=Depends(auth.current_user)):
    roles(user,'driver')
    if not auth.has_permission(user,'vehicle_refuel') and not auth.has_permission(user,'vehicle_faults_report'):raise db.error(403,'FORBIDDEN','رفع الصورة غير مصرح.')
    raw=validated_image(body)
    with Image.open(io.BytesIO(raw)) as source:
        im=ImageOps.exif_transpose(source).convert('RGB');im.thumbnail((2000,2000));out=io.BytesIO();im.save(out,'JPEG',quality=88);data=out.getvalue()
    with db.tx() as c:
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone();shift_access(c,user,s)
        if s['user_id']!=user['id'] or s['status'] not in ('open','closing'):raise db.error(409,'SHIFT_CLOSED','الشفت لا يستقبل مرفقات جديدة.')
        def perform():
            if not db.rate_limit(c,'driver-evidence:'+user['id'],12,60):raise db.error(429,'RATE_LIMITED','انتظر قبل رفع صور أخرى.')
            eid=uid();c.execute('INSERT INTO driver_evidence(id,brand_id,shift_id,driver_id,content_type,data) VALUES(%s,%s,%s,%s,%s,%s)',(eid,user['brand_id'],sid,user['id'],'image/jpeg',data));return {'id':eid}
        import hashlib
        return mutate(c,user,request,'driver.evidence:'+sid,{'sha256':hashlib.sha256(data).hexdigest()},perform)

@router.get('/{eid}')
def download(eid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        r=c.execute('SELECT * FROM driver_evidence WHERE id=%s AND brand_id=%s',(eid,user['brand_id'])).fetchone()
        if not r:raise db.error(404,'EVIDENCE_SCOPE','الصورة غير متاحة.')
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(r['shift_id'],)).fetchone()
        if user['role']=='admin' and (auth.has_permission(user,'vehicle_maintenance_manage') or auth.has_permission(user,'accounting_reports')):
            if not set(s['branch_ids'])<=set(user.get('branch_ids',[])):raise db.error(404,'EVIDENCE_SCOPE','الصورة خارج النطاق.')
            receipt=any(x.get('receipt_id')==eid for x in (s.get('vehicle_log') or {}).get('refuels',[]))
            report=c.execute("SELECT 1 FROM vehicle_reports WHERE evidence_id=%s AND department='maintenance'",(eid,)).fetchone()
            if not ((receipt and auth.has_permission(user,'accounting_reports')) or (report and auth.has_permission(user,'vehicle_maintenance_manage'))):raise db.error(404,'EVIDENCE_SCOPE','الصورة خارج صلاحية القسم.')
        else:shift_access(c,user,s)
        return Response(bytes(r['data']),media_type=r['content_type'],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
