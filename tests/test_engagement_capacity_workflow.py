"""The combined candidate cannot substitute a lighter load or fake resources."""
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]


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
