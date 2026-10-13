"""Opt-in isolated CPU diagnosis; never loaded by the deployment entrypoint.

Run via tools.hardening_load with --profile-cpu. The normal performance gate
does not enable profiling. Reports contain function identifiers/timings only,
never request bodies, SQL, credentials or customer values.
"""
import atexit
import json
import os
from pathlib import Path

from tools.hardening_load import safe_url

if os.getenv('APP_ENV') != 'testing':
    raise RuntimeError('CPU diagnosis is restricted to an isolated test process.')
safe_url(os.environ['TEST_DATABASE_URL'])
if not os.environ.get('DATABASE_URL', '').startswith(os.environ['TEST_DATABASE_URL'].split('?')[0] + '?'):
    raise RuntimeError('CPU diagnosis requires the load harness isolated schema.')

import yappi

# One thread-aware profiler owns the process; do not attach competing
# cProfile/sys.monitoring instances to FastAPI's concurrent worker threads.
yappi.set_clock_type('cpu')


@atexit.register
def write_profile():
    yappi.stop()
    rows = [{'file': Path(row.module).name, 'line': row.lineno, 'function': row.name,
             'calls': row.ncall, 'cpu_self_seconds': round(row.tsub, 6),
             'cpu_cumulative_seconds': round(row.ttot, 6)} for row in yappi.get_func_stats()]
    threads = [{'name': row.name, 'cpu_seconds': round(row.ttot, 6),
                'scheduling_count': row.sched_count} for row in yappi.get_thread_stats()]
    base = Path(os.environ['KPS_LOAD_PROFILE_OUTPUT'])
    output = base.with_name(f'{base.stem}-{os.getpid()}{base.suffix}')
    output.write_text(json.dumps({'mode': 'isolated_thread_aware_cpu', 'threads': threads,
        'functions': sorted(rows, key=lambda row: row['cpu_self_seconds'], reverse=True)}, indent=2))


from app.main import app as application


async def app(scope, receive, send):
    async def observed_send(message):
        if message['type'] == 'lifespan.startup.complete':
            yappi.start(builtins=True, profile_threads=True)
        if message['type'] == 'lifespan.shutdown.complete':
            # Uvicorn re-raises SIGTERM after shutdown, so atexit alone is
            # insufficient. Persist while graceful shutdown still owns us.
            write_profile()
        await send(message)
    await application(scope, receive, observed_send)
