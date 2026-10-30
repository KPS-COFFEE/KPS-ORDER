"""A capacity certificate must prove kernel budgets and exact workload ownership."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import hardening_load as load


@pytest.fixture(autouse=True)
def explicit_current_profile(monkeypatch):
    # Tests verify both profiles regardless of the enclosing CI job's choice.
    monkeypatch.delenv('KPS_LOAD_RESOURCE_PROFILE', raising=False)


def test_unknown_budget_cannot_silently_relax_a_gate(monkeypatch):
    monkeypatch.setenv('KPS_LOAD_RESOURCE_PROFILE', 'arbitrary')
    with pytest.raises(ValueError, match='Unknown resource profile'):
        load.resource_budget()


def test_proposed_profile_is_explicit_and_cannot_certify_current_limits(budgets, monkeypatch):
    monkeypatch.setenv('KPS_LOAD_RESOURCE_PROFILE', 'proposed')
    with pytest.raises(ValueError):
        profile(budgets)
    (budgets.app / 'cpu.max').write_text('200000 100000')
    (budgets.app / 'memory.max').write_text(str(4 * 1024 ** 3))
    (budgets.db / 'cpu.max').write_text('100000 100000')
    (budgets.db / 'memory.max').write_text(str(2 * 1024 ** 3))
    budgets.inspected['HostConfig'].update(NanoCpus=1_000_000_000, Memory=2 * 1024 ** 3, MemorySwap=2 * 1024 ** 3)
    configured = profile(budgets)
    assert configured.budget['name'] == 'proposed'
    (budgets.app / 'cgroup.procs').write_text('100002\n100003\n100004\n')
    configured.observe(100002, [100003, 100004])
    (budgets.app / 'cgroup.procs').write_text('')
    monkeypatch.setenv('KPS_LOAD_RESOURCE_PROFILE', 'current')
    with pytest.raises(ValueError):
        profile(budgets)


def test_proposal_runs_do_not_replace_current_budget_or_reduce_workload():
    workflow = Path(__file__).resolve().parents[1] / '.github/workflows/software-hardening.yml'
    section = workflow.read_text().split('  proposed-capacity-budget:', 1)[1].split('  representative-render-budget:', 1)[0]
    assert 'repetition: [1, 2]' in section and 'KPS_LOAD_RESOURCE_PROFILE: proposed' in section
    assert '--cpus 1 --memory 2g --memory-swap 2g' in section
    for option in ('--levels', '--rounds', '--p95-ms', '--p99-ms', '--max-error-rate', '--request-timeout'):
        assert option not in section


@pytest.mark.parametrize('name,db_cpu,db_memory', [
    ('economy', 5, 1024 ** 3), ('medium', 10, 2 * 1024 ** 3)])
def test_hosting_candidates_require_their_exact_kernel_limits_and_never_certify_current(
        budgets, monkeypatch, name, db_cpu, db_memory):
    monkeypatch.setenv('KPS_LOAD_RESOURCE_PROFILE', name)
    with pytest.raises(ValueError):
        profile(budgets)
    (budgets.app / 'cpu.max').write_text('100000 100000')
    (budgets.app / 'memory.max').write_text(str(2 * 1024 ** 3))
    (budgets.db / 'cpu.max').write_text(f'{db_cpu * 10000} 100000')
    (budgets.db / 'memory.max').write_text(str(db_memory))
    budgets.inspected['HostConfig'].update(NanoCpus=db_cpu * 100_000_000,
                                         Memory=db_memory, MemorySwap=db_memory)
    configured = profile(budgets)
    assert configured.budget['name'] == name
    (budgets.app / 'cgroup.procs').write_text('100002\n100003\n100004\n')
    configured.observe(100002, [100003, 100004])
    # A later quota escape must invalidate the candidate just like the current plan.
    (budgets.db / 'cpu.max').write_text('200000 100000')
    with pytest.raises(ValueError):
        configured.observe(100002, [100003, 100004])


def test_economy_precedes_medium_and_neither_reduces_the_retained_workload():
    workflow = Path(__file__).resolve().parents[1] / '.github/workflows/hosting-capacity.yml'
    value = workflow.read_text()
    assert "needs: economy" in value
    assert "needs.economy.result == 'failure'" in value
    assert value.count('repetition: [1, 2]') == 2
    assert '--cpus 0.5 --memory 1g --memory-swap 1g' in value
    assert '--cpus 1 --memory 2g --memory-swap 2g' in value
    for option in ('--levels', '--rounds', '--p95-ms', '--p99-ms', '--request-timeout'):
        assert option not in value


def test_cpu_only_proposal_retains_the_deployed_database_limits(budgets, monkeypatch):
    monkeypatch.setenv('KPS_LOAD_RESOURCE_PROFILE', 'cpu_only')
    assert load.resource_budget() == {
        'name': 'cpu_only', 'app_cpu_tenths': 20, 'app_memory': 4 * 1024 ** 3,
        'db_cpu_tenths': 5, 'db_memory': 1024 ** 3}
    with pytest.raises(ValueError):
        profile(budgets)
    (budgets.app / 'cpu.max').write_text('200000 100000')
    (budgets.app / 'memory.max').write_text(str(4 * 1024 ** 3))
    (budgets.db / 'cpu.max').write_text('50000 100000')
    (budgets.db / 'memory.max').write_text(str(1024 ** 3))
    budgets.inspected['HostConfig'].update(NanoCpus=500_000_000,
                                         Memory=1024 ** 3, MemorySwap=1024 ** 3)
    configured = profile(budgets)
    (budgets.app / 'cgroup.procs').write_text('100002\n100003\n100004\n')
    configured.observe(100002, [100003, 100004])
    (budgets.app / 'cpu.max').write_text('100000 100000')
    with pytest.raises(ValueError):
        configured.observe(100002, [100003, 100004])


def test_cpu_only_proposal_keeps_the_complete_zero_error_workload():
    workflow = Path(__file__).resolve().parents[1] / '.github/workflows/cpu-capacity.yml'
    value = workflow.read_text()
    assert 'KPS_LOAD_RESOURCE_PROFILE: cpu_only' in value
    assert 'repetition: [1, 2]' in value
    assert '--cpus 0.5 --memory 1g --memory-swap 1g' in value
    assert '--max-error-rate 0' in value
    for option in ('--levels', '--rounds', '--p95-ms', '--p99-ms', '--request-timeout'):
        assert option not in value


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
    # Render DB upgrade was verified on 2026-10-30: app 1 CPU/2 GiB, DB 1 CPU/2 GiB.
    # Retain the economy helper and historical failure artifacts; this gate
    # must now measure the actual paid medium kernel limits.
    assert '--cpus 1 --memory 2g --memory-swap 2g' in section
    assert 'KPS_LOAD_RESOURCE_PROFILE: medium' in section
    assert 'current-render-medium-resource-budget' in section
    assert 'current-render-economy-resource-budget' not in section
    assert '--preserve-env=GITHUB_ACTIONS,KPS_LOAD_RESOURCE_PROFILE' in section
    assert '--application-cgroup "$KPS_LOAD_CGROUP_PATH"' in section
    assert '--max-error-rate 0\n' in section
    for option in ('--levels', '--rounds', '--p95-ms', '--p99-ms', '--request-timeout'):
        assert option not in section

