#!/usr/bin/env python3
"""Compose an OCI static-review artifact without container execution.

This is not a Dockerfile build or runtime certification. All input paths are
local; the script neither downloads packages nor contacts application services.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


def digest(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-oci', type=Path, required=True)
    p.add_argument('--runtime-target', type=Path, required=True)
    p.add_argument('--font-deb', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if args.output.exists():
        raise SystemExit('Output must not exist; preserve previous evidence.')
    expected_font = '8892669e51aab4dc56682c8e39d8ddb7d70fad83c369344e1e240bf3ca22bb76'
    if hashlib.sha256(args.font_deb.read_bytes()).hexdigest() != expected_font:
        raise SystemExit('Unexpected fonts-dejavu-core 2.37-6 package hash')
    out = args.output
    blobs = out / 'blobs' / 'sha256'
    blobs.mkdir(parents=True)
    base_blobs = args.base_oci / 'blobs' / 'sha256'
    descriptor = json.loads((args.base_oci / 'index.json').read_text())['manifests'][0]
    if descriptor['digest'] != 'sha256:2ed6491b93cd49272ee6de2b5a38440c3448360322c089fc23e370722d74179d':
        raise SystemExit('Unexpected base Linux amd64 image manifest')
    def read_blob(d):
        data = (base_blobs / d.split(':')[1]).read_bytes()
        if digest(data) != d:
            raise SystemExit('Corrupt base blob: ' + d)
        return data
    manifest = json.loads(read_blob(descriptor['digest']))
    config = json.loads(read_blob(manifest['config']['digest']))
    base_files = {}
    for layer in manifest['layers']:
        data = read_blob(layer['digest'])
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for member in archive:
                name = member.name.lstrip('./')
                if name in ('etc/passwd', 'etc/group', 'var/lib/dpkg/status') and member.isfile():
                    base_files[name] = archive.extractfile(member).read()
    for f in base_blobs.iterdir():
        shutil.copyfile(f, blobs / f.name)
    evidence = []
    layer_raw = io.BytesIO()
    with tarfile.open(fileobj=layer_raw, mode='w', format=tarfile.PAX_FORMAT) as archive:
        def add_bytes(name, data, uid=0, gid=0, mode=0o644):
            t = tarfile.TarInfo(name)
            t.size, t.mode, t.uid, t.gid, t.mtime = len(data), mode, uid, gid, 0
            archive.addfile(t, io.BytesIO(data))
        def add_tree(source, prefix, uid=0, gid=0):
            for file in sorted(source.rglob('*')):
                if file.is_symlink():
                    raise SystemExit('Symlink refused in local runtime input: ' + str(file))
                if not file.is_file() or '__pycache__' in file.parts or file.suffix == '.pyc':
                    continue
                name = prefix + '/' + str(file.relative_to(source))
                data = file.read_bytes()
                add_bytes(name, data, uid, gid, 0o755 if os.access(file, os.X_OK) else 0o644)
                if name.startswith('app/'):
                    evidence.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest()})
        # pip --target's generated scripts have host-interpreter shebangs and
        # aren't used. Module execution uses the site-packages tree below.
        for child in sorted(args.runtime_target.iterdir()):
            if child.name in ('bin', '__pycache__'):
                continue
            dest = 'usr/local/lib/python3.12/site-packages/' + child.name
            if child.is_dir():
                add_tree(child, dest)
            elif child.is_file():
                add_bytes(dest, child.read_bytes())
        for name in ('app', 'migrations', 'data', 'public'):
            add_tree(repo / name, 'app/' + name, 10001, 10001)
        for name in ('docs/metrics.md', 'tools/portable_backup.py', 'requirements.lock'):
            data = (repo / name).read_bytes()
            add_bytes('app/' + name, data, 10001, 10001)
            evidence.append({'path': 'app/' + name, 'sha256': hashlib.sha256(data).hexdigest()})
        add_tree(repo / 'tools/windows-print-bridge', 'app/tools/windows-print-bridge', 10001, 10001)
        # Use the application's exact transformation on local static assets.
        sys.path.insert(0, str(repo))
        from app.image_previews import thumbnail
        for source in sorted((repo / 'public/assets').iterdir()):
            if source.is_file() and source.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp'):
                add_bytes('app/public/previews/' + source.name + '.webp', thumbnail(source.read_bytes()), 10001, 10001)
        font_control = subprocess.check_output(['dpkg-deb', '-f', str(args.font_deb)])
        font_tar = subprocess.check_output(['dpkg-deb', '--fsys-tarfile', str(args.font_deb)])
        with tarfile.open(fileobj=io.BytesIO(font_tar)) as fonts:
            for member in fonts:
                name = member.name.lstrip('./')
                if member.isfile():
                    add_bytes(name, fonts.extractfile(member).read(), mode=member.mode)
                elif member.issym():
                    member.name = name
                    member.uid = member.gid = member.mtime = 0
                    archive.addfile(member)
        for name, suffix in (
            ('etc/passwd', b'kpsorder:x:10001:10001::/home/kpsorder:/bin/sh\n'),
            ('etc/group', b'kpsorder:x:10001:\n'),
            ('var/lib/dpkg/status', b'\n' + font_control.replace(b'Package: fonts-dejavu-core\n', b'Package: fonts-dejavu-core\nStatus: install ok installed\n') + b'\n'),
        ):
            add_bytes(name, base_files[name].rstrip(b'\n') + b'\n' + suffix)
    raw = layer_raw.getvalue()
    compressed = gzip.compress(raw, mtime=0)
    d = digest(compressed)
    (blobs / d.split(':')[1]).write_bytes(compressed)
    manifest['layers'].append({'mediaType':'application/vnd.oci.image.layer.v1.tar+gzip','digest':d,'size':len(compressed)})
    config['rootfs']['diff_ids'].append(digest(raw))
    config['config'].update(User='10001:10001', WorkingDir='/app', ExposedPorts={'10000/tcp':{}}, Cmd=['/bin/sh','-c','exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 2 --timeout-keep-alive 15'])
    config['config']['Env'] += ['PYTHONDONTWRITEBYTECODE=1','PYTHONUNBUFFERED=1','PIP_NO_CACHE_DIR=1','PORT=10000']
    revision = subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    dirty = bool(subprocess.check_output(['git','status','--porcelain'],cwd=repo,text=True))
    source_digest = digest(json.dumps(sorted(evidence,key=lambda e:e['path']),sort_keys=True).encode())
    config['config']['Labels'] = {'org.opencontainers.image.revision':revision, 'com.kps.security.assembly':'daemonless-static-review-only', 'com.kps.security.source-content':source_digest, 'com.kps.security.source-dirty':str(dirty).lower()}
    config.setdefault('history',[]).append({'created_by':'tools/security_image_assemble.py: static OCI assembly; Dockerfile RUN not executed'})
    def write_json(data,media):
        data=json.dumps(data,sort_keys=True,separators=(',',':')).encode();d=digest(data);(blobs/d.split(':')[1]).write_bytes(data)
        return {'mediaType':media,'digest':d,'size':len(data)}
    manifest['config']=write_json(config,'application/vnd.oci.image.config.v1+json')
    final=write_json(manifest,'application/vnd.oci.image.manifest.v1+json')
    (out/'index.json').write_text(json.dumps({'schemaVersion':2,'manifests':[final]},indent=2))
    (out/'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}')
    result={'image_manifest_digest':final['digest'],'config_digest':manifest['config']['digest'],'base_manifest_digest':descriptor['digest'],'source_head':revision,'source_dirty':dirty,'source_content_digest':source_digest,'source_files':evidence,'limitations':['Static OCI composition; canonical Dockerfile build and container execution not performed.','No claim of equality to historical production image.','Fonts package extracted and registered; apt-get/useradd RUN not executed.','Application preview transform ran under host Python, same locked Pillow.']}
    (out.parent/(out.name+'-provenance.json')).write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='source_files'},indent=2))

if __name__=='__main__':
    main()
