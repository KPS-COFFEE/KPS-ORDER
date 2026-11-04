"""Owner-approved, expiring entry grants for work access outside Saudi Arabia.

The grant only clears the geographical restriction. It does not establish a
session, replace SMS proof, or widen any employee permission. Entry links are
single-use and are exchanged for a separate host-only browser credential;
neither raw credential is stored in PostgreSQL or the audit log.
"""
from __future__ import annotations

import ipaddress
import re
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import auth, db

router = APIRouter(tags=['staff-geographic-access'])
COOKIE = 'kps_work_geo'
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}\Z')
MAX_HOURS = 168
_FIELDS = ('id', 'user_id', 'brand_id', 'approved_by', 'reason', 'created_at',
           'expires_at', 'redeemed_at', 'revoked_at', 'revoked_by',
           'revocation_reason', 'revocation_source', 'display_name', 'approved_by_name')
_SELECT = """SELECT g.*,u.display_name,u.active AS target_active,
 u.role AS target_role,u.invitation_status AS target_invitation_status,
 u.brand_id AS target_brand_id,b.active AS target_brand_active,
 a.display_name AS approved_by_name,a.active AS approver_active,
 a.role AS approver_role,a.invitation_status AS approver_invitation_status,
 a.brand_id AS approver_brand_id,ab.active AS approver_brand_active
 FROM staff_geo_access_grants g JOIN users u ON u.id=g.user_id
 JOIN brands b ON b.id=u.brand_id JOIN users a ON a.id=g.approved_by
 JOIN brands ab ON ab.id=a.brand_id """


def _enabled():
    from . import staff_geo
    return staff_geo.enabled()


def _network(request):
    """Only the trusted provider supplies location; caller headers are ignored.

ZZ (a known global address outside the positive Saudi allowlist) may use an
explicit owner grant. Missing, malformed or non-global addresses never can.
"""
    from . import staff_geo
    if request is None:
        return None
    result = staff_geo.location(request)
    try:
        address = ipaddress.ip_address(result.get('client_ip') or '')
        if not address.is_global:
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    return result


def _denied(*, location=False):
    if location:
        return db.error(403, 'STAFF_GEO_LOCATION_UNVERIFIED',
                        'تعذر التحقق من موقع الاتصال. دخول الإدارة متاح من السعودية أو بسماح موثق من المالك. / The connection location could not be verified.')
    return db.error(403, 'STAFF_GEO_RESTRICTED',
                    'دخول الإدارة من خارج السعودية يتطلب سماحًا مؤقتًا من المالك. / Work access outside Saudi Arabia requires a temporary owner approval.')


def _owner_scope(conn, owner):
    """Use canonical ownership plus the existing explicit brand memberships."""
    brands = {owner['brand_id']}
    from . import multibrand
    if multibrand.enabled():
        brands.update(row['id'] for row in multibrand.available(conn, owner))
    return brands


def _owner(conn, user):
    if not user or user.get('role') != 'owner':
        raise db.error(403, 'ROLE_FORBIDDEN', 'هذا الإجراء للمالك فقط. / Owner only.')
    owner = conn.execute("SELECT u.* FROM users u JOIN brands b ON b.id=u.brand_id "
                         "WHERE u.id=%s AND u.role='owner' AND u.active "
                         "AND u.invitation_status='active' AND b.active", (user['id'],)).fetchone()
    if not owner:
        raise db.error(403, 'ROLE_FORBIDDEN', 'صلاحية المالك غير سارية. / Owner authority is no longer active.')
    return owner


def _target(conn, identifier, owner):
    row = conn.execute("SELECT u.* FROM users u JOIN brands b ON b.id=u.brand_id "
                       "WHERE u.id=%s AND u.role<>'customer' AND u.active "
                       "AND u.invitation_status='active' AND b.active", (identifier,)).fetchone()
    if not row or row['brand_id'] not in _owner_scope(conn, owner):
        raise db.error(404, 'STAFF_NOT_FOUND', 'الموظف غير متاح ضمن صلاحياتك. / Staff account unavailable in your scope.')
    return row


def _authority_active(conn, row):
    if not (row['target_active'] and row['target_brand_active']
            and row['target_role'] != 'customer' and row['target_invitation_status'] == 'active'
            and row['target_brand_id'] == row['brand_id']
            and row['approver_active'] and row['approver_brand_active']
            and row['approver_role'] == 'owner' and row['approver_invitation_status'] == 'active'
            and row['approver_brand_id'] == row['owner_brand_id']):
        return False
    if row['brand_id'] == row['owner_brand_id']:
        return True
    # A revoked cross-brand membership also revokes the authority behind the
    # grant; a saved approval must never become a permanent cross-brand bypass.
    owner = conn.execute('SELECT * FROM users WHERE id=%s', (row['approved_by'],)).fetchone()
    return bool(owner and row['brand_id'] in _owner_scope(conn, owner))


def _status(conn, row):
    if row['revoked_at']:
        return 'revoked'
    if row['expires_at'] <= datetime.now(timezone.utc):
        return 'expired'
    if not _authority_active(conn, row):
        return 'disabled'
    return 'active'


def _public(conn, row):
    return {**{key: row.get(key) for key in _FIELDS}, 'status': _status(conn, row)}


def _token_grant(conn, token, *, entry=False, lock=False):
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        return None
    column = 'entry_token_hash' if entry else 'access_token_hash'
    row = conn.execute(_SELECT + 'WHERE g.' + column + '=%s AND g.revoked_at IS NULL '
                       'AND g.expires_at>now()' + (' AND g.redeemed_at IS NULL' if entry else ' AND g.redeemed_at IS NOT NULL')
                       + (' FOR UPDATE OF g' if lock else ''), (auth.token_hash(token),)).fetchone()
    return row if row and _authority_active(conn, row) else None


def require_entry(request):
    """Protect the work shell and unauthenticated work endpoints abroad."""
    if not _enabled():
        return
    network = _network(request)
    if not network:
        raise _denied(location=True)
    if network.get('country_code') == 'SA':
        return
    token = request.cookies.get(COOKIE)
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise _denied()
    with db.tx() as conn:
        grant = _token_grant(conn, token)
    if not grant:
        raise _denied()


def require_user(conn, request, user):
    """Check the canonical account before any multibrand/session projection."""
    if (user and user.get('role') == 'customer') or not _enabled():
        return
    network = _network(request)
    if not network:
        raise _denied(location=True)
    if network.get('country_code') == 'SA':
        return
    grant = _token_grant(conn, request.cookies.get(COOKIE))
    if not user or not grant or grant['user_id'] != user['id']:
        raise _denied()


class GrantBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str = Field(min_length=1, max_length=100)
    hours: int = Field(default=24, ge=1, le=MAX_HOURS, strict=True)
    reason: str = Field(min_length=3, max_length=500)

    @field_validator('reason')
    @classmethod
    def validate_reason(cls, value):
        value = value.strip()
        if len(value) < 3:
            raise ValueError('A reason is required')
        return value


class RevokeBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=3, max_length=500)
    _reason = field_validator('reason')(GrantBody.validate_reason.__func__)


@router.get('/api/admin/staff-geo/grants')
def grants(user_id: str | None = Query(default=None, max_length=100), user=Depends(auth.require_roles('owner'))):
    with db.tx() as conn:
        owner = _owner(conn, user)
        scope = sorted(_owner_scope(conn, owner))
        if user_id:
            # Retain a disabled/expired target's history for the owner. Listing
            # history must not silently reactivate an account or its grants.
            target = conn.execute("SELECT id,brand_id FROM users WHERE id=%s AND role<>'customer'", (user_id,)).fetchone()
            if not target or target['brand_id'] not in scope:
                raise db.error(404, 'STAFF_NOT_FOUND', 'الموظف غير متاح ضمن صلاحياتك. / Staff account unavailable in your scope.')
        rows = conn.execute(_SELECT + 'WHERE g.brand_id=ANY(%s)' + (' AND g.user_id=%s' if user_id else '')
                            + ' ORDER BY g.created_at DESC LIMIT 200', (scope,) + ((user_id,) if user_id else ())).fetchall()
        return {'enabled': _enabled(), 'grants': [_public(conn, row) for row in rows]}


@router.post('/api/admin/staff-geo/grants')
def create_grant(body: GrantBody, response: Response, user=Depends(auth.require_roles('owner'))):
    with db.tx() as conn:
        owner = _owner(conn, user)
        auth.require_owner_sms_step_up(conn, user)
        target = _target(conn, body.user_id, owner)
        token = secrets.token_urlsafe(32)
        identifier = db.new_id('geo_')
        conn.execute('INSERT INTO staff_geo_access_grants(id,user_id,brand_id,approved_by,owner_brand_id,reason,entry_token_hash,expires_at) '
                     "VALUES(%s,%s,%s,%s,%s,%s,%s,now()+%s*interval '1 hour')",
                     (identifier, target['id'], target['brand_id'], owner['id'], owner['brand_id'],
                      body.reason, auth.token_hash(token), body.hours))
        row = conn.execute(_SELECT + 'WHERE g.id=%s', (identifier,)).fetchone()
        db.audit(conn, owner['id'], 'staff_geo.granted', 'staff_geo_grant', identifier,
                 {'user_id': target['id'], 'brand_id': target['brand_id'], 'reason': body.reason,
                  'hours': body.hours, 'expires_at': row['expires_at'].isoformat()})
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return {'grant': _public(conn, row), 'entry_url': '/work-access#token=' + token}


@router.post('/api/admin/staff-geo/grants/{identifier}/revoke')
def revoke_grant(identifier: str, body: RevokeBody, user=Depends(auth.require_roles('owner'))):
    with db.tx() as conn:
        owner = _owner(conn, user)
        auth.require_owner_sms_step_up(conn, user)
        row = conn.execute(_SELECT + 'WHERE g.id=%s FOR UPDATE OF g', (identifier,)).fetchone()
        if not row or row['brand_id'] not in _owner_scope(conn, owner):
            raise db.error(404, 'STAFF_GEO_GRANT_NOT_FOUND', 'السماح غير موجود ضمن صلاحياتك. / Grant unavailable in your scope.')
        if not row['revoked_at']:
            conn.execute("UPDATE staff_geo_access_grants SET revoked_at=now(),revocation_source='owner',revoked_by=%s,revocation_reason=%s "
                         'WHERE id=%s', (owner['id'], body.reason, identifier))
            db.audit(conn, owner['id'], 'staff_geo.revoked', 'staff_geo_grant', identifier,
                     {'user_id': row['user_id'], 'brand_id': row['brand_id'], 'reason': body.reason})
            row = conn.execute(_SELECT + 'WHERE g.id=%s', (identifier,)).fetchone()
        return {'grant': _public(conn, row)}


class RedeemBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')


@router.post('/api/staff-geo/redeem')
def redeem(body: RedeemBody, request: Request, response: Response):
    auth.check_origin(request)
    network = _network(request)
    if not network:
        raise _denied(location=True)
    # Commit the attempt before token validation; errors cannot roll back the
    # brute-force budget. Hash the network address instead of retaining it.
    with db.tx() as conn:
        allowed = db.rate_limit(conn, 'staff-geo-redeem:' + auth.token_hash(network['client_ip']), 10, 600)
    if not allowed:
        raise db.error(429, 'STAFF_GEO_RATE_LIMIT', 'انتظر قبل إعادة المحاولة. / Wait before trying again.')
    with db.tx() as conn:
        row = _token_grant(conn, body.token, entry=True, lock=True)
        if not row:
            raise db.error(403, 'STAFF_GEO_LINK_INVALID', 'رابط السماح غير صالح أو مستخدم أو منتهي. اطلب رابطًا جديدًا من المالك. / The entry link is invalid, used, or expired.')
        access_token = secrets.token_urlsafe(32)
        conn.execute('UPDATE staff_geo_access_grants SET access_token_hash=%s,redeemed_at=now() WHERE id=%s',
                     (auth.token_hash(access_token), row['id']))
        db.audit(conn, None, 'staff_geo.redeemed', 'staff_geo_grant', row['id'],
                 {'user_id': row['user_id'], 'approved_by': row['approved_by'],
                  'country_code': network.get('country_code') or 'ZZ'})
        remaining = max(1, int((row['expires_at'] - datetime.now(timezone.utc)).total_seconds()))
        response.set_cookie(COOKIE, access_token, max_age=remaining,
                            expires=row['expires_at'].astimezone(timezone.utc),
                            httponly=True, secure=auth.secure_cookie(), samesite='strict', path='/')
        # The entry grant never adopts an older work identity from this browser.
        # Tombstone work-cookie fallback so the approved person signs in with
        # their own SMS; a separate customer session remains untouched.
        if auth.context_cookies_enabled(conn):
            work_cookie, work_csrf = auth.context_cookie_names('work')
            response.set_cookie(work_cookie, 'logged_out', max_age=30 * 24 * 3600,
                                httponly=True, secure=auth.secure_cookie(), samesite='strict', path='/')
            response.delete_cookie(work_csrf, path='/')
        else:
            response.delete_cookie(auth.COOKIE, path='/')
            response.delete_cookie(auth.CSRF_COOKIE, path='/')
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return {'accepted': True, 'redirect': '/ops', 'expires_at': row['expires_at']}
