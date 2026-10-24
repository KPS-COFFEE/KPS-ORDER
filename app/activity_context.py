"""Mutable request scope survives threadpool boundaries; credentials never enter logs."""
from contextvars import ContextVar

scope_context = ContextVar('employee_activity_scope', default=None)


class ActivityContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        token = scope_context.set(scope)
        try:
            await self.app(scope, receive, send)
        finally:
            scope_context.reset(token)


def associate(request, user):
    if user['role'] != 'customer':
        request.scope.setdefault('state', {})['activity_actor'] = {
            'id': user['id'], 'session_id': user['session_id'], 'brand_id': user['brand_id']}


def context(actor_id):
    actor = (scope_context.get() or {}).get('state', {}).get('activity_actor', {})
    return actor if actor.get('id') == actor_id else {}
