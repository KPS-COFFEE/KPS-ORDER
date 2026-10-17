"""Exact KPS portal boundaries on one backend; cookies remain host-only."""
import os
from fastapi import Request
from starlette.responses import JSONResponse, RedirectResponse
from . import db

PORTAL_ROLES = {
    'order.kps.sa': frozenset({'customer'}),
    'admin.kps.sa': frozenset({'owner', 'admin'}),
    'staff.kps.sa': frozenset({'owner', 'delivery_manager', 'delivery_staff', 'branch_manager', 'driver'}),
}


def home_for_role(role):
    host = 'order.kps.sa' if role == 'customer' else 'admin.kps.sa' if role in {'owner', 'admin'} else 'staff.kps.sa'
    return 'https://' + host + ('/' if role == 'customer' else '/ops')


def enabled():
    return os.getenv('KPS_PORTAL_HOST_ROUTING_ENABLED') == 'true'


def enforce_role(request, user):
    if not enabled():
        return
    allowed = PORTAL_ROLES.get(request.url.hostname, frozenset())
    if allowed is not None and user['role'] not in allowed:
        raise db.error(403, 'PORTAL_ROLE_FORBIDDEN', 'استخدم بوابة حسابك المخولة. / Open your authorized portal.')


class PortalBoundaryMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not enabled():
            return await self.app(scope, receive, send)
        request = Request(scope)
        host, path = request.url.hostname, request.url.path
        if host not in PORTAL_ROLES:
            if path in {'/health', '/ready'}:
                return await self.app(scope, receive, send)
            return await JSONResponse({'detail':{'code':'PORTAL_HOST_REJECTED','message':'استخدم عنوان البوابة المخول. / Use an authorized portal host.'}},status_code=403)(scope,receive,send)
        blocked = False
        if path.startswith('/api/') and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            # A global allowed-origin list is insufficient between these portals.
            blocked = request.headers.get('origin', '').rstrip('/') != 'https://' + host
        if host == 'order.kps.sa':
            blocked = blocked or path in {'/ops', '/ops.html', '/ops.js'} or path.startswith('/api/identity/staff/') or path == '/api/auth/login'
        else:
            blocked = blocked or path.startswith('/api/auth/otp/') or path == '/api/identity/guest'
            if path == '/' and request.method in {'GET', 'HEAD'}:
                return await RedirectResponse('/ops', status_code=307, headers={'Cache-Control':'no-store'})(scope, receive, send)
        if blocked:
            return await JSONResponse({'detail':{'code':'PORTAL_BOUNDARY_REJECTED','message':'الطلب غير متاح من هذه البوابة. / Request unavailable from this portal.'}}, status_code=403, headers={'Cache-Control':'no-store'})(scope, receive, send)
        await self.app(scope, receive, send)
