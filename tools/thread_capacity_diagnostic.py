"""Isolated scheduling experiment; never a production application entrypoint."""
import os

import anyio.to_thread
from tools.hardening_load import safe_url

if os.getenv('APP_ENV') != 'testing':
    raise RuntimeError('Thread scheduling diagnosis requires an isolated test process.')
safe_url(os.environ['TEST_DATABASE_URL'])
if not os.environ.get('DATABASE_URL', '').startswith(os.environ['TEST_DATABASE_URL'].split('?')[0] + '?'):
    raise RuntimeError('Thread scheduling diagnosis requires the load harness isolated schema.')
TOKENS = int(os.environ['KPS_DIAGNOSTIC_THREAD_TOKENS'])
if TOKENS not in {8, 40}:
    raise RuntimeError('Only the declared 40 versus 8 thread diagnostic is allowed.')

from app.main import app as application


async def app(scope, receive, send):
    if scope['type'] == 'lifespan':
        anyio.to_thread.current_default_thread_limiter().total_tokens = TOKENS
    await application(scope, receive, send)
