"""Do not certify a different worker topology from the Docker entry point."""
import inspect
import json
import shlex
from pathlib import Path

import pytest

from tools import hardening_load

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('port', ['', '18080'])
def test_release_load_and_docker_use_the_same_two_web_workers(monkeypatch, port):
    import os
    import runpy
    import sys

    lines = (ROOT / 'Dockerfile').read_text().splitlines()
    commands = [json.loads(line[4:]) for line in lines if line.startswith('CMD ')]
    assert len(commands) == 2
    assert commands[-1] == ['/usr/local/bin/python', '/usr/local/bin/kps-order-start.py']
    assert commands[0][:2] == ['/bin/sh', '-c']
    rollback_args = shlex.split(commands[0][2])
    assert rollback_args[:5] == ['exec', 'python', '-m', 'uvicorn', 'app.main:app']
    assert rollback_args[rollback_args.index('--port') + 1] == '${PORT:-10000}'
    assert rollback_args[rollback_args.index('--host') + 1] == '0.0.0.0'
    assert int(rollback_args[rollback_args.index('--workers') + 1]) == hardening_load.RELEASE_WORKERS == 2
    assert int(rollback_args[rollback_args.index('--timeout-keep-alive') + 1]) == hardening_load.RELEASE_KEEPALIVE_SECONDS == 15
    captured = []
    monkeypatch.setenv('PORT', port)
    monkeypatch.setattr(os, 'execv', lambda executable, args: captured.append((executable, args)))
    deployed = Path('/usr/local/bin/kps-order-start.py')
    runpy.run_path(str(deployed if deployed.is_file() else ROOT / 'tools/container_entrypoint.py'))
    assert len(captured) == 1
    executable, args = captured[0]
    assert executable == sys.executable
    assert args[:4] == [sys.executable, '-m', 'uvicorn', 'app.main:app']
    assert int(args[args.index('--workers') + 1]) == hardening_load.RELEASE_WORKERS == 2
    assert int(args[args.index('--timeout-keep-alive') + 1]) == hardening_load.RELEASE_KEEPALIVE_SECONDS == 15
    assert inspect.signature(hardening_load.start_app).parameters['workers'].default == 2
    assert args[args.index('--host') + 1] == '0.0.0.0'
    assert args[args.index('--port') + 1] == (port or '10000')


def test_both_release_runs_retain_the_complete_strict_load_gate():
    workflow = (ROOT / '.github/workflows/software-hardening.yml').read_text()
    assert 'isolated-load-recovery:' in workflow and 'isolated-two-worker-capacity:' in workflow
    assert '--workers 2' in workflow
    assert '--profile-cpu --workers 1 --levels 50' in workflow
    # Neither independent release run overrides the latency, error or workload
    # defaults. The diagnostic-only level 50 run cannot replace either gate.
    for option in ('--p95-ms', '--p99-ms', '--max-error-rate', '--rounds'):
        assert option not in workflow
    assert workflow.count('--levels') == 1


def test_release_cli_defaults_keep_the_original_acceptance_thresholds(monkeypatch):
    parse = hardening_load.argparse.ArgumentParser.parse_args

    class CheckedWithoutDatabase(Exception):
        pass

    def checked(parser, *args, **kwargs):
        values = parse(parser, [])
        assert values.workers == hardening_load.RELEASE_WORKERS == 2
        assert values.levels == '50,100,250,500' and values.rounds == 2
        assert values.p95_ms == 5000 and values.p99_ms == 10000
        assert values.max_error_rate == .01 and not values.profile_cpu
        raise CheckedWithoutDatabase

    monkeypatch.setattr(hardening_load.argparse.ArgumentParser, 'parse_args', checked)
    with pytest.raises(CheckedWithoutDatabase):
        hardening_load.main()


def test_keepalive_preserves_idle_socket_across_old_five_second_boundary():
    """Real HTTP sockets, no DB: distinguish idle lifetime from request latency."""
    import contextlib
    import http.client
    import select
    import socket
    import threading
    import time

    import uvicorn

    async def app(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200,
                    'headers': [(b'content-length', b'2')]})
        await send({'type': 'http.response.body', 'body': b'OK'})

    @contextlib.contextmanager
    def server(timeout):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        runtime = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_config=None,
            access_log=False, timeout_keep_alive=timeout))
        thread = threading.Thread(target=runtime.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        client = http.client.HTTPConnection('127.0.0.1', listener.getsockname()[1], timeout=5)
        try:
            deadline = time.monotonic() + 5
            while not runtime.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.01)
            assert runtime.started
            client.request('GET', '/')
            assert client.getresponse().read() == b'OK'
            yield client
        finally:
            client.close()
            runtime.should_exit = True
            thread.join(5)
            listener.close()
            assert not thread.is_alive()

    with server(5) as old, server(hardening_load.RELEASE_KEEPALIVE_SECONDS) as release:
        old_socket, release_socket = old.sock, release.sock
        # The old server closes while the checkout's other request may still be
        # outstanding. The release keeps this connection available for reuse.
        assert select.select([old_socket], [], [], 7)[0] == [old_socket]
        assert old_socket.recv(1, socket.MSG_PEEK) == b''
        assert not select.select([release_socket], [], [], 0)[0]
        release.request('GET', '/')
        assert release.getresponse().read() == b'OK'
        assert release.sock is release_socket
