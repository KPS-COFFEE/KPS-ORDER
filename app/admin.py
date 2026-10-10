"""Scoped operational configuration and scheduled first-party banners."""
from __future__ import annotations

import hashlib
import io
import re
import math
from datetime import datetime

from fastapi import APIRouter, Depends, File, Header, Response, UploadFile
from PIL import Image, UnidentifiedImageError
from . import auth, db, catalog, email_delivery

router = APIRouter(prefix='/api')
owner = auth.require_roles('owner')
DELIVERY_CITIES = {'حفرالباطن', 'الذيبية', 'القيصومة', 'الخفجي', 'رفحاء', 'قبه'}


def city(value):
    normalized = re.sub(r'\s+', '', catalog.normalize_city(str(value)))
    return 'رفحاء' if normalized == 'رفحا' else normalized


def scoped(conn, table, record_id, user):
    if table not in {'branches', 'products', 'users', 'banners', 'assets'}:
        raise ValueError('unsupported scoped table')
    row = conn.execute(f'SELECT * FROM {table} WHERE id=%s AND brand_id=%s FOR UPDATE', (record_id, user['brand_id'])).fetchone()
    if not row:
        raise db.error(404, 'RECORD_NOT_FOUND', 'السجل غير موجود ضمن صلاحياتك.')
    return row


def money(value, field):
    if type(value) is not int or not 0 <= value <= 100_000_000:
        raise db.error(422, 'MONEY_INVALID', f'{field}: أدخل المبلغ بالهللات كعدد صحيح غير سالب.')
    return value


def text(value, maximum, field):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise db.error(422, 'TEXT_INVALID', f'{field}: قيمة غير صالحة.')
    return value.strip()


def keys(body, allowed):
    if not isinstance(body, dict) or set(body) - set(allowed):
        raise db.error(422, 'CONFIGURATION_FIELDS_INVALID', 'تحتوي البيانات حقولًا غير مسموحة.')


def branch_ids(conn, values, brand):
    if not isinstance(values, list) or len(values) > 500 or any(not isinstance(x, str) for x in values):
        raise db.error(422, 'BRANCH_SCOPE_INVALID', 'نطاق الفروع غير صحيح.')
    if len(set(values)) != len(values):
        raise db.error(422, 'BRANCH_SCOPE_INVALID', 'أزل الفروع المكررة.')
    for value in values:
        if not conn.execute('SELECT 1 FROM branches WHERE id=%s AND brand_id=%s', (value, brand)).fetchone():
            raise db.error(422, 'BRANCH_SCOPE_INVALID', 'أحد الفروع خارج البراند.')
    return values


def coordinate(value):
    if not isinstance(value, dict) or set(value) != {'lat', 'lng'}:
        raise db.error(422, 'COORDINATE_INVALID', 'الموقع يحتاج خط عرض وطول فعليين.')
    if any(type(value[k]) not in {int, float} or not math.isfinite(value[k]) for k in ('lat', 'lng')) or not -90 <= value['lat'] <= 90 or not -180 <= value['lng'] <= 180:
        raise db.error(422, 'COORDINATE_INVALID', 'الموقع غير صحيح.')
    return value


def hours(value):
    if not isinstance(value, list) or not value or len(value) > 28:
        raise db.error(422, 'HOURS_INVALID', 'أدخل ساعات العمل الفعلية.')
    for row in value:
        if not isinstance(row, dict) or set(row) != {'days', 'start', 'end'}:
            raise db.error(422, 'HOURS_INVALID', 'صيغة ساعات العمل غير صحيحة.')
        if not isinstance(row['days'], list) or not row['days'] or any(type(d) is not int or not 0 <= d <= 6 for d in row['days']):
            raise db.error(422, 'HOURS_INVALID', 'الأيام من الاثنين 0 إلى الأحد 6.')
        for field in ('start', 'end'):
            if not isinstance(row[field], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', row[field]):
                raise db.error(422, 'HOURS_INVALID', 'الوقت بصيغة HH:MM وتوقيت الرياض.')
    return value


def validate_options(groups, variants=None):
    if not isinstance(groups,list) or len(groups)>30:
        raise db.error(422,'OPTIONS_INVALID','مجموعات الخيارات غير صحيحة.')
    seen=set()
    for group in groups:
        if not isinstance(group,dict):
            raise db.error(422,'OPTIONS_INVALID','مجموعة الخيارات تحتاج حقولًا واضحة.')
        code=group.get('code') or group.get('id')
        if not isinstance(code,str) or not 1<=len(code)<=80 or code in seen:
            raise db.error(422,'OPTIONS_INVALID','معرف مجموعة الخيارات مفقود أو مكرر.')
        seen.add(code);text(group.get('name',''),120,'اسم الخيار')
        if 'required' in group and type(group['required']) is not bool:
            raise db.error(422,'OPTIONS_INVALID','تحديد الخيار الإلزامي غير صحيح.')
        minimum=group.get('min_select',1 if group.get('required',True) else 0);maximum=group.get('max_select',1)
        if type(minimum) is not int or type(maximum) is not int or not 0<=minimum<=maximum<=30:
            raise db.error(422,'OPTIONS_INVALID','حدود الاختيار غير صحيحة.')
        values=group.get('values')
        if not isinstance(values,list) or not values or len(values)>100 or maximum>len(values):
            raise db.error(422,'OPTIONS_INVALID','حدد قيمًا فعلية كافية للخيارات.')
        codes=set()
        for value in values:
            if not isinstance(value,dict):
                raise db.error(422,'OPTIONS_INVALID','قيمة الخيار غير صحيحة.')
            value_code=value.get('code') or value.get('id')
            if not isinstance(value_code,str) or not 1<=len(value_code)<=80 or value_code in codes:
                raise db.error(422,'OPTIONS_INVALID','معرف قيمة الخيار مفقود أو مكرر.')
            codes.add(value_code);text(value.get('name',''),120,'اسم قيمة الخيار')
            money(value.get('price_delta_halalas',0),'فرق سعر الخيار')
            if 'enabled' in value and type(value['enabled']) is not bool:
                raise db.error(422,'OPTIONS_INVALID','تفعيل قيمة الخيار غير صحيح.')
    if variants is not None:
        if not isinstance(variants,list) or len(variants)>100:
            raise db.error(422,'VARIANTS_INVALID','قائمة الألوان غير صحيحة.')
        seen=set()
        for variant in variants:
            if not isinstance(variant,dict):
                raise db.error(422,'VARIANTS_INVALID','بيانات اللون غير صحيحة.')
            code=variant.get('sku') or variant.get('code') or variant.get('id')
            if not isinstance(code,str) or not 1<=len(code)<=120 or code in seen:
                raise db.error(422,'VARIANTS_INVALID','معرف اللون مفقود أو مكرر.')
            seen.add(code)
            if 'hex' in variant and not re.fullmatch(r'#[A-Fa-f0-9]{6}',str(variant['hex'])):
                raise db.error(422,'VARIANTS_INVALID','لون العرض غير صحيح.')


def validate_branch_settings(settings, branch_city):
    allowed = {'active','pickup_enabled','car_enabled','delivery_enabled','payment_methods','opening_hours','delivery_hours',
               'coverage','location','location_verified','map_url','map_url_verified','delivery_fee_halalas','minimum_order_halalas','ordering_verified','operational_data_verified'}
    keys(settings, allowed)
    for field in ('active','pickup_enabled','car_enabled','delivery_enabled','ordering_verified','operational_data_verified','location_verified','map_url_verified'):
        if field in settings and type(settings[field]) is not bool:
            raise db.error(422, 'BOOLEAN_INVALID', 'إعداد التفعيل يجب أن يكون صحيحًا أو خطأ.')
    for field in ('opening_hours','delivery_hours'):
        if settings.get(field) not in (None, []):
            hours(settings[field])
    for field in ('delivery_fee_halalas','minimum_order_halalas'):
        if settings.get(field) is not None:
            money(settings[field], field)
    if settings.get('location') is not None:
        coordinate(settings['location'])
    if settings.get('map_url') is not None and not catalog.safe_map_url(settings['map_url']):
        raise db.error(422,'MAP_URL_NOT_ALLOWED','اختر رابط Google Maps معتمدًا دون روابط خارجية.')
    if 'payment_methods' in settings and (not isinstance(settings['payment_methods'], list) or
            any(not isinstance(value, str) or value not in {'cash','card'} for value in settings['payment_methods'])):
        raise db.error(422, 'PAYMENT_METHOD_INVALID', 'طرق الدفع المسموحة كاش وشبكة عند الاستلام.')
    coverage = settings.get('coverage')
    if coverage is not None:
        if not isinstance(coverage, dict) or coverage.get('type') not in {'polygon','radius'}:
            raise db.error(422, 'COVERAGE_INVALID', 'أدخل نطاق الخدمة الفعلي.')
        if coverage['type'] == 'radius':
            keys(coverage, {'type','center','radius_meters'})
            coordinate(coverage.get('center'))
            if type(coverage.get('radius_meters')) not in {int,float} or not 0 < coverage['radius_meters'] <= 200_000:
                raise db.error(422, 'COVERAGE_INVALID', 'نطاق الخدمة غير صحيح.')
        else:
            keys(coverage, {'type','coordinates'})
            points = coverage.get('coordinates')
            if not isinstance(points, list) or not 3 <= len(points) <= 1000:
                raise db.error(422, 'COVERAGE_INVALID', 'نطاق الخدمة يحتاج ثلاث نقاط فعلية على الأقل.')
            for point in points:
                if not isinstance(point,list) or len(point)!=2:
                    raise db.error(422, 'COVERAGE_INVALID', 'النقطة بصيغة [خط الطول، خط العرض].')
                coordinate({'lng':point[0],'lat':point[1]})
    if settings.get('delivery_enabled') and city(branch_city) not in DELIVERY_CITIES:
        raise db.error(422, 'DELIVERY_CITY_FORBIDDEN', 'التوصيل معتمد للمدن الست فقط في الإطلاق الحالي.')


@router.get('/admin/config')
def configuration(user=Depends(owner)):
    with db.tx() as conn:
        result = {'brand': conn.execute('SELECT * FROM brands WHERE id=%s',(user['brand_id'],)).fetchone(),
                  'branches': conn.execute('SELECT * FROM branches WHERE brand_id=%s ORDER BY branch_no',(user['brand_id'],)).fetchall(),
                  'products': conn.execute('SELECT * FROM products WHERE brand_id=%s ORDER BY category,name',(user['brand_id'],)).fetchall(),
                  'users': conn.execute('SELECT id,username,display_name,role,brand_id,branch_ids,phone,active FROM users WHERE brand_id=%s ORDER BY role,display_name',(user['brand_id'],)).fetchall(),
                  'banners': conn.execute('SELECT * FROM banners WHERE brand_id=%s ORDER BY sort_order,created_at',(user['brand_id'],)).fetchall(),
                  'settings': conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(user['brand_id'],)).fetchone()['settings']}
    try:
        auth.provider_config()
        result['otp_configured'] = True
    except Exception:
        result['otp_configured'] = False
    return result


@router.patch('/admin/branches/{record_id}')
def update_branch(record_id: str, body: dict, user=Depends(owner), idem: str | None=Header(None,alias='Idempotency-Key')):
    keys(body, {'name','city','settings'})
    with db.tx() as conn:
        def perform():
            row = scoped(conn,'branches',record_id,user)
            name = text(body.get('name',row['name']),200,'اسم الفرع')
            branch_city = text(body.get('city',row['city']),100,'المدينة')
            changes = body.get('settings',{})
            validate_branch_settings(changes,branch_city)
            settings = row['settings'] | changes
            if 'map_url' in changes and changes['map_url'] is None:
                settings['map_url_verified'] = False
            if 'location' in changes and changes['location'] is None:
                settings['location_verified'] = False
            if settings.get('delivery_enabled') and city(branch_city) not in DELIVERY_CITIES:
                raise db.error(422,'DELIVERY_CITY_FORBIDDEN','أوقف التوصيل قبل نقل الفرع إلى مدينة غير معتمدة.')
            enabled = any(settings.get(f) for f in ('pickup_enabled','car_enabled','delivery_enabled'))
            if enabled and (not settings.get('ordering_verified') or not settings.get('opening_hours') or not settings.get('payment_methods')):
                raise db.error(422,'BRANCH_CONFIGURATION_INCOMPLETE','تحقق من التشغيل وساعات العمل وطرق الدفع قبل تفعيل الفرع.')
            if settings.get('delivery_enabled') and (any(not settings.get(k) for k in ('delivery_hours','coverage','location')) or settings.get('delivery_fee_halalas') is None):
                raise db.error(422,'DELIVERY_CONFIGURATION_INCOMPLETE','يلزم الموقع والتغطية والساعات والرسوم الفعلية قبل تفعيل التوصيل.')
            conn.execute('UPDATE branches SET name=%s,city=%s,settings=%s,updated_at=now() WHERE id=%s',(name,branch_city,db.json(settings),record_id))
            db.audit(conn,user['id'],'branch.updated','branch',record_id,{'fields':list(body),'settings_fields':list(changes)})
            return {'id':record_id,'name':name,'city':branch_city,'settings':settings}
        return db.idempotent(conn,user['id'],idem,'branch.update:'+record_id,body,perform)


@router.patch('/admin/products/{record_id}')
def update_product(record_id: str, body: dict, user=Depends(owner), idem: str | None=Header(None,alias='Idempotency-Key')):
    keys(body, {'name','category','price_halalas','settings'})
    with db.tx() as conn:
        def perform():
            row = scoped(conn,'products',record_id,user)
            changes = body.get('settings',{})
            keys(changes, {'enabled','source_verified','available_cities','unavailable_branch_ids','option_groups','variants','image_url','image_asset_id','description','lifecycle','available_branch_ids'})
            if 'lifecycle' in changes and changes['lifecycle'] not in {'draft','active','paused'}:
                raise db.error(422,'PRODUCT_LIFECYCLE_INVALID','حالة الصنف مسودة أو مفعل أو موقوف.')
            if 'lifecycle' in changes:
                changes['enabled']=changes['lifecycle']=='active'
            elif 'enabled' in changes:
                changes['lifecycle']='active' if changes['enabled'] is True else 'paused'
            if 'available_branch_ids' in changes:
                branch_ids(conn,changes['available_branch_ids'],user['brand_id'])
            for key in ('enabled','source_verified'):
                if key in changes and type(changes[key]) is not bool:
                    raise db.error(422,'BOOLEAN_INVALID','قيمة التفعيل غير صحيحة.')
            if 'unavailable_branch_ids' in changes:
                branch_ids(conn,changes['unavailable_branch_ids'],user['brand_id'])
            if 'available_cities' in changes and (not isinstance(changes['available_cities'],list) or any(not isinstance(x,str) or len(x)>100 for x in changes['available_cities'])):
                raise db.error(422,'CITY_POLICY_INVALID','سياسة توفر المدن غير صحيحة.')
            if 'available_cities' in changes:
                changes['availability_policy_version']=int(row['settings'].get('availability_policy_version',0))+1
            if changes.get('image_asset_id'):
                asset=scoped(conn,'assets',changes['image_asset_id'],user)
                if asset['purpose']!='product':
                    raise db.error(422,'PRODUCT_IMAGE_INVALID','اختر صورة منتج مرفوعة فعليًا.')
                changes['image_url']='/api/product-assets/'+asset['id']
            if 'image_url' in changes and not re.fullmatch(r'(?:/assets/[A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|webp|svg)|/api/product-assets/asset_[a-f0-9]{32})',changes['image_url']):
                raise db.error(422,'IMAGE_URL_INVALID','اختر صورة محلية معتمدة.')
            if changes.get('image_url','').startswith('/api/product-assets/'):
                asset=scoped(conn,'assets',changes['image_url'].rsplit('/',1)[1],user)
                if asset['purpose']!='product':
                    raise db.error(422,'PRODUCT_IMAGE_INVALID','صورة المنتج غير صحيحة.')
            for key in ('option_groups','variants'):
                if key in changes and (not isinstance(changes[key],list) or len(changes[key])>30):
                    raise db.error(422,'OPTIONS_INVALID','خيارات الصنف غير صحيحة.')
            validate_options(changes.get('option_groups',row['settings'].get('option_groups',[])),changes.get('variants',row['settings'].get('variants',[])))
            settings = row['settings'] | changes
            if settings.get('lifecycle') == 'active' and settings.get('source_verified') is not True:
                raise db.error(422,'PRODUCT_SOURCE_UNVERIFIED','اعتمد السعر والصورة والخيارات قبل تفعيل الصنف.')
            name = text(body.get('name',row['name']),200,'اسم الصنف')
            category = text(body.get('category',row['category']),150,'التصنيف')
            price = money(body.get('price_halalas',row['price_halalas']),'سعر الصنف')
            updated = dict(row, name=name, category=category, price_halalas=price, settings=settings)
            if changes.get('source_verified') is True:
                settings['owner_approved_digest'] = catalog.commercial_digest(updated)
            elif settings.get('menu_approval') and not catalog.source_approved(updated):
                settings['source_verified'] = False
            if 'image_url' in changes:
                settings['pending_owner_image'] = False
            conn.execute('UPDATE products SET name=%s,category=%s,price_halalas=%s,settings=%s,updated_at=now() WHERE id=%s',(name,category,price,db.json(settings),record_id))
            db.audit(conn,user['id'],'product.updated','product',record_id,{'fields':list(body),'settings_fields':list(changes)})
            return {'id':record_id,'name':name,'category':category,'price_halalas':price,'settings':settings}
        return db.idempotent(conn,user['id'],idem,'product.update:'+record_id,body,perform)


@router.post('/admin/users')
def create_user(body: dict, user=Depends(owner), idem: str | None=Header(None,alias='Idempotency-Key')):
    raise db.error(422,'STAFF_EMAIL_INVITATION_REQUIRED','أنشئ الموظف بدعوة بريد فردية من شاشة الموظفين.')

@router.patch('/admin/users/{record_id}')
def update_user(record_id: str, body: dict, user=Depends(owner), idem: str | None=Header(None,alias='Idempotency-Key')):
    keys(body, {'display_name','role','branch_ids','active','password'})
    with db.tx() as conn:
        def perform():
            row = scoped(conn,'users',record_id,user)
            if row['role']=='customer':
                raise db.error(403,'CUSTOMER_ACCOUNT_PROTECTED','لا يمكن تحويل حساب عميل إلى موظف.')
            role = body.get('role',row['role'])
            active = body.get('active',row['active'])
            if role not in auth.ROLES-{'customer'} or type(active) is not bool:
                raise db.error(422,'USER_CONFIGURATION_INVALID','إعدادات الموظف غير صحيحة.')
            if role != row['role'] and role != 'owner':
                raise db.error(403,'STAFF_ROLE_INVITATION_REQUIRED','تغيير دور الموظف يحتاج مسار دعوة مخول.')
            if role == 'owner' and row['role'] != 'owner':
                raise db.error(403,'OWNER_INVITATION_REQUIRED','إضافة مالك تحتاج دعوة بريد واعتماد المالك الحالي.')
            if active and row.get('invitation_status') in {'draft','pending_email','pending_verification'}:
                raise db.error(403,'STAFF_EMAIL_VERIFICATION_REQUIRED','يلزم تحقق الموظف من بريده قبل التفعيل.')
            conn.execute('SELECT id FROM users WHERE brand_id=%s AND role=%s AND active ORDER BY id FOR UPDATE',(user['brand_id'],'owner')).fetchall()
            if row['role']=='owner' and (role!='owner' or not active):
                other = conn.execute('SELECT 1 FROM users WHERE brand_id=%s AND role=%s AND active AND id<>%s',(user['brand_id'],'owner',record_id)).fetchone()
                if not other:
                    raise db.error(409,'LAST_OWNER_PROTECTED','لا يمكن تعطيل آخر مالك مخوّل.')
            scope = branch_ids(conn,body.get('branch_ids',row['branch_ids']),user['brand_id'])
            if role!='owner' and not scope:
                raise db.error(422,'BRANCH_SCOPE_REQUIRED','حدد الفروع المصرح بها للموظف.')
            auth.ensure_safe_staff_scope_change(conn,row,scope,active)
            if role != row['role'] and conn.execute("SELECT 1 FROM shifts WHERE user_id=%s AND status<>'closed'",(record_id,)).fetchone():
                raise db.error(409,'OPEN_SHIFT_ROLE_PROTECTED','أغلق أو سلّم مسؤولية الشفت قبل تغيير الدور.')
            hashed = auth.hash_password(body['password']) if body.get('password') else row['password_hash']
            name = text(body.get('display_name',row['display_name']),200,'الاسم')
            updated = conn.execute('UPDATE users SET display_name=%s,role=%s,branch_ids=%s,active=%s,password_hash=%s,updated_at=now() WHERE id=%s RETURNING id,username,display_name,role,brand_id,branch_ids,phone,active',(name,role,db.json(scope),active,hashed,record_id)).fetchone()
            conn.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL',(record_id,))
            db.audit(conn,user['id'],'user.updated','user',record_id,{'fields':list(body)})
            return updated
        safe_body=body | ({'password':hashlib.sha256(body['password'].encode()).hexdigest()} if 'password' in body else {})
        return db.idempotent(conn,user['id'],idem,'user.update:'+record_id,safe_body,perform)


@router.patch('/admin/settings')
def update_settings(body: dict, user=Depends(owner), idem: str | None=Header(None,alias='Idempotency-Key')):
    keys(body, {'operational_day_start','late_after_minutes','theme','guest_operational_confirmation_above_halalas'})
    if 'operational_day_start' in body and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',str(body['operational_day_start'])):
        raise db.error(422,'OPERATIONAL_DAY_INVALID','وقت بداية اليوم التشغيلي بصيغة HH:MM.')
    if 'late_after_minutes' in body and (type(body['late_after_minutes']) is not int or not 1<=body['late_after_minutes']<=1440):
        raise db.error(422,'DELAY_SETTING_INVALID','حد التأخير من 1 إلى 1440 دقيقة.')
    if 'guest_operational_confirmation_above_halalas' in body and body['guest_operational_confirmation_above_halalas'] is not None:
        money(body['guest_operational_confirmation_above_halalas'],'حد طلب الاتصال التشغيلي')
    if 'theme' in body:
        theme=body['theme']; keys(theme,{'accent','background','text','font'})
        for key in {'accent','background','text'} & set(theme):
            if not re.fullmatch(r'#[A-Fa-f0-9]{6}',str(theme[key])):
                raise db.error(422,'THEME_INVALID','اللون بصيغة آمنة #RRGGBB.')
        if theme.get('font','Tahoma') not in {'Tahoma','Arial','sans-serif','Noto Kufi Arabic'}:
            raise db.error(422,'THEME_INVALID','اختر خطًا معتمدًا.')
    with db.tx() as conn:
        def perform():
            row=conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s FOR UPDATE',(user['brand_id'],)).fetchone()
            settings=row['settings']|body
            conn.execute('UPDATE app_settings SET settings=%s,updated_at=now() WHERE brand_id=%s',(db.json(settings),user['brand_id']))
            db.audit(conn,user['id'],'settings.updated','brand',user['brand_id'],{'fields':list(body)})
            return {'settings':settings}
        return db.idempotent(conn,user['id'],idem,'settings.update',body,perform)


@router.post('/admin/assets')
async def upload_asset(file: UploadFile=File(...), purpose: str='banner', user=Depends(owner), idem: str | None=Header(None,alias='Idempotency-Key')):
    if purpose not in {'banner','product'}:
        raise db.error(422,'ASSET_PURPOSE_INVALID','نوع الصورة غير صحيح.')
    contents=await file.read(5*1024*1024+1)
    if len(contents)>5*1024*1024 or not contents:
        raise db.error(413,'IMAGE_SIZE_INVALID','الصورة يجب ألا تتجاوز 5 ميجابايت.')
    try:
        with Image.open(io.BytesIO(contents)) as image:
            if image.format not in {'PNG','JPEG','WEBP'} or image.width>8000 or image.height>8000 or image.width*image.height>24_000_000:
                raise ValueError('unsupported image')
            image.load(); image.thumbnail((2400,1600))
            image=image.convert('RGB'); output=io.BytesIO(); image.save(output,format='JPEG',quality=88,optimize=True)
            processed=output.getvalue()
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):
        raise db.error(422,'IMAGE_TYPE_INVALID','ارفع صورة PNG أو JPEG أو WEBP سليمة.') from None
    digest=hashlib.sha256(processed).hexdigest()
    with db.tx() as conn:
        def perform():
            identifier=db.new_id('asset_')
            conn.execute('INSERT INTO assets(id,brand_id,owner_id,purpose,mime_type,filename,contents,sha256) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(identifier,user['brand_id'],user['id'],purpose,'image/jpeg',purpose+'.jpg',processed,digest))
            db.audit(conn,user['id'],purpose+'.asset.created','asset',identifier,{'sha256':digest,'bytes':len(processed)})
            return {'id':identifier,'preview_url':'/api/admin/assets/'+identifier,'mime_type':'image/jpeg'}
        return db.idempotent(conn,user['id'],idem,purpose+'.asset.create',{'sha256':digest},perform)


@router.get('/admin/assets/{record_id}')
def preview_asset(record_id:str,user=Depends(owner)):
    with db.tx() as conn:
        row=scoped(conn,'assets',record_id,user)
    return Response(bytes(row['contents']),media_type=row['mime_type'],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


def banner_fields(conn,body,user,existing=None):
    keys(body,{'title','body','button_label','destination','desktop_asset_id','mobile_asset_id','branch_ids','active','sort_order','starts_at','ends_at'})
    row=dict(existing or {'title':'','body':'','button_label':'','destination':{},'desktop_asset_id':None,'mobile_asset_id':None,'branch_ids':[],'active':False,'sort_order':0,'starts_at':None,'ends_at':None}) | body
    for key,limit in [('title',160),('body',320),('button_label',64)]:
        row[key]=text(row[key],limit,key)
    if not row['title'] or type(row['active']) is not bool or type(row['sort_order']) is not int or not 0<=row['sort_order']<=10_000:
        raise db.error(422,'BANNER_INVALID','العنوان والتفعيل والترتيب غير صحيحين.')
    row['branch_ids']=branch_ids(conn,row['branch_ids'],user['brand_id'])
    for key in ('desktop_asset_id','mobile_asset_id'):
        if row[key]:
            asset=scoped(conn,'assets',row[key],user)
            if asset['purpose']!='banner':
                raise db.error(422,'BANNER_ASSET_INVALID','الصورة ليست مخصصة للعروض.')
    destination=row['destination']
    keys(destination,{'kind','id'})
    if destination:
        if destination.get('kind')=='product':
            scoped(conn,'products',destination.get('id'),user)
        elif destination.get('kind')=='category':
            if not conn.execute('SELECT 1 FROM products WHERE category=%s AND brand_id=%s',(destination.get('id'),user['brand_id'])).fetchone():
                raise db.error(422,'BANNER_DESTINATION_INVALID','التصنيف غير موجود.')
        else:
            raise db.error(422,'BANNER_DESTINATION_INVALID','اختر صنفًا أو تصنيفًا فعليًا داخل المتجر.')
    for key in ('starts_at','ends_at'):
        if row[key] is not None:
            try:
                value=datetime.fromisoformat(row[key].replace('Z','+00:00')) if isinstance(row[key],str) else row[key]
                if value.tzinfo is None:
                    raise ValueError('timezone required')
                row[key]=value
            except (ValueError,AttributeError,TypeError):
                raise db.error(422,'BANNER_TIME_INVALID','أرسل وقتًا مع المنطقة الزمنية؛ الإدارة تعرض توقيت الرياض.') from None
    if row['starts_at'] and row['ends_at'] and row['ends_at']<=row['starts_at']:
        raise db.error(422,'BANNER_TIME_INVALID','نهاية الإعلان يجب أن تأتي بعد بدايته.')
    return {key:row[key] for key in ('title','body','button_label','destination','desktop_asset_id','mobile_asset_id','branch_ids','active','sort_order','starts_at','ends_at')}


@router.post('/admin/banners')
def create_banner(body:dict,user=Depends(owner),idem:str|None=Header(None,alias='Idempotency-Key')):
    with db.tx() as conn:
        def perform():
            row=banner_fields(conn,body,user); identifier=db.new_id('banner_')
            conn.execute('INSERT INTO banners(id,brand_id,title,body,button_label,destination,desktop_asset_id,mobile_asset_id,branch_ids,active,sort_order,starts_at,ends_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(identifier,user['brand_id'],row['title'],row['body'],row['button_label'],db.json(row['destination']),row['desktop_asset_id'],row['mobile_asset_id'],db.json(row['branch_ids']),row['active'],row['sort_order'],row['starts_at'],row['ends_at']))
            db.audit(conn,user['id'],'banner.created','banner',identifier,{'fields':list(body)})
            return {'id':identifier,**row}
        return db.idempotent(conn,user['id'],idem,'banner.create',body,perform)


@router.patch('/admin/banners/{record_id}')
def update_banner(record_id:str,body:dict,user=Depends(owner),idem:str|None=Header(None,alias='Idempotency-Key')):
    with db.tx() as conn:
        def perform():
            existing=scoped(conn,'banners',record_id,user); row=banner_fields(conn,body,user,existing)
            conn.execute('UPDATE banners SET title=%s,body=%s,button_label=%s,destination=%s,desktop_asset_id=%s,mobile_asset_id=%s,branch_ids=%s,active=%s,sort_order=%s,starts_at=%s,ends_at=%s,updated_at=now() WHERE id=%s',(row['title'],row['body'],row['button_label'],db.json(row['destination']),row['desktop_asset_id'],row['mobile_asset_id'],db.json(row['branch_ids']),row['active'],row['sort_order'],row['starts_at'],row['ends_at'],record_id))
            db.audit(conn,user['id'],'banner.updated','banner',record_id,{'fields':list(body)})
            return {'id':record_id,**row}
        return db.idempotent(conn,user['id'],idem,'banner.update:'+record_id,body,perform)


def active_banners(conn,brand_id,branch_id):
    if branch_id and not conn.execute('SELECT 1 FROM branches WHERE id=%s AND brand_id=%s',(branch_id,brand_id)).fetchone():
        raise db.error(404,'BRANCH_NOT_FOUND','الفرع غير موجود ضمن البراند.')
    return conn.execute("SELECT * FROM banners WHERE brand_id=%s AND active AND (starts_at IS NULL OR starts_at<=now()) AND (ends_at IS NULL OR ends_at>now()) AND (branch_ids='[]'::jsonb OR branch_ids ? %s) ORDER BY sort_order,created_at LIMIT 20",(brand_id,branch_id or '')).fetchall()


@router.get('/banners')
def public_banners(brand_id:str='kps',branch_id:str|None=None):
    with db.tx() as conn:
        rows=active_banners(conn,brand_id,branch_id)
    def asset_url(identifier):
        return f'/api/banner-assets/{identifier}?brand_id={brand_id}'+(f'&branch_id={branch_id}' if branch_id else '') if identifier else None
    return {'banners':[{'id':row['id'],'title':row['title'],'body':row['body'],'button_label':row['button_label'],'destination':row['destination'],'desktop_image_url':asset_url(row['desktop_asset_id']),'mobile_image_url':asset_url(row['mobile_asset_id'])} for row in rows]}


@router.get('/banner-assets/{record_id}')
def public_banner_asset(record_id:str,brand_id:str='kps',branch_id:str|None=None):
    with db.tx() as conn:
        banners=active_banners(conn,brand_id,branch_id)
        if not any(record_id in {row['desktop_asset_id'],row['mobile_asset_id']} for row in banners):
            raise db.error(404,'BANNER_ASSET_UNAVAILABLE','الصورة غير متاحة.')
        asset=conn.execute("SELECT contents,mime_type FROM assets WHERE id=%s AND brand_id=%s AND purpose='banner'",(record_id,brand_id)).fetchone()
        if not asset:
            raise db.error(404,'BANNER_ASSET_UNAVAILABLE','الصورة غير متاحة.')
    return Response(bytes(asset['contents']),media_type=asset['mime_type'],headers={'Cache-Control':'public, max-age=60','X-Content-Type-Options':'nosniff'})


@router.post('/admin/branches')
def create_branch(body:dict,user=Depends(owner),idem:str|None=Header(None,alias='Idempotency-Key')):
    keys(body,{'branch_no','name','city','settings','source_id'})
    branch_no=body.get('branch_no')
    if type(branch_no) is not int or not 1<=branch_no<=10000:
        raise db.error(422,'BRANCH_NUMBER_INVALID','أدخل رقم التشغيل الفعلي للفرع.')
    name=text(body.get('name'),200,'اسم الفرع'); branch_city=text(body.get('city'),100,'المدينة')
    if not name or not branch_city:
        raise db.error(422,'BRANCH_IDENTITY_REQUIRED','اسم الفرع ومدينته مطلوبان.')
    supplied_settings=body.get('settings',{})
    validate_branch_settings(supplied_settings,branch_city)
    if any(supplied_settings.get(key) for key in ('pickup_enabled','car_enabled','delivery_enabled')):
        raise db.error(422,'INITIAL_BRANCH_DISABLED','أنشئ الفرع متوقفًا ثم راجع بيانات تشغيله قبل التفعيل.')
    with db.tx() as conn:
        def perform():
            if conn.execute('SELECT 1 FROM branches WHERE brand_id=%s AND branch_no=%s',(user['brand_id'],branch_no)).fetchone():
                raise db.error(409,'BRANCH_NUMBER_EXISTS','رقم الفرع التشغيلي موجود؛ حدّث سجله دون إعادة إنشائه.')
            identifier=db.new_id('branch_')
            source_id=text(body.get('source_id','admin:'+identifier),200,'مرجع المصدر')
            if conn.execute('SELECT 1 FROM branches WHERE brand_id=%s AND source_id=%s',(user['brand_id'],source_id)).fetchone():
                raise db.error(409,'BRANCH_SOURCE_EXISTS','مرجع المصدر موجود لفرع آخر.')
            settings={'pickup_enabled':False,'car_enabled':False,'delivery_enabled':False,'ordering_verified':False}|supplied_settings
            row=conn.execute('INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *',(identifier,branch_no,source_id,user['brand_id'],name,branch_city,db.json(settings))).fetchone()
            db.audit(conn,user['id'],'branch.created','branch',identifier,{'branch_no':branch_no,'source_id':source_id})
            return {'branch':row}
        return db.idempotent(conn,user['id'],idem,'branch.create',body,perform)


@router.post('/admin/products')
def create_product(body:dict,user=Depends(owner),idem:str|None=Header(None,alias='Idempotency-Key')):
    keys(body,{'name','category','price_halalas','source_id','settings'})
    name=text(body.get('name'),200,'اسم الصنف');category=text(body.get('category'),150,'التصنيف')
    price=money(body.get('price_halalas'),'سعر الصنف')
    if not name or not category:
        raise db.error(422,'PRODUCT_IDENTITY_REQUIRED','اسم الصنف وتصنيفه مطلوبان.')
    settings=body.get('settings',{})
    keys(settings,{'description','option_groups','variants','available_cities','unavailable_branch_ids','available_branch_ids','image_url','image_asset_id'})
    validate_options(settings.get('option_groups',[]),settings.get('variants',[]))
    with db.tx() as conn:
        def perform():
            identifier=db.new_id('product_');source_id=text(body.get('source_id','admin:'+identifier),200,'مرجع المصدر')
            if conn.execute("SELECT 1 FROM products WHERE brand_id=%s AND settings->>'source_id'=%s",(user['brand_id'],source_id)).fetchone():
                raise db.error(409,'PRODUCT_SOURCE_EXISTS','الصنف موجود بهذا المرجع؛ حدّث سجله دون إعادة إنشائه.')
            if 'available_branch_ids' in settings:
                branch_ids(conn,settings['available_branch_ids'],user['brand_id'])
            if 'unavailable_branch_ids' in settings:
                branch_ids(conn,settings['unavailable_branch_ids'],user['brand_id'])
            if settings.get('image_asset_id'):
                asset=scoped(conn,'assets',settings['image_asset_id'],user)
                if asset['purpose']!='product':
                    raise db.error(422,'PRODUCT_IMAGE_INVALID','اختر صورة منتج مرفوعة فعليًا.')
                settings['image_url']='/api/product-assets/'+asset['id']
            elif settings.get('image_url') and not re.fullmatch(r'/assets/[A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|webp|svg)',settings['image_url']):
                raise db.error(422,'IMAGE_URL_INVALID','اختر صورة محلية معتمدة.')
            configured=settings|{'enabled':False,'lifecycle':'draft','source_verified':False,'source_id':source_id}
            row=conn.execute('INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *',(identifier,user['brand_id'],name,category,price,db.json(configured))).fetchone()
            db.audit(conn,user['id'],'product.created','product',identifier,{'source_id':source_id,'price_halalas':price})
            return {'product':row}
        return db.idempotent(conn,user['id'],idem,'product.create',body,perform)


@router.get('/product-assets/{record_id}')
def public_product_asset(record_id:str):
    with db.tx() as conn:
        row=conn.execute("SELECT a.contents,a.mime_type FROM assets a JOIN products p ON p.brand_id=a.brand_id JOIN brands b ON b.id=p.brand_id WHERE a.id=%s AND a.purpose='product' AND p.settings->>'image_url'=%s AND COALESCE((p.settings->>'enabled')::boolean,true) AND b.active LIMIT 1",(record_id,'/api/product-assets/'+record_id)).fetchone()
        if not row:
            raise db.error(404,'PRODUCT_ASSET_UNAVAILABLE','الصورة غير متاحة.')
    return Response(bytes(row['contents']),media_type=row['mime_type'],headers={'Cache-Control':'public,max-age=60','X-Content-Type-Options':'nosniff'})


@router.post('/admin/brands')
def create_brand(body:dict,user=Depends(owner),idem:str|None=Header(None,alias='Idempotency-Key')):
    keys(body,{'id','name','owner_email','owner_display_name'})
    brand_id=text(body.get('id'),60,'معرف البراند'); name=text(body.get('name'),200,'اسم البراند')
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,59}',brand_id) or not name:
        raise db.error(422,'BRAND_IDENTITY_INVALID','أدخل هوية البراند بشكل صحيح.')
    email=email_delivery.normalize_email(text(body.get('owner_email',''),254,'بريد مالك البراند'))
    display_name=text(body.get('owner_display_name',''),200,'اسم المسؤول')
    with db.tx() as conn:
        auth.require_owner_step_up(conn,user)
        def perform():
            if conn.execute('SELECT 1 FROM brands WHERE id=%s',(brand_id,)).fetchone():
                raise db.error(409,'BRAND_OR_USERNAME_EXISTS','البراند موجود؛ لا يُعاد إنشاؤه.')
            conn.execute('INSERT INTO brands(id,name) VALUES(%s,%s)',(brand_id,name))
            conn.execute('INSERT INTO app_settings(brand_id,settings) VALUES(%s,%s)',(brand_id,db.json({})))
            conn.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled) VALUES(%s,'guest','active',true),(%s,'email','testing',true),(%s,'sms','unconfigured',false),(%s,'whatsapp','unconfigured',false)",(brand_id,brand_id,brand_id,brand_id))
            identifier=db.new_id('user_')
            conn.execute("INSERT INTO users(id,display_name,role,brand_id,email,active,identity_kind,invitation_status) VALUES(%s,%s,'owner',%s,%s,false,'pending_email','pending_email')",(identifier,display_name,brand_id,email))
            challenge=auth._create_email_token(conn,brand=brand_id,email=email,purpose='add_owner',user_id=identifier,created_by=user['id'])
            db.audit(conn,user['id'],'brand.created','brand',brand_id,{'owner_user_id':identifier,'invitation_status':'pending_email'})
            return {'brand_id':brand_id,'name':name,'owner_user_id':identifier,'invitation_status':'pending_email','challenge_id':challenge,'customer_path':'/?brand_id='+brand_id}
        return db.idempotent(conn,user['id'],idem,'brand.create',body,perform)


@router.patch('/admin/brand-services')
def brand_services(body: dict, user=Depends(owner), idem: str | None=Header(None,alias='Idempotency-Key')):
    keys(body, {'services', 'reason'})
    services = body.get('services')
    keys(services, {'delivery', 'pickup', 'car_pickup'})
    if not services or any(type(value) is not bool for value in services.values()):
        raise db.error(422, 'SERVICE_POLICY_INVALID', 'اختر خدمات البراند المراد تفعيلها أو إيقافها.')
    reason = text(body.get('reason', ''), 500, 'سبب تعديل الخدمات')
    if not reason:
        raise db.error(422, 'SERVICE_POLICY_REASON_REQUIRED', 'اكتب سبب تعديل الخدمات.')
    with db.tx() as conn:
        def perform():
            row = conn.execute('SELECT settings FROM brands WHERE id=%s FOR UPDATE', (user['brand_id'],)).fetchone()
            previous = row['settings'].get('services', {})
            settings = row['settings'] | {'services': previous | services}
            conn.execute('UPDATE brands SET settings=%s WHERE id=%s', (db.json(settings), user['brand_id']))
            db.audit(conn, user['id'], 'brand.services.updated', 'brand', user['brand_id'],
                     {'previous': previous, 'services': settings['services'], 'reason': reason})
            return {'services': settings['services'], 'existing_orders_preserved': True}
        return db.idempotent(conn, user['id'], idem, 'brand.services.update', body, perform)


@router.post('/admin/delivery-only')
def delivery_only(body: dict, user=Depends(owner), idem: str | None=Header(None,alias='Idempotency-Key')):
    keys(body, {'branch_ids', 'confirm', 'reason'})
    if type(body.get('confirm', False)) is not bool:
        raise db.error(422, 'CONFIRMATION_INVALID', 'راجع أثر التغيير ثم أكد الحفظ.')
    reason = text(body.get('reason', ''), 500, 'سبب تعديل الخدمات')
    with db.tx() as conn:
        scope = branch_ids(conn, body.get('branch_ids', []), user['brand_id'])
        if not scope:
            raise db.error(422, 'BRANCH_SCOPE_REQUIRED', 'حدد الفروع المراد تعديل خدماتها.')
        brand = conn.execute('SELECT settings FROM brands WHERE id=%s FOR SHARE', (user['brand_id'],)).fetchone()
        rows = [scoped(conn, 'branches', bid, user) for bid in sorted(scope)]
        warnings = []
        for row in rows:
            available, code, why = catalog.branch_service(row, 'delivery', brand_settings=brand['settings'])
            if not available:
                warnings.append({'branch_id': row['id'], 'branch_no': row['branch_no'], 'name': row['name'], 'code': code,
                                 'reason': why, 'will_accept_new_orders': False})
        preview = {'branch_ids': scope, 'disabled_services': ['pickup', 'car_pickup'], 'delivery_will_be_enabled': False,
                   'warnings': warnings, 'existing_orders_preserved': True}
        if not body.get('confirm'):
            return dict(preview, saved=False, confirmation_required=True)
        if not reason:
            raise db.error(422, 'SERVICE_POLICY_REASON_REQUIRED', 'اكتب سبب تعديل الخدمات.')
        def perform():
            for row in rows:
                settings = row['settings'] | {'pickup_enabled': False, 'car_enabled': False}
                conn.execute('UPDATE branches SET settings=%s,updated_at=now() WHERE id=%s', (db.json(settings), row['id']))
                db.audit(conn, user['id'], 'branch.delivery_only', 'branch', row['id'],
                         {'previous': {key: row['settings'].get(key) for key in ('pickup_enabled','car_enabled','delivery_enabled')},
                          'reason': reason, 'delivery_preserved': row['settings'].get('delivery_enabled', False)})
            return dict(preview, saved=True, confirmation_required=False)
        return db.idempotent(conn, user['id'], idem, 'branches.delivery_only', body, perform)
