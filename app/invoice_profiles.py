"""Owner-controlled seller profiles and explicitly non-fiscal receipt QR codes.

VAT configuration is preparation, not authorization to issue fiscal documents.
No live ZATCA invoice is issued by this module. Phase readiness remains blocked.
"""
import base64
import copy
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from reportlab.graphics.barcode.qrencoder import QRCode, QRErrorCorrectLevel

from . import auth, db

router = APIRouter(prefix='/api/invoice-profiles', tags=['invoice-preparation'])
owner = auth.require_roles('owner')
REEM_BRANCHES = {1,2,3,4,5,6,7,8,9,13,14,15}
BRANCH_MATCHES_PATH = Path(__file__).resolve().parents[1] / 'data/branch_google_matches.json'


def defaults(brand_id):
    if brand_id != 'kps':
        return {'entities': {}, 'assignments': {}}
    return {'entities': {
        'reem': {'legal_name': 'مؤسسة ريم فهاد لافي الحربي لتقديم الوجبات',
                 'vat_enabled': True, 'vat_number': '311887526600003'},
        'sami': {'legal_name': 'مؤسسة سامي فهاد لافي الحربي لتقديم الوجبات',
                 'vat_enabled': False, 'vat_number': ''}},
        'assignments': {f'kps-branch-{i}': 'reem' if i in REEM_BRANCHES else 'sami' for i in range(1,21)}}


def settings(conn, brand_id):
    row = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s', (brand_id,)).fetchone()
    return copy.deepcopy((row or {}).get('settings', {}).get('invoice_profiles') or defaults(brand_id))


@lru_cache(maxsize=4)
def _branch_addresses(path, file_version):
    # Only source-file addresses are cached, never mutable prices or permissions.
    # The stat identity changes on edits/replacements; old versions are bounded.
    matches = json.loads(path.read_text())['matches']
    addresses = {}
    for match in matches:
        addresses.setdefault((match['brand_id'], match['branch_id']), match['google_address'])
    return addresses


def capture(conn, brand_id, branch_id, *, brand_settings=None, channel=None):
    config = (settings(conn, brand_id) if brand_settings is None else
              copy.deepcopy(brand_settings.get('invoice_profiles') or defaults(brand_id)))
    if channel == 'kps_order' and brand_id == 'kps':
        from .channel_commercial import seller_profile
        return seller_profile(config)
    entity_id = config['assignments'].get(branch_id)
    entity = copy.deepcopy(config['entities'].get(entity_id, {}))
    # Addresses are source data, never inferred from the customer's destination.
    stat = BRANCH_MATCHES_PATH.stat()
    version = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    address = _branch_addresses(BRANCH_MATCHES_PATH, version).get((brand_id, branch_id), '')
    return {'entity_id': entity_id, **entity, 'address': address,
            'issuance_status': 'not_enabled', 'prices_include_vat': bool(entity.get('vat_enabled'))}


class Entity(BaseModel):
    model_config = ConfigDict(extra='forbid')
    legal_name: str = Field(min_length=1, max_length=120)
    vat_enabled: StrictBool
    vat_number: str = Field(default='', max_length=15)


class ProfileBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    entities: dict[str, Entity]
    assignments: dict[str, str]
    reason: str = Field(min_length=3, max_length=300)
    revision: str


def validate(body):
    if not 1 <= len(body.entities) <= 50 or len(body.assignments)>500 or not body.reason.strip():
        raise db.error(422, 'INVOICE_PROFILE_INVALID', 'راجع المؤسسات والفروع وسبب التعديل.')
    for key, entity in body.entities.items():
        if not re.fullmatch(r'[a-z0-9_-]{1,60}', key) or not entity.legal_name.strip():
            raise db.error(422, 'SELLER_INVALID', 'اسم ومعرف المؤسسة مطلوبان.')
        if entity.vat_enabled and not re.fullmatch(r'3\d{13}3', entity.vat_number):
            raise db.error(422, 'VAT_NUMBER_REQUIRED', 'أدخل الرقم الضريبي الصحيح للمؤسسة المسجلة.')
        if not entity.vat_enabled and entity.vat_number:
            raise db.error(422, 'UNREGISTERED_VAT_NUMBER', 'المؤسسة غير المسجلة لا تحمل رقمًا ضريبيًا.')
    if any(value not in body.entities for value in body.assignments.values()):
        raise db.error(422, 'SELLER_ASSIGNMENT_INVALID', 'حدد مؤسسة صحيحة لكل فرع.')


@router.get('')
def read_profiles(user=Depends(owner)):
    with db.tx() as conn:
        config = settings(conn, user['brand_id'])
        branches = conn.execute('SELECT id,name FROM branches WHERE brand_id=%s ORDER BY branch_no', (user['brand_id'],)).fetchall()
    return {**config, 'revision': db.digest(config), 'branches': branches,
            'tax_issuance_enabled': False, 'status': 'preparation_only'}


@router.post('')
def save_profiles(body: ProfileBody, user=Depends(owner), idem: str | None=Header(None,alias='Idempotency-Key')):
    validate(body)
    with db.tx() as conn:
        def perform():
            row = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s FOR UPDATE', (user['brand_id'],)).fetchone()
            current = settings(conn, user['brand_id'])
            if db.digest(current) != body.revision:
                raise db.error(409, 'INVOICE_PROFILE_STALE', 'عدّل مستخدم آخر الإعدادات؛ أعد فتح الشاشة.')
            branches = {r['id'] for r in conn.execute('SELECT id FROM branches WHERE brand_id=%s', (user['brand_id'],)).fetchall()}
            if set(body.assignments) != branches:
                raise db.error(422, 'INVOICE_BRANCH_SCOPE', 'حدد مؤسسة لكل فرع ضمن هذا البراند فقط.')
            config = {'entities': {key: value.model_dump() for key,value in body.entities.items()}, 'assignments': body.assignments}
            conn.execute('UPDATE app_settings SET settings=%s,updated_at=now() WHERE brand_id=%s',
                         (db.json(row['settings'] | {'invoice_profiles': config}),user['brand_id']))
            db.audit(conn,user['id'],'invoice_profiles.changed','brand',user['brand_id'],
                     {'before':current,'after':config,'reason':body.reason,'effective_at':datetime.now(timezone.utc).isoformat()})
            return {'saved':True,'tax_issuance_enabled':False,'revision':db.digest(config)}
        return db.idempotent(conn,user['id'],idem,'invoice_profiles.save',body.model_dump(),perform)


def qr_matrix(text):
    if len(text.encode('utf-8'))>1800:
        raise ValueError('Receipt QR payload too large')
    qr = QRCode(None,QRErrorCorrectLevel.M)
    qr.addData(text); qr.make()
    size = qr.getModuleCount()
    # Four-module white quiet zone is carried in the matrix itself, in every renderer.
    blank = '0'*(size+8)
    return [blank]*4 + ['0000'+''.join('1' if qr.isDark(y,x) else '0' for x in range(size))+'0000' for y in range(size)] + [blank]*4


def qr_svg(matrix):
    size = len(matrix)
    path = ' '.join(f'M{x} {y}h1v1h-1z' for y,row in enumerate(matrix) for x,value in enumerate(row) if value=='1')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges"><rect width="{size}" height="{size}" fill="white"/><path d="{path}" fill="black"/></svg>'


def ordinary_qr(order):
    snap = order['snapshot']
    profile = snap.get('receipt_profile', {})
    # Never falsely encode an unverified tax invoice. No customer name, phone,
    # address, authorization token or item notes are embedded in a public QR.
    data = {'type':'KPS_ORDER_RECEIPT_NOT_TAX_INVOICE','id':order['id'],
            'number':str(snap.get('order_reference') or order['order_no']),
            'seller':profile.get('legal_name') or 'KPS COFFEE',
            'created_at':order['created_at'].isoformat(),
            'total_sar':f"{int(snap.get('total_halalas',order.get('total_halalas',0)))/100:.2f}"}
    text = json.dumps(data,ensure_ascii=False,separators=(',',':'))
    matrix = qr_matrix(text)
    return {'text':text,'matrix':matrix,'image':'data:image/svg+xml;base64,'+base64.b64encode(qr_svg(matrix).encode()).decode()}


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    entity: Entity


@router.post('/preview')
def preview(body:PreviewBody,user=Depends(owner)):
    validate(ProfileBody(entities={'preview':body.entity},assignments={},reason='Preview',revision=''))
    gross = 2300
    tax = int((Decimal(gross)*Decimal(15)/Decimal(115)).quantize(Decimal('1'),rounding=ROUND_HALF_UP)) if body.entity.vat_enabled else 0
    data = {'type':'DESIGN_PREVIEW_NOT_A_FISCAL_INVOICE','seller':body.entity.legal_name,
            'vat_number':body.entity.vat_number,'total_sar':'23.00','vat_sar':f'{tax/100:.2f}'}
    matrix = qr_matrix(json.dumps(data,ensure_ascii=False))
    return {'preview_only':True,'total_halalas':gross,'tax_halalas':tax,'net_halalas':gross-tax,
            'qr_image':'data:image/svg+xml;base64,'+base64.b64encode(qr_svg(matrix).encode()).decode(),
            'tax_issuance_enabled':False}
