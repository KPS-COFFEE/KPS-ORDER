"""Protected lightweight menu over the canonical catalog; public menu untouched."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import secrets
from fastapi import APIRouter, Depends, Request, Response, Query
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from . import auth, catalog, customer_information as information, db

router=APIRouter()
PUBLIC=Path(__file__).resolve().parents[1]/'public'
preview_access=auth.require_permission('products_manage')
TOKEN=re.compile(r'^[A-Za-z0-9_-]{43}$')
MENU_COOKIE='kps_menu_session'
ATTR_COOKIE='kps_menu_attribution'
EVENTS={'visit','category_view','product_view','branches_open','branch_view','maps_click','review_click','social_click','contact_click'}


def private_headers(response):
    response.headers['Cache-Control']='private, no-store'
    response.headers['X-Robots-Tag']='noindex, nofollow, noarchive'
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
    return response


@router.get('/menu/preview')
def preview(user=Depends(preview_access)):
    return private_headers(FileResponse(PUBLIC/'menu-preview.html',media_type='text/html'))


@router.get('/menu/analytics')
def dashboard(user=Depends(preview_access)):
    if user['role']!='owner': raise db.error(403,'ROLE_FORBIDDEN','إحصاءات المنيو متاحة للمالك.')
    return private_headers(FileResponse(PUBLIC/'menu-analytics.html',media_type='text/html'))


@router.get('/menu/information')
def information_editor(user=Depends(auth.require_roles('owner'))):
    return private_headers(FileResponse(PUBLIC/'menu-information.html',media_type='text/html'))


@router.get('/menu/assets/{filename}')
def assets(filename:str,user=Depends(preview_access)):
    if filename not in {'menu-preview.js','menu-preview.css','menu-analytics.js','menu-information.js'}: raise db.error(404,'FILE_NOT_FOUND','الملف غير موجود.')
    return private_headers(FileResponse(PUBLIC/filename))


def menu_session(conn,request,brand_id,response=None):
    token=request.cookies.get(MENU_COOKIE,'')
    row=conn.execute('SELECT * FROM menu_sessions WHERE id=%s AND brand_id=%s AND preview AND expires_at>now()',
                     (token,brand_id)).fetchone() if TOKEN.fullmatch(token) else None
    if row: return row
    if response is None: raise db.error(409,'MENU_SESSION_REQUIRED','افتح المعاينة أولًا.')
    token=secrets.token_urlsafe(32)
    row=conn.execute('INSERT INTO menu_sessions(id,brand_id) VALUES(%s,%s) RETURNING *',(token,brand_id)).fetchone()
    response.set_cookie(MENU_COOKIE,token,httponly=True,secure=auth.secure_cookie(),samesite='lax',max_age=30*86400,path='/')
    return row


@router.get('/api/menu/preview')
def preview_data(request:Request,response:Response,user=Depends(preview_access)):
    result=catalog.catalog(brand_id=user['brand_id'],branch_id=None)
    # The generic menu cannot judge city/branch restrictions. Global flags and
    # required globally enabled options use the existing availability engine.
    with db.tx() as conn:
        raw=conn.execute("SELECT * FROM products WHERE brand_id=%s AND coalesce(settings->>'lifecycle','active')<>'draft' AND coalesce(settings->>'purchasable','true')<>'false' ORDER BY id",(user['brand_id'],)).fetchall()
        lookup={p['id']:p for p in raw}
        for product in result['products']:
            original=lookup[product['id']]
            from . import print_translations
            translated=print_translations.labels(original)
            product.setdefault('name_en',translated.get(original['name'],''))
            product.setdefault('category_en',{'☕ الأكواب الحرارية':'☕ Thermal cups','☕ المشروبات':'☕ Drinks','✨ جديدنا':'✨ New arrivals','⭐ الأكثر مبيعاً':'⭐ Best sellers','🍪 السناكس والحلويات':'🍪 Snacks & desserts','🍹 المنعشات':'🍹 Refreshments','📦 بوكسات الضيافة':'📦 Sharing boxes','🥤 الملحقات':'🥤 Extras'}.get(original['category'],''))
            for group in product['option_groups']:
                group.setdefault('name_en',translated.get(group.get('name'),''))
                for value in group['values']: value.setdefault('name_en',translated.get(value.get('name'),''))
            global_product=dict(original,id='generic:'+original['id'],settings=original['settings']|{'available_cities':[]})
            product['globally_available'],_=catalog.product_availability(global_product,None)
        branches=conn.execute('SELECT * FROM branches WHERE brand_id=%s ORDER BY branch_no',(user['brand_id'],)).fetchall()
        prefs=conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(user['brand_id'],)).fetchone()
        settings=(prefs or {}).get('settings',{})
        links=settings.get('customer_links',[])
        try: information.validate_links(links)
        except Exception: links=[]
        result['customer_links']=links
        result['branches']=[{**{k:b[k] for k in ('id','name','city','branch_no')},
                             'maps_url':catalog.directions_url(b),
                             **information.public_information(b)} for b in branches if b['settings'].get('active',True) is True]
        menu_session(conn,request,user['brand_id'],response)
    result['preview']=True
    result['public_activation_enabled']=False  # no activation endpoint in this release
    private_headers(response)
    return result


class Event(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str=Field(pattern=r'^[A-Za-z0-9_-]{10,80}$')
    event_type:str=Field(max_length=30)
    target:str=Field(default='',max_length=200)


@router.post('/api/menu/events')
def record_event(body:Event,request:Request,user=Depends(preview_access)):
    if body.event_type not in EVENTS: raise db.error(422,'MENU_EVENT_INVALID','التفاعل غير صالح.')
    with db.tx() as conn:
        session=menu_session(conn,request,user['brand_id'])
        if not db.rate_limit(conn,'menu:'+session['id'],240,60): raise db.error(429,'MENU_RATE_LIMIT','تفاعلات كثيرة.')
        target=body.target
        if body.event_type=='product_view':
            valid=conn.execute('SELECT 1 FROM products WHERE id=%s AND brand_id=%s',(target,user['brand_id'])).fetchone()
        elif body.event_type=='category_view':
            valid=conn.execute('SELECT 1 FROM products WHERE category=%s AND brand_id=%s',(target,user['brand_id'])).fetchone()
        elif body.event_type in {'branch_view','maps_click','review_click','social_click','contact_click'} and conn.execute('SELECT 1 FROM branches WHERE id=%s AND brand_id=%s',(target,user['brand_id'])).fetchone():
            valid=conn.execute('SELECT 1 FROM branches WHERE id=%s AND brand_id=%s',(target,user['brand_id'])).fetchone()
        elif body.event_type in {'maps_click','social_click','contact_click'}:
            config=conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(user['brand_id'],)).fetchone()
            links=(config or {}).get('settings',{}).get('customer_links',[])
            expected={'maps_click':'maps','social_click':'social','contact_click':'contact'}[body.event_type]
            valid=any(l.get('id')==target and l.get('kind')==expected for l in links)
        else: valid=target==''
        if not valid: raise db.error(422,'MENU_TARGET_INVALID','الهدف خارج الكتالوج.')
        conn.execute('INSERT INTO menu_events(id,session_id,event_type,target) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                     (session['id']+':'+body.id,session['id'],body.event_type,target))
    return {'recorded':True,'preview':True}


@router.get('/menu/handoff')
def handoff(request:Request,user=Depends(preview_access)):
    with db.tx() as conn:
        session=menu_session(conn,request,user['brand_id'])
        if not db.rate_limit(conn,'menu-handoff:'+session['id'],30,60): raise db.error(429,'MENU_RATE_LIMIT','انتظر قليلًا.')
        token=secrets.token_urlsafe(32)
        conn.execute('INSERT INTO menu_handoffs(id,session_id) VALUES(%s,%s)',(token,session['id']))
        conn.execute('INSERT INTO menu_events(id,session_id,event_type) VALUES(%s,%s,\'order_click\')',(db.new_id('me_'),session['id']))
    # Relative URL in local tests; fixed first-party public order destination in production.
    base='' if __import__('os').getenv('APP_ENV') in {'testing','development'} else 'https://order.kps.sa'
    return private_headers(RedirectResponse(base+'/?source=menu_qr&menu_ref='+token,status_code=303))


def adopt_handoff(request,response):
    token=request.query_params.get('menu_ref','')
    if request.query_params.get('source')!='menu_qr' or not TOKEN.fullmatch(token): return
    with db.tx() as conn:
        row=conn.execute('SELECT h.*,s.brand_id FROM menu_handoffs h JOIN menu_sessions s ON s.id=h.session_id WHERE h.id=%s AND h.expires_at>now() AND s.expires_at>now()', (token,)).fetchone()
        requested_brand=request.query_params.get('brand_id','kps')
        if not row or row['brand_id']!=requested_brand: return
        updated=conn.execute('UPDATE menu_handoffs SET started_at=now() WHERE id=%s AND started_at IS NULL RETURNING id',(token,)).fetchone()
        if updated: conn.execute("INSERT INTO menu_events(id,session_id,event_type) VALUES(%s,%s,'order_start')",(db.new_id('me_'),row['session_id']))
        response.set_cookie(ATTR_COOKIE,token,httponly=True,secure=auth.secure_cookie(),samesite='lax',max_age=86400,path='/')


def attribute_order(conn,request,order):
    if request is None: return
    token=request.cookies.get(ATTR_COOKIE,'')
    if not TOKEN.fullmatch(token): return
    row=conn.execute('SELECT h.*,s.brand_id FROM menu_handoffs h JOIN menu_sessions s ON s.id=h.session_id '
                     'WHERE h.id=%s AND h.started_at IS NOT NULL AND h.expires_at>now() AND s.expires_at>now()', (token,)).fetchone()
    if row and row['brand_id']==order['brand_id']:
        conn.execute('INSERT INTO menu_order_attribution(order_id,session_id,handoff_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                     (order['id'],row['session_id'],token))


@router.patch('/api/menu/customer-links')
def update_links(body:dict,user=Depends(auth.require_permission('appearance_manage'))):
    if set(body)!={'links'}: raise db.error(422,'CUSTOMER_LINK_INVALID','الحقول غير صالحة.')
    links=information.validate_links(body['links'])
    with db.tx() as conn:
        conn.execute("UPDATE app_settings SET settings=jsonb_set(settings,'{customer_links}',%s),updated_at=now() WHERE brand_id=%s",(db.json(links),user['brand_id']))
        db.audit(conn,user['id'],'customer.links_updated','brand',user['brand_id'],{'link_ids':[l['id'] for l in links]})
    return {'links':links}


def period_range(period,start,end):
    from zoneinfo import ZoneInfo
    local=ZoneInfo('Asia/Riyadh');today=datetime.now(local).date()
    if period=='custom':
        try:
            a=datetime.fromisoformat(start);b=datetime.fromisoformat(end)
            if a.tzinfo is not None or b.tzinfo is not None or a.time()!=datetime.min.time() or b.time()!=datetime.min.time(): raise ValueError()
            a=a.replace(tzinfo=local);b=b.replace(tzinfo=local)+timedelta(days=1)
        except (ValueError,TypeError): raise db.error(422,'MENU_RANGE_INVALID','أدخل تاريخ البداية والنهاية.')
    elif period in {'today','7d','30d'}:
        days={'today':1,'7d':7,'30d':30}[period]
        a=datetime.combine(today-timedelta(days=days-1),datetime.min.time(),local)
        b=datetime.combine(today+timedelta(days=1),datetime.min.time(),local)
    else: raise db.error(422,'MENU_RANGE_INVALID','الفترة غير صالحة.')
    if not 0<(b-a).days<=366: raise db.error(422,'MENU_RANGE_INVALID','اختر فترة حتى سنة.')
    return a,b


@router.get('/api/menu/analytics')
def analytics(period:str='today',start:str|None=Query(None,max_length=10),end:str|None=Query(None,max_length=10),
              preview:bool=True,user=Depends(auth.require_roles('owner'))):
    a,b=period_range(period,start,end)
    with db.tx() as conn:
        counts=conn.execute('SELECT e.event_type,count(*)::int AS count FROM menu_events e JOIN menu_sessions s ON s.id=e.session_id '
                            'WHERE s.brand_id=%s AND s.preview=%s AND e.created_at>=%s AND e.created_at<%s GROUP BY e.event_type',
                            (user['brand_id'],preview,a,b)).fetchall()
        metrics={c['event_type']:c['count'] for c in counts}
        unique=conn.execute("SELECT count(DISTINCT s.id)::int AS n FROM menu_events e JOIN menu_sessions s ON s.id=e.session_id WHERE s.brand_id=%s AND s.preview=%s AND e.event_type='visit' AND e.created_at>=%s AND e.created_at<%s",(user['brand_id'],preview,a,b)).fetchone()['n']
        top=conn.execute("SELECT e.target AS product_id,p.name,count(*)::int AS views FROM menu_events e JOIN menu_sessions s ON s.id=e.session_id JOIN products p ON p.id=e.target AND p.brand_id=s.brand_id WHERE s.brand_id=%s AND s.preview=%s AND e.event_type='product_view' AND e.created_at>=%s AND e.created_at<%s GROUP BY e.target,p.name ORDER BY views DESC LIMIT 10",(user['brand_id'],preview,a,b)).fetchall()
        hours=conn.execute("SELECT extract(hour from e.created_at AT TIME ZONE 'Asia/Riyadh')::int AS hour,count(*)::int AS visits FROM menu_events e JOIN menu_sessions s ON s.id=e.session_id WHERE s.brand_id=%s AND s.preview=%s AND e.event_type='visit' AND e.created_at>=%s AND e.created_at<%s GROUP BY hour ORDER BY hour",(user['brand_id'],preview,a,b)).fetchall()
        days=conn.execute("SELECT (e.created_at AT TIME ZONE 'Asia/Riyadh')::date AS day,count(*)::int AS visits FROM menu_events e JOIN menu_sessions s ON s.id=e.session_id WHERE s.brand_id=%s AND s.preview=%s AND e.event_type='visit' AND e.created_at>=%s AND e.created_at<%s GROUP BY day ORDER BY day",(user['brand_id'],preview,a,b)).fetchall()
        category=conn.execute("SELECT e.target AS category,count(*)::int AS views FROM menu_events e JOIN menu_sessions s ON s.id=e.session_id WHERE s.brand_id=%s AND s.preview=%s AND e.event_type='category_view' AND e.created_at>=%s AND e.created_at<%s GROUP BY e.target ORDER BY views DESC",(user['brand_id'],preview,a,b)).fetchall()
        # Count revenue by completion date. Refunds are deliberately not described as net revenue.
        rows=conn.execute('SELECT o.*,s.id AS menu_session_id FROM menu_order_attribution x JOIN menu_sessions s ON s.id=x.session_id JOIN orders o ON o.id=x.order_id AND o.brand_id=s.brand_id '
                          'WHERE s.brand_id=%s AND s.preview=%s AND (o.created_at>=%s AND o.created_at<%s OR o.completed_at>=%s AND o.completed_at<%s)',(user['brand_id'],preview,a,b,a,b)).fetchall()
        completed=[o for o in rows if o['completed_at'] and a<=o['completed_at']<b and o['payment_status']=='paid' and o['preparation_status']!='cancelled' and o['delivery_status']!='cancelled']
        revenue=sum(o['total_halalas'] for o in completed)
        by_branch={};items={}
        for o in rows:
            v=by_branch.setdefault(o['branch_id'],{'branch_id':o['branch_id'],'attributed_created':0,'completed':0,'revenue_halalas':0})
            if a<=o['created_at']<b: v['attributed_created']+=1
            if o in completed:
                v['completed']+=1;v['revenue_halalas']+=o['total_halalas']
                for i in o['snapshot'].get('items',[]):
                    pid=i['product_id'];v2=items.setdefault(pid,{'product_id':pid,'name':i.get('name',pid),'quantity':0})
                    v2['quantity']+=i['quantity']
        # Cohort rates use orders CREATED in range and their current completion status;
        # never divide unrelated completion-period orders by today's menu visitors.
        cohort=[o for o in rows if a<=o['created_at']<b]
        cohort_completed=[o for o in completed if o in cohort]
        names={r['id']:r['name'] for r in conn.execute('SELECT id,name FROM branches WHERE brand_id=%s',(user['brand_id'],)).fetchall()}
        for v in by_branch.values():
            v['name']=names.get(v['branch_id'],v['branch_id'])
            done=sum(o in cohort_completed for o in cohort if o['branch_id']==v['branch_id'])
            v['created_order_completion_rate']=done/v['attributed_created'] if v['attributed_created'] else None
        visiting_ids={r['session_id'] for r in conn.execute("SELECT DISTINCT e.session_id FROM menu_events e JOIN menu_sessions s ON s.id=e.session_id WHERE s.brand_id=%s AND s.preview=%s AND e.event_type='visit' AND e.created_at>=%s AND e.created_at<%s",(user['brand_id'],preview,a,b)).fetchall()}
        converted=len({o['menu_session_id'] for o in cohort_completed}&visiting_ids)
    return {'preview':preview,'range':{'start':a,'end_exclusive':b,'timezone':'Asia/Riyadh'},'metrics':metrics,
            'unique_sessions':unique,'visit_to_completed_order_rate':converted/unique if unique else None,
            'completed_orders':len(completed),'gross_revenue_halalas':revenue,'average_order_value_halalas':revenue//len(completed) if completed else None,
            'top_products':top,'top_ordered_products':sorted(items.values(),key=lambda i:-i['quantity'])[:10],
            'categories':category,'hours':hours,'days':days,'order_branches':list(by_branch.values()),
            'limitations':['Shared QR: physical scan branch is unknown.','Clicks are interactions, not physical visits.','Preview data is separate from live data.','Revenue is paid completed gross order value before refunds.','Branch rate: completed attributed orders / attributed orders created in range.']}
