"""The combined candidate cannot substitute a lighter load or fake resources."""
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]


def test_retained_load_selects_customer_cookie_jar_without_disabling_security():
    import inspect
    from tools.hardening_load import Workload
    source=inspect.getsource(Workload.user)
    assert "'X-KPS-Context': 'customer'" in source
    assert "local_address=source, retries=0" in source
    assert 'SESSION_CONTEXT_COOKIES_ENABLED' not in source


def test_report_fixture_never_targets_an_operational_or_unowned_schema(monkeypatch):
    import pytest
    from tools.hardening_load import hosting_report_checks
    for url in ['postgresql://synthetic@remote.invalid/test_report',
                'postgresql://synthetic@127.0.0.1/production',
                'postgresql://synthetic@127.0.0.1/test_report']:
        monkeypatch.setenv('DATABASE_URL',url)
        with pytest.raises(ValueError,match='run-owned local test schema'):
            hosting_report_checks(url,'http://127.0.0.1:1',[])


def test_combined_capacity_waits_for_real_dual_brand_acceptance():
    jobs=yaml.safe_load((ROOT/'.github/workflows/enbishan-isolated.yml').read_text())['jobs']
    assert jobs['capacity-economy']['needs']=='dual-brand'
    assert 'if' not in jobs['capacity-economy']
    for name,previous in [('medium','economy'),('proposed','medium')]:
        job=jobs['capacity-'+name]
        assert job['needs']=='capacity-'+previous
        assert "needs.capacity-"+previous+".result == 'failure'" in job['if']
    for name in ['economy','medium','proposed']:
        job=jobs['capacity-'+name]
        assert job['strategy']['matrix']['repetition']==[1,2]
        assert job['env']['KPS_LOAD_RESOURCE_PROFILE']==name
        assert job['services']['postgres']['image']=='postgres:18'
        load=next(s['run'] for s in job['steps'] if s.get('name','').startswith('Retained 50'))
        assert '--max-error-rate 0' in load
        assert '--application-cgroup' in load and '--database-container' in load
        for option in ['--levels','--rounds','--p95-ms','--p99-ms','--request-timeout']:
            assert option not in load
        assert any('--prepare-application-cgroup' in s.get('run','') for s in job['steps'])
        assert job['steps'][-1]['with']['if-no-files-found']=='error'
