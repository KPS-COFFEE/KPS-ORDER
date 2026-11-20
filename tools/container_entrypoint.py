"""Shell-free equivalent of the existing two-worker Uvicorn command."""
import os
import sys

os.execv(sys.executable, [
    sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0',
    '--port', os.environ.get('PORT') or '10000', '--workers', '2',
    '--timeout-keep-alive', '15',
])
