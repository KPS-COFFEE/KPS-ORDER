"""A capacity certificate must prove kernel budgets and exact workload ownership."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import hardening_load as load


@pytest.mark.parametrize('latency,failed', [(4999.999, False), (5000, True), (5000.001, True),
                                          (None, True), (float('nan'), True), (float('inf'), True)])
def test_owner_p95_limit_is_strict_and_invalid_measurements_cannot_pass(latency, failed):
    result = load.latency_failure_reasons({'p95_ms': latency, 'p99_ms': 9000}, 5000, 10000)
    assert ('P95 latency gate failed.' in result) is failed


def write_group(path, quota, memory, pids=''):
    path.mkdir(parents=True)
    values = {'cpu.max': quota, 'memory.max': str(memory), 'memory.swap.max': '0', 'cgroup.procs': pids,
              'cpu.stat': 'usage_usec 100\nnr_periods 10\nnr_throttled 5\nthrottled_usec 50\n',
              'memory.events': 'low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n', 'memory.current': '1234'}
    for name, value in values.items():
        (path / name).write_text(value)


@pytest.fixture
def budgets(tmp_path, monkeypatch):
    root = tmp_path / 'cgroup'
    monkeypatch.setattr(load, 'CGROUP_ROOT', root)
    app = root / ('kps_order_load_' + 'a' * 32)
    db = root / 'docker-synthetic-postgres'
    write_group(app, load.RENDER_APP_CPU_MAX, load.RENDER_APP_MEMORY_BYTES)
    write_group(db, '10000 100000', load.RENDER_DB_MEMORY_BYTES, '100091\n100092\n')
    generator = root / 'runner'
    memberships = {os.getpid(): generator, 100091: db, 100002: app, 100003: app, 100004: app}
    inspected = {'HostConfig': {'NanoCpus': 100_000_000, 'Memory': load.RENDER_DB_MEMORY_BYTES,
                               'MemorySwap': load.RENDER_DB_MEMORY_BYTES},
                 'State': {'Running': True, 'Pid': 100091}}
    monkeypatch.setattr(load, 'inspect_test_container', lambda *args: inspected)
    monkeypatch.setattr(load, 'process_cgroup', lambda pid, **kwargs: memberships[pid])
    monkeypatch.setattr(load, 'owned_process_pids', lambda pid: {100002, 100003, 100004})
    return SimpleNamespace(app=app, db=db, generator=generator, inspected=inspected, memberships=memberships)


def profile(budgets):
    return load.RenderResourceProfile(budgets.app, 'synthetic-local-container',
                                     'postgresql://fixture@127.0.0.1/test_resource_profile')


def test_real_budget_and_owned_worker_proof_exclude_the_controller_and_generator(budgets):
    configured = profile(budgets)
    (budgets.app / 'cgroup.procs').write_text('100002\n100003\n100004\n')
    observation = configured.observe(100002, [100003, 100004])
    assert observation['application']['pids'] == [100002, 100003, 100004]
    assert 'generator/sudo controller excluded' in observation['application']['observation_scope']
    assert observation['application']['cpu']['nr_throttled'] == 5
    assert observation['application']['memory_current_bytes'] == 1234
    assert os.getpid() not in observation['application']['pids']


@pytest.mark.parametrize('file,value', [('cpu.max', '60000 100000'), ('cpu.max', 'max 100000'),
                                       ('memory.max', '1073741824'), ('memory.swap.max', '1')])
def test_an_unconstrained_or_larger_application_cannot_certify_the_small_render_plan(budgets, file, value):
    (budgets.app / file).write_text(value)
    with pytest.raises(ValueError):
        profile(budgets)


@pytest.mark.parametrize('field,value', [('NanoCpus', 1_000_000_000), ('Memory', 1_073_741_824),
                                        ('MemorySwap', -1)])
def test_docker_metadata_must_match_the_requested_database_budget(budgets, field, value):
    budgets.inspected['HostConfig'][field] = value
    with pytest.raises(ValueError):
        profile(budgets)


@pytest.mark.parametrize('file,value', [('cpu.max', '20000 100000'), ('memory.max', '536870912'),
                                       ('memory.swap.max', 'max')])
def test_correct_docker_metadata_cannot_hide_different_actual_kernel_limits(budgets, file, value):
    (budgets.db / file).write_text(value)
    with pytest.raises(ValueError):
        profile(budgets)


def test_existing_workload_group_is_never_reused(budgets):
    (budgets.app / 'cgroup.procs').write_text('999\n')
    with pytest.raises(ValueError, match='empty'):
        profile(budgets)


@pytest.mark.parametrize('membership', ['app', 'db'])
def test_generator_may_not_consume_the_workload_budget(budgets, membership):
    budgets.memberships[os.getpid()] = getattr(budgets, membership)
    with pytest.raises(ValueError):
        profile(budgets)


def test_unrelated_process_in_the_group_invalidates_the_measurement(budgets):
    configured = profile(budgets)
    (budgets.app / 'cgroup.procs').write_text('100002\n100003\n100004\n999\n')
    with pytest.raises(RuntimeError, match='PID set'):
        configured.observe(100002, [100003, 100004])


def test_worker_outside_the_group_invalidates_the_measurement(budgets):
    configured = profile(budgets)
    (budgets.app / 'cgroup.procs').write_text('100002\n100003\n100004\n')
    budgets.memberships[100004] = budgets.generator
    with pytest.raises(RuntimeError, match='escaped'):
        configured.observe(100002, [100003, 100004])


def test_changing_limits_after_startup_invalidates_the_measurement(budgets):
    configured = profile(budgets)
    (budgets.app / 'cgroup.procs').write_text('100002\n100003\n100004\n')
    (budgets.app / 'cpu.max').write_text('100000 100000')
    with pytest.raises(ValueError):
        configured.observe(100002, [100003, 100004])


@pytest.mark.parametrize('path', ['/sys/fs/cgroup', '/sys/fs/cgroup/docker-other',
                                  '/sys/fs/cgroup/kps_order_load_../victim'])
def test_resource_helpers_refuse_shared_or_unrelated_groups(path):
    with pytest.raises(ValueError):
        load.application_cgroup_path(path)


def test_two_resource_runs_retain_the_original_workload_and_generator_budget():
    workflow = Path(__file__).resolve().parents[1] / '.github/workflows/software-hardening.yml'
    section = workflow.read_text().split('  representative-render-budget:', 1)[1]
    assert 'repetition: [1, 2]' in section and 'fail-fast: false' in section
    assert '--cpus 0.1 --memory 256m --memory-swap 256m' in section
    assert '--application-cgroup "$KPS_LOAD_CGROUP_PATH"' in section
    for option in ('--levels', '--rounds', '--p95-ms', '--p99-ms', '--max-error-rate', '--request-timeout'):
        assert option not in section

