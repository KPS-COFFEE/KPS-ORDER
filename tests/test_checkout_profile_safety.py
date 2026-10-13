"""Diagnostic instrumentation must never attach to a live application."""
import os
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
