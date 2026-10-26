"""Choose genuine targeted hotfix coverage only for the exact reviewed delta."""
import argparse
import json
import subprocess
from pathlib import Path

BASELINE = '792c471f1079d23222fb71453ba533758ade1dc4'
APP_BLOBS = {
    'app/aggregate_driver_closure.py': 'abe273132e309abb192a8b2ec6e781039271ffb0',
    'public/ops.js': '2fbff63e28e49277bced631aa201cada4682997e',
}
ALLOWED = set(APP_BLOBS) | {
    'tests/test_aggregate_driver_closure.py', 'tests/test_physical_receipt_boundary.py',
    'tests/test_ops_error_messages.cjs',
    '.github/workflows/application-rollback-certification.yml',
    '.github/workflows/security-closure.yml', '.github/workflows/physical-receipt-hotfix.yml',
    'tools/receipt_hotfix_scope.py', 'docs/receipt-hotfix-tests.txt',
}

def git(*args):
    return subprocess.check_output(['git', *args], text=True, stderr=subprocess.DEVNULL).strip()

def select():
    reason = 'Exact reviewed receipt delta; all other repository inputs unchanged.'
    try:
        subprocess.check_call(['git', 'merge-base', '--is-ancestor', BASELINE, 'HEAD'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        changed = set(git('diff', '--name-only', BASELINE, 'HEAD').splitlines())
        if not set(APP_BLOBS).issubset(changed) or changed - ALLOWED:
            raise ValueError('Changes exceed the reviewed hotfix allowlist.')
        if any(git('rev-parse', 'HEAD:' + path) != sha for path, sha in APP_BLOBS.items()):
            raise ValueError('Application blob differs from the exact reviewed hotfix.')
        manifest = 'docs/receipt-hotfix-tests.txt'
        suites = [line.strip() for line in Path(manifest).read_text().splitlines()
                  if line.strip() and not line.startswith('#')]
        if len(suites) != 16 or len(set(suites)) != 16 or not all(Path(p).is_file() for p in suites):
            raise ValueError('Targeted test manifest is incomplete.')
        return manifest, {'coverage': 'targeted_receipt_hotfix', 'full_operational_certificate': False,
                          'baseline': BASELINE, 'revision': git('rev-parse', 'HEAD'),
                          'reviewed_application_blobs': APP_BLOBS, 'suites': suites, 'reason': reason}
    except (subprocess.CalledProcessError, ValueError, OSError) as error:
        return 'docs/final-operational-tests.txt', {'coverage': 'full_operational',
                'baseline': BASELINE, 'reason': str(error)}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    manifest, evidence = select()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + '\n')
    print(manifest)
