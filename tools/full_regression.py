"""Disjoint complete CI shards and an exact-revision coverage gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SHARDS = 3


def collect():
    command = [sys.executable, '-m', 'pytest', '--collect-only', '-q', '--strict-markers']
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        print(result.stdout, result.stderr)
        raise RuntimeError('Full collection failed.')
    nodes = [line.strip() for line in result.stdout.splitlines() if line.startswith('tests/') and '::' in line]
    if not nodes or len(set(nodes)) != len(nodes):
        raise RuntimeError('Collected test identifiers must be nonempty and unique.')
    return nodes


def partition(nodes):
    files = sorted({node.split('::', 1)[0] for node in nodes})
    groups, weights = [[] for _ in range(SHARDS)], [0] * SHARDS
    def weight(file):
        source = (ROOT / file).read_text()
        count = sum(node.startswith(file + '::') for node in nodes)
        # Real browser journeys dominate elapsed time. Assign whole files so
        # each worker preserves its existing fixtures and artifact provenance.
        return count * (12 if 'chromium_browser' in source else 1) + len(source) / 20000
    for file in sorted(files, key=lambda file: (-weight(file), file)):
        index = min(range(SHARDS), key=lambda index: weights[index])
        groups[index].append(file)
        weights[index] += weight(file)
    flattened = [file for group in groups for file in group]
    if len(flattened) != len(set(flattened)) or set(flattened) != set(files):
        raise RuntimeError('Partition lost or duplicated a test file.')
    return groups


def run(index):
    nodes = collect()
    files = partition(nodes)[index]
    selected = [node for node in nodes if node.split('::', 1)[0] in files]
    destination = ROOT / 'test-results'
    destination.mkdir(exist_ok=True)
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    result_path = destination / f'full-shard-{index}.xml'
    manifest = {'revision': revision, 'shard': index, 'shards': SHARDS,
                'all_collected': nodes, 'selected': selected, 'files': files}
    print(f'Full regression shard {index}: {len(selected)} / {len(nodes)} tests, {len(files)} files.', flush=True)
    code = subprocess.call([sys.executable, '-m', 'pytest', '-q', '--strict-markers',
                            *files, '--junitxml=' + str(result_path)], cwd=ROOT)
    manifest['exit_code'] = code
    manifest['xml_exists'] = result_path.is_file()
    if result_path.is_file():
        tree = ET.parse(result_path)
        suites = list(tree.getroot().iter('testsuite'))
        manifest['failures'] = sum(int(s.get('failures', '0')) for s in suites)
        manifest['errors'] = sum(int(s.get('errors', '0')) for s in suites)
        manifest['skipped'] = sum(int(s.get('skipped', '0')) for s in suites)
        # Pytest's junit count also includes subtests. Unique collection IDs
        # above are the complete-suite coverage contract.
        manifest['junit_cases'] = sum(int(s.get('tests', '0')) for s in suites)
    (destination / f'full-shard-{index}.json').write_text(json.dumps(manifest, indent=2))
    return code


def verify(directory, revision):
    paths = sorted(directory.rglob('full-shard-*.json'))
    manifests = [json.loads(path.read_text()) for path in paths]
    if len(manifests) != SHARDS or {m['shard'] for m in manifests} != set(range(SHARDS)):
        raise RuntimeError('All three completed shard manifests are required.')
    expected = set(manifests[0]['all_collected'])
    selected = [node for m in manifests for node in m['selected']]
    for m in manifests:
        if m['revision'] != revision or m['shards'] != SHARDS or set(m['all_collected']) != expected:
            raise RuntimeError('Revision or full-collection mismatch.')
        if m['exit_code'] != 0 or not m['xml_exists'] or any(m.get(k, 1) for k in ('failures', 'errors', 'skipped')):
            raise RuntimeError('Every full shard must pass without skipped tests.')
    if len(selected) != len(set(selected)) or set(selected) != expected:
        raise RuntimeError('Full suite coverage has missing or duplicate tests.')
    print(json.dumps({'revision': revision, 'unique_tests_passed': len(expected), 'failed': 0,
                      'errors': 0, 'skipped': 0, 'junit_cases': sum(m['junit_cases'] for m in manifests),
                      'complete_disjoint_coverage': True}, sort_keys=True))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard', type=int, choices=range(SHARDS))
    parser.add_argument('--verify', type=Path)
    parser.add_argument('--revision')
    args = parser.parse_args()
    if args.verify and args.revision and args.shard is None:
        return verify(args.verify, args.revision)
    if args.shard is not None and args.verify is None:
        return run(args.shard)
    parser.error('Choose --shard or --verify plus --revision.')


if __name__ == '__main__':
    raise SystemExit(main())
