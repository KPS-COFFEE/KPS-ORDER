"""Do not certify a different worker topology from the Docker entry point."""
import inspect
import json
from pathlib import Path
import shlex

import pytest

from tools import hardening_load

ROOT = Path(__file__).resolve().parents[1]


def test_release_load_and_docker_use_the_same_two_web_workers():
    lines = (ROOT / 'Dockerfile').read_text().splitlines()
    commands = [json.loads(line[4:]) for line in lines if line.startswith('CMD ')]
    assert len(commands) == 1
    shell, flag, command = commands[0]
    assert (shell, flag) == ('/bin/sh', '-c')
    args = shlex.split(command)
    assert args[:5] == ['exec', 'python', '-m', 'uvicorn', 'app.main:app']
    assert int(args[args.index('--workers') + 1]) == hardening_load.RELEASE_WORKERS == 2
    assert inspect.signature(hardening_load.start_app).parameters['workers'].default == 2
    assert args[args.index('--host') + 1] == '0.0.0.0'
    assert args[args.index('--port') + 1] == '${PORT:-10000}'


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
