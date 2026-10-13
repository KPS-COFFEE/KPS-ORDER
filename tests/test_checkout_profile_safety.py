"""Diagnostic instrumentation must never attach to a live application."""
import os
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('environment,url,database', [
    ('production', 'postgresql://fixture@127.0.0.1/test_load', 'postgresql://fixture@127.0.0.1/test_load?options=test'),
    ('testing', 'postgresql://fixture@remote.invalid/test_load', 'postgresql://fixture@remote.invalid/test_load?options=test'),
    ('testing', 'postgresql://fixture@127.0.0.1/test_load', 'postgresql://fixture@127.0.0.1/real_database'),
])
def test_cpu_diagnostic_rejects_nonisolated_environment(environment, url, database):
    env = dict(os.environ, APP_ENV=environment, TEST_DATABASE_URL=url, DATABASE_URL=database)
    result = subprocess.run([sys.executable, '-m', 'tools.profile_checkout'], env=env,
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'isolated' in result.stderr or 'loopback' in result.stderr
    assert 'Started server' not in result.stderr


def test_cpu_diagnostic_preserves_fastapi_thread_call_keywords(tmp_path):
    """FastAPI passes a keyword named function through the threadpool wrapper."""
    url = 'postgresql://fixture@127.0.0.1/test_load'
    env = dict(os.environ, APP_ENV='testing', TEST_DATABASE_URL=url,
               DATABASE_URL=url + '?options=isolated',
               KPS_LOAD_PROFILE_OUTPUT=str(tmp_path / 'cpu.json'))
    # A tiny app exercises the installed FastAPI without connecting a database
    # or importing the deployed application/lifespan into this subprocess.
    code = '''
import sys, types
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
app = FastAPI()
module = types.ModuleType('app.main')
module.app = app
sys.modules['app.main'] = module
import tools.profile_checkout
def identity():
    return 'fixture'
@app.get('/probe')
def probe(value=Depends(identity)):
    return {'value': value}
with TestClient(app) as client:
    response = client.get('/probe')
    assert response.status_code == 200, response.text
    assert response.json() == {'value': 'fixture'}
'''
    result = subprocess.run([sys.executable, '-c', code], env=env,
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    reports = list(tmp_path.glob('cpu-*.json'))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report['thread_calls'] == 2
    assert any(row['function'] == 'probe' for row in report['functions'])
