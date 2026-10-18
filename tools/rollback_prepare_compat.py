"""Assemble a distinct prior application plus explicit compatibility overlay.

Read-only against the source repository. Writes only a new empty output directory.
Example: python tools/rollback_prepare_compat.py --repository . --output /tmp/rollback-context
The resulting context must independently pass image and application certification.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

FOUNDATION = ('Dockerfile', 'requirements.lock', 'requirements.txt',
              'tools/security_runtime_root.py', 'tools/container_entrypoint.py')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(repository, *args):
    return subprocess.check_output(['git', '-C', str(repository), *args])


def assemble(repository, overlay, output):
    repository, overlay, output = map(lambda p: Path(p).resolve(), (repository, overlay, output))
    manifest_path = overlay / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    baseline = manifest['baseline_sha']
    if not re.fullmatch(r'[0-9a-f]{40}', baseline):
        raise ValueError('Rollback baseline must be an exact full commit SHA.')
    if output == repository or output in repository.parents or (output.exists() and any(output.iterdir())):
        raise ValueError('Output must be a new empty directory, never the source repository.')
    inventory = manifest['files']
    if not inventory:
        raise ValueError('An explicit nonempty compatibility inventory is required.')
    blobs = {}
    for name, expected in inventory.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or relative.parts[0] not in {'app','migrations','public'}:
            raise ValueError('Compatibility path must stay inside application inputs.')
        source = overlay / relative
        if source.is_symlink() or not source.is_file() or digest(source.read_bytes()) != expected:
            raise ValueError('Compatibility overlay hash mismatch: '+name)
        blobs[name] = source.read_bytes()
    if git(repository, 'rev-parse', baseline+'^{commit}').decode().strip() != baseline:
        raise ValueError('Exact baseline commit is unavailable in this repository.')
    candidate_sha = git(repository, 'rev-parse', 'HEAD').decode().strip()
    candidate_tree = git(repository, 'rev-parse', 'HEAD^{tree}').decode().strip()
    # Archive the prior app, never copy the candidate app directory wholesale.
    archive = git(repository, 'archive', '--format=tar', baseline)
    output.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as incoming:
        incoming.extractall(output, filter='data')
    for name, data in blobs.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    # Build the prior app with the candidate's current pinned secure runtime and
    # dependency lock, rather than assuming a historical image is still safe.
    foundation = {}
    for name in FOUNDATION:
        data = git(repository, 'show', candidate_sha+':'+name)
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        foundation[name] = digest(data)
    for name in ('app/orders.py','public/ops.js'):
        if (output / name).read_bytes() == git(repository, 'show', candidate_sha+':'+name):
            raise ValueError('Application rollback must contain a distinct prior application: '+name)
    identity = {'baseline_sha':baseline,'candidate_sha':candidate_sha,'candidate_tree':candidate_tree,
                'overlay_manifest_sha256':digest(manifest_path.read_bytes()),
                'overlay_files':inventory,'secure_foundation_files':foundation,
                'database_restore_performed':False,'production_validated':False,
                'image_certified':False,'fallbacks':manifest['fallbacks']}
    target = output / 'docs/rollback-source-identity.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded=json.dumps(identity, indent=2, sort_keys=True)+'\n'
    target.write_text(encoded)
    # Docker copies app/ into the immutable image; docs/ only copies metrics.md.
    (output/'app/rollback_source_identity.json').write_text(encoded)
    return identity


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--overlay', type=Path, default=Path(__file__).resolve().parent / 'rollback_application_overlay')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(assemble(args.repository, args.overlay, args.output), indent=2, sort_keys=True))
