"""Run the unmodified release against disposable, learner-isolated databases.

No import from this module exists in the operating application. It can only
start with an explicit training flag and a freshly initialized loopback DB.
"""
from __future__ import annotations
import asyncio
import contextvars
import html
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HERE=Path(__file__).resolve().parent
SOURCE_SHA='e67c61643717be8ca8744409f0214bc35b6d0a0a'
ROOM=contextvars.ContextVar('training_room',default=None)
BRAND=contextvars.ContextVar('training_brand',default=None)
ROOMS={}
ROOM_LOCK=threading.Lock()
ROLES={'customer':'الزبون','driver':'السائق','evening':'مدير التوصيل المسائي',
       'morning':'مدير التوصيل الصباحي','manager':'المدير','branch':'موظف الفرع',
       'accountant':'المحاسب','fleet':'مسؤول السيارات','owner':'المالك'}
SCENARIOS={
 'explore':('تجربة حرة','تنقّل بين الأدوار وجرّب الإجراءات الحالية على بيانات تدريبية.'),
 'order':('طلب الزبون','اختر الخدمة والفرع والأصناف، ثم أكمل الطلب من الواجهة الأصلية.'),
 'dispatch':('إسناد الطلب','افتح الطلبات ثم اختر السائق المتاح. بدّل إلى السائق لإكمال الرحلة.'),
 'delivery':('رحلة السائق','استلم المهمة وتابع خطوات الاستلام والصور والتسليم والتحصيل.'),
 'shortfall':('الزبون دفع أقل','افتح التحصيل، فعّل «المبلغ المستلم أقل من المطلوب»، واكتب المستلم والسبب.'),
 'nonreceipt':('الزبون لم يدفع','من التحصيل اختر «لم أستلم أي مبلغ»، ووثّق السبب والواقعة.'),
 'split':('كاش وشبكة','اختر «كاش وشبكة» وسجّل ما استلمته فعليًا. لا تفترض نجاح عملية الشبكة.'),
 'problem':('طلب متعثر','ارفع المشكلة من المهمة، ثم بدّل إلى مدير التوصيل لمتابعتها.'),
 'chat':('المحادثات والمرفقات','افتح المحادثات، أرسل رسالة أو صورة، ثم بدّل الدور لمتابعة الرد.'),
 'closure':('تقفيلة السائق','افتح الشفتات والتقفيلات ثم أنهِ الشفت وفق النموذج الحالي.'),
 'manager-close':('تقفيلة مدير التوصيل','استلم تقفيلات السائقين أولًا، ثم تابع تقفيلة المدير وتسليم الكيس.'),
 'accounting':('متابعة المحاسبة','راجع التحصيل الفعلي وفرق التحصيل. التقفيلة التشغيلية ليست اعتمادًا ماليًا.'),
 'reports':('التقارير','افتح التقارير المحفوظة أو تقارير التشغيل وجرّب الفترة والتصفية والطباعة.'),
 'permissions':('الصلاحيات الحالية','راجع الصلاحيات الموجودة فعلًا؛ لا توجد إضافة تدريبية لصلاحيات لم تُنفذ.'),
 'morning':('الشفت الصباحي','تدرّب بحساب مدير التوصيل الصباحي وفق فروعه وصلاحياته المحدودة.'),
 'vehicle':('السيارات والوقود','راجع السيارات وسجل التشغيل والفحص والتعبئة ضمن الدور المخول.'),
 'complaint':('شكوى العميل','أنشئ محادثة أو تذكرة ثم جرّب الرد والمتابعة من دور المدير.'),
 'print':('الطباعة وفرع التجهيز','جرّب اختيار فرع التجهيز ومشاهدة حالة الطباعة. لا توجد طابعة فعلية متصلة.'),
}

def configure():
 if os.getenv('KPS_TRAINING_ONLY')!='true':raise RuntimeError('Explicit isolated training flag required')
 if any(os.getenv(k) for k in ('DATABASE_URL','TAWKED_API_KEY','RESEND_API_KEY','SMTP_PASSWORD','TWILIO_AUTH_TOKEN','RENDER_API_KEY')):
  raise RuntimeError('Training must not inherit a database or external provider credential')
 binary=next(iter(sorted(Path('/usr/lib/postgresql').glob('*/bin'))),None)
 if not binary:raise RuntimeError('PostgreSQL binaries are required')
 os.environ['PATH']=str(binary)+':'+os.environ.get('PATH','')
 pgroot=Path('/tmp/kps-training-postgres')
 if pgroot.exists():shutil.rmtree(pgroot)
 subprocess.run(['initdb','-D',str(pgroot),'-A','trust','--no-locale','-E','UTF8'],check=True,stdout=subprocess.DEVNULL)
 subprocess.run(['pg_ctl','-D',str(pgroot),'-l','/tmp/kps-training-postgres.log','-o',
  "-h 127.0.0.1 -p 55432 -c unix_socket_directories='' -c shared_buffers=16MB -c max_connections=50 -c work_mem=1MB -c fsync=off",'-w','start'],check=True,stdout=subprocess.DEVNULL)
 subprocess.run(['createdb','-h','127.0.0.1','-p','55432','test_enbishan_template'],check=True)
 url='postgresql://'+os.getenv('USER','learner')+'@127.0.0.1:55432/test_enbishan_template'
 # Resolve username from actual OS identity rather than trusting USER.
 import pwd
 url='postgresql://'+pwd.getpwuid(os.getuid()).pw_name+'@127.0.0.1:55432/test_enbishan_template'
 os.environ.update(DATABASE_URL=url,APP_ENV='testing',ENBISHAN_ISOLATED_ENABLED='true',
  PUBLIC_BASE_URL=os.getenv('RENDER_EXTERNAL_URL','http://localhost:8080'),SESSION_COOKIE_SECURE='true',
  SESSION_CONTEXT_COOKIES_ENABLED='true',OTP_PROVIDER='disabled',EMAIL_PROVIDER='disabled',
  EMAIL_WORKER_ENABLED='false',REPORT_WORKER_ENABLED='false',MANAGER_NOTIFICATION_WORKER_ENABLED='false',
  MAPBOX_ENABLED='false',MAPBOX_CUSTOMER_ENABLED='false',STAFF_GEO_MODE='off',
  KPS_PORTAL_HOST_ROUTING_ENABLED='false',KPS_OFFICIAL_HOST_ENFORCEMENT_ENABLED='false',
  KPS_CENTRAL_OPERATIONS_ENABLED='true',INITIAL_CATALOG_IMPORT='true',
  KPS_CENTRAL_WORKDAYS_ENABLED='true',SESSION_TTL_HOURS='2')
 from urllib.parse import urlsplit
 host=urlsplit(os.environ['PUBLIC_BASE_URL']).hostname
 os.environ['APP_ALLOWED_HOSTS']=host+',localhost,127.0.0.1,testserver'
 os.environ['APP_ALLOWED_ORIGINS']=os.environ['PUBLIC_BASE_URL']
 return url

def seed():
 from app import db, employee_permissions
 db.migrate()
 with db.tx() as c:
  c.execute("INSERT INTO brands(id,name) VALUES('enbishan','عنبيشن') ON CONFLICT DO NOTHING")
  c.execute("INSERT INTO app_settings(brand_id,settings) VALUES('enbishan','{}') ON CONFLICT DO NOTHING")
  for brand in ('kps','enbishan'):
   cat=json.loads((HERE/'fixtures'/f'training-public-{brand}-catalog.json').read_text())
   branches=json.loads((HERE/'fixtures'/f'training-public-{brand}-branches.json').read_text())
   c.execute('UPDATE brands SET name=%s,settings=%s WHERE id=%s',(cat['brand']['name'],db.json({'services':branches['brand_services']}),brand))
   settings={'operational_day_start':'16:00','customer_phone_required':False,'delivery_workflow':'manager_dispatch',
             'theme':cat['brand'].get('theme',{}),'services':branches['brand_services']}
   c.execute('UPDATE app_settings SET settings=settings||%s WHERE brand_id=%s',(db.json(settings),brand))
   for b in branches['branches']:
    loc=b.get('location') or {'lat':28.4,'lng':45.98}
    existing=c.execute('SELECT settings FROM branches WHERE id=%s',(b['id'],)).fetchone()
    bs=dict(existing['settings']) if existing else {}
    bs.update(active=True,ordering_verified=True,operational_data_verified=True,location=loc,
     location_verified=bool(b.get('location_verified')),payment_methods=b.get('payment_methods',['cash','card']),
     delivery_enabled=bool(b.get('delivery_eligible')),pickup_enabled=False,car_enabled=False)
    if bs.get('delivery_fee_halalas') is None:bs['delivery_fee_halalas']=0
    if bs.get('minimum_order_halalas') is None:bs['minimum_order_halalas']=1000
    if not bs.get('coverage'):bs['coverage']={'type':'radius','center':loc,'radius_meters':15000}
    bs['opening_hours']=[{'days':list(range(7)),'start':'00:00','end':'00:00'}]
    bs['delivery_hours']=[{'days':list(range(7)),'start':'16:00','end':'04:00'}]
    c.execute('INSERT INTO branches(id,brand_id,branch_no,source_id,name,city,settings) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET settings=EXCLUDED.settings,name=EXCLUDED.name',
     (b['id'],brand,b['branch_no'],'training:'+b['id'],b['name'],b['city'],db.json(bs)))
   for p in cat['products']:
    ps={k:v for k,v in p.items() if k not in {'id','brand_id','name','category','price_halalas','available','unavailable_reason'}}
    c.execute('INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name,category=EXCLUDED.category,price_halalas=EXCLUDED.price_halalas,settings=EXCLUDED.settings',
     (p['id'],brand,p['name'],p['category'],p['price_halalas'],db.json(ps)))
   from app.catalog import commercial_digest
   for product in c.execute('SELECT * FROM products WHERE brand_id=%s',(brand,)).fetchall():
    digest=commercial_digest(product)
    c.execute("UPDATE products SET settings=settings||%s WHERE id=%s",(db.json({'owner_approved_digest':digest}),product['id']))
   c.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled) VALUES(%s,'guest','active',true) ON CONFLICT(brand_id,channel) DO UPDATE SET state='active',enabled=true",(brand,))
  branches={b:[r['id'] for r in c.execute('SELECT id FROM branches WHERE brand_id=%s',(b,)).fetchall()] for b in ('kps','enbishan')}
  specs=[('owner','owner',[]),('manager','delivery_manager',[]),('evening','delivery_manager',[]),('morning','delivery_manager',[]),
         ('driver','driver',[]),('driver2','driver',[]),('branch','branch_manager',[]),('accountant','admin',['accounting_reports','accounting_review','accounting_cases_followup']),
         ('fleet','admin',['vehicle_maintenance_manage','fleet_active_swap'])]
  extra={'manager':['reports_supervise','driver_duty_manage','driver_support_approve','orders_operational_view','complimentary_approve','orders_cancel_after_print','orders_cancel_collected','branch_availability']}
  for index,(name,role,perms) in enumerate(specs):
   scope=['kps-branch-2'] if name in {'morning','branch'} else branches['kps']
   denials=[]
   if name=='manager':denials=['driver_terminal','payments_collect','custody_transfer','shifts_approve','shifts_delegate_close']
   if name=='evening':denials=['accounting_cases_followup']
   if name=='morning':denials=sorted(employee_permissions.eligible(role)-{'orders_view','orders_contact','orders_print','chat_view','chat_reply'})
   c.execute('INSERT INTO users(id,display_name,role,brand_id,branch_ids,permissions,extra_permissions,permission_denials,phone,invitation_status) VALUES(%s,%s,%s,\'kps\',%s,%s,%s,%s,%s,\'active\')',
    ('train-'+name,ROLES.get(name,'السائق الثاني')+' — تدريب',role,db.json(scope),db.json(perms),db.json(extra.get(name,[])),db.json(denials),'+96650000'+str(index+1000)))
   c.execute('INSERT INTO staff_brand_memberships(user_id,brand_id,branch_ids,granted_by) VALUES(%s,\'enbishan\',%s,\'train-owner\')',('train-'+name,db.json(branches['enbishan'])))
  for i,brand in enumerate(('kps','enbishan')):
   c.execute("INSERT INTO users(id,display_name,role,brand_id,phone,identity_kind,phone_verified_at,phone_sms_verified_at) VALUES(%s,'زبون تدريب','customer',%s,%s,'phone_sms_verified',now(),now())",('train-customer-'+brand,brand,'+96650000900'+str(i)))
   for name,kind,manager in [('evening','manager',None),('driver','driver','train-shift-evening-'+brand)]:
    vl={'version':1,'vehicle':{'plate':'ت د ر 1001','type':'سيدان','odometer_km':1000},'start':{'odometer_km':1000,'fuel_percent':100,'checks':{k:'ok' for k in ('tyres','brakes','lights','oil','damage','cleanliness')}},'refuels':[]} if kind=='driver' else {}
    c.execute('INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id,opening_halalas,vehicle_log) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
     ('train-shift-'+name+'-'+brand,kind,'train-'+name,brand,db.json(branches[brand]),manager,20000 if kind=='driver' else 0,db.json(vl)))
   branch='kps-branch-2' if brand=='kps' else branches[brand][0]
   for number in range(1,5):
    oid='train-order-'+brand+'-'+str(number)
    snap={'items':[{'product_id':'training-item','name':'طلب تدريب','quantity':1,'unit_price_halalas':2500,'line_total_halalas':2500,'units':[]}],
          'branch_name':'حفر الباطن - الخالدية' if brand=='kps' else 'عنبيشن 1','delivery_workflow':'manager_dispatch','payment_method':'cash','delivery_period':'evening'}
    customer={'name':'زبون تدريب '+str(number),'phone':'+966500009000','neighborhood':'حي التدريب','location':{'lat':28.4,'lng':45.98}}
    c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,%s,%s,%s,'delivery',%s,%s,'cash',2500,0,2500,%s,%s)",
      (oid,brand,branch,'train-customer-'+brand,'handed_over' if number==1 else 'ready','picked_up' if number==1 else 'pending',db.json(snap),db.json(customer)))
    db.event(c,oid,'train-evening','order.created',{'training':True})
    if number==1:c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,accepted_at,picked_up_at) VALUES(%s,%s,'train-driver',%s,'picked_up','train-evening',now(),now())",('train-task-'+brand,oid,'train-shift-driver-'+brand))
  c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'",(db.json({'morning_delivery':{'version':1,'enabled':True,'execution_branch_ids':['kps-branch-2'],'manager_user_ids':['train-morning'],'preparer_user_ids':['train-branch']}}),))
  c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,delivery_window_snapshot) VALUES('train-morning-shift','manager','train-morning','kps','[\"kps-branch-2\"]','{\"period\":\"morning\"}')")
  # No real employee contact, provider configuration or operational records.
 db.close_pools()

def build():
 base=configure()
 from app import db, storefront_scope, sms_verification, auth
 seed()
 db.POOL_MIN_SIZE=0;db.POOL_MAX_SIZE=2
 original_pool=db._pool_for_url
 def room_pool(url):
  room=ROOM.get()
  if room:url=base.rsplit('/',1)[0]+'/'+room
  return original_pool(url)
 db._pool_for_url=room_pool
 # Original brand rules, with a training-only selector for the two storefronts.
 original_bound=storefront_scope.bound_brand
 def bound(request):
  if auth.session_context(request)=='work':return original_bound(request)
  if BRAND.get():return BRAND.get()
  return original_bound(request)
 storefront_scope.bound_brand=bound
 # External messaging is replaced only at the transport boundary, as in the
 # repository's real-HTTP acceptance fixture. Business/auth rules stay intact.
 references=set()
 def sms_start(phone,reference,lang='ar'):
  references.add(reference);return reference,datetime.now(timezone.utc)+timedelta(minutes=5)
 sms_verification.start=sms_start
 sms_verification.check=lambda reference,code:reference in references and code=='0123'
 auth._provider_ready=lambda provider:None
 from app.main import app as original
 from starlette.requests import Request
 from starlette.responses import HTMLResponse,JSONResponse,RedirectResponse,FileResponse
 from starlette.applications import Starlette
 from starlette.routing import Route,Mount
 import psycopg
 from psycopg import sql

 def new_room():
  with ROOM_LOCK:return make_room()
 def make_room():
  db.close_pools(base)
  now=time.monotonic()
  for key,last in list(ROOMS.items()):
   if now-last>3600:
    db.close_pools(base.rsplit('/',1)[0]+'/'+key)
    with psycopg.connect(base,autocommit=True) as c:c.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(key)))
    ROOMS.pop(key,None)
  if len(ROOMS)>=24:raise RuntimeError('TRAINING_CAPACITY')
  name='test_enbishan_train_'+secrets.token_hex(8)
  with psycopg.connect(base,autocommit=True) as c:c.execute(sql.SQL('CREATE DATABASE {} TEMPLATE test_enbishan_template').format(sql.Identifier(name)))
  ROOMS[name]=now;return name

 async def landing(request):return FileResponse(HERE/'portal.html')
 async def status(request):return JSONResponse({'training':True,'source_sha':SOURCE_SHA,'source_unchanged':True,'learner_isolation':'separate_database','real_orders':False,'real_provider_sends':False,'roles':ROLES,'scenarios':SCENARIOS})
 async def start(request):
  role=request.query_params.get('role','driver');brand=request.query_params.get('brand','kps')
  if role not in ROLES or brand not in {'kps','enbishan'}:return JSONResponse({'error':'Invalid training role'},status_code=400)
  room=request.cookies.get('training_room') if request.query_params.get('resume')=='1' else None
  if room not in ROOMS:
   try:room=await asyncio.to_thread(new_room)
   except RuntimeError:return HTMLResponse('غرف التدريب مشغولة الآن. جرّب بعد قليل.',status_code=503)
  token=ROOM.set(room)
  try:
   session,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
   uid='train-customer-'+brand if role=='customer' else 'train-'+role
   with db.tx() as c:
    c.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at,customer_verified_at) VALUES(%s,%s,%s,%s,now()+interval '2 hours',CASE WHEN %s THEN now() ELSE NULL END)",
     (db.new_id('train-session-'),auth.token_hash(session),auth.token_hash(csrf),uid,role=='customer'))
   response=RedirectResponse('/' if role=='customer' else '/ops',status_code=303)
   for name,value,http in [('training_room',room,True),('training_brand',brand,False),('training_role',role,False),('training_scenario',request.query_params.get('scenario','explore'),False),
      ('kps_customer_session' if role=='customer' else 'kps_work_session',session,True),('kps_customer_csrf' if role=='customer' else 'kps_work_csrf',csrf,False)]:
    response.set_cookie(name,value,max_age=7200,httponly=http,secure=os.environ['PUBLIC_BASE_URL'].startswith('https:'),samesite='lax')
   return response
  finally:ROOM.reset(token)

 async def training_asset(request):
  name=request.path_params['name']
  if name not in {'training.js','portal.css','portal.js'}:return JSONResponse({},status_code=404)
  return FileResponse(HERE/name)

 class Isolation:
  def __init__(self,app):self.app=app
  async def __call__(self,scope,receive,send):
   if scope['type']!='http':return await self.app(scope,receive,send)
   req=Request(scope);room=req.cookies.get('training_room');brand=req.cookies.get('training_brand','kps')
   path=scope['path']
   if path in {'/training','/training/','/training/start','/training/status'} or path.startswith('/training/assets/'):
    return await self.app(scope,receive,send)
   if path in {'/health','/ready'}:
    return await JSONResponse({'status':'ready','training':True,'source_sha':SOURCE_SHA})(scope,receive,send)
   if room not in ROOMS:
    if path.startswith('/api/'):return await JSONResponse({'detail':{'code':'TRAINING_ROOM_REQUIRED','message':'ابدأ جلسة تدريب جديدة.'}},status_code=401)(scope,receive,send)
    return await RedirectResponse('/training')(scope,receive,send)
   ROOMS[room]=time.monotonic()
   rt=ROOM.set(room);bt=BRAND.set(brand if brand in {'kps','enbishan'} else 'kps')
   if auth.session_context(req)=='work' and not req.headers.get('x-operations-brand'):
    scope={**scope,'headers':[*scope['headers'],(b'x-operations-brand',BRAND.get().encode())]}
   # A provider never receives training messages or a physical print command.
   if path.startswith(('/api/printing/setup','/api/printing/bridge','/api/recovery/restore','/api/identity/owners/','/api/identity/owner-phone')):
    ROOM.reset(rt);BRAND.reset(bt)
    return await JSONResponse({'detail':{'code':'TRAINING_EXTERNAL_DISABLED','message':'هذا الإجراء يربط جهازًا أو هوية حقيقية؛ غير متصل في التدريب.'}},status_code=409)(scope,receive,send)
   html_response=False
   async def wrapped(message):
    nonlocal html_response
    if message['type']=='http.response.start':
     headers=list(message.get('headers',[]));html_response=any(k==b'content-type' and b'text/html' in v for k,v in headers)
     if html_response:headers=[(k,v) for k,v in headers if k not in {b'content-length',b'content-security-policy'}]
     headers.append((b'x-kps-training',b'isolated'))
     message={**message,'headers':headers}
    elif message['type']=='http.response.body' and html_response and not message.get('more_body'):
     body=message.get('body',b'');injection=b'<script src="/training/assets/training.js" defer></script>'
     message={**message,'body':body.replace(b'</body>',injection+b'</body>') if b'</body>' in body else body+injection}
    await send(message)
   try:await self.app(scope,receive,wrapped)
   finally:ROOM.reset(rt);BRAND.reset(bt)

 outer=Starlette(routes=[Route('/training',landing),Route('/training/',landing),Route('/training/start',start),Route('/training/status',status),Route('/training/assets/{name}',training_asset),Mount('/',original)],lifespan=original.router.lifespan_context)
 return Isolation(outer)

if __name__=='__main__':
 import uvicorn
 app=build()
 uvicorn.run(app,host='0.0.0.0',port=int(os.getenv('PORT','8080')),workers=1,log_level='warning')
