"""Package a Debian13/Python3.12 runtime without OS administration utilities.

Copies complete Debian binary packages and their truthful dpkg controls/file lists.
No vulnerability filters or package-name/version rewriting are used.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

root = Path('/runtime-root')
local = Path('/usr/local')
# These optional terminal/SQLite/libuuid adapters are unused by the web app.
# SQLite was already unavailable in the previous certified image. uuid.py has
# its standard pure-Python fallback; uuid4 continues to use os.urandom.
for pattern in ('_sqlite3*.so', '_curses*.so', 'readline*.so', '_uuid*.so', '_tkinter*.so'):
    for p in (local / 'lib/python3.12/lib-dynload').glob(pattern):
        p.unlink()
shutil.copytree(local, root / 'usr/local', symlinks=True, dirs_exist_ok=True)

packages = {'fonts-dejavu-core', 'libstdc++6', 'libgcc-s1'}
with_shell = os.environ.get('KPS_RUNTIME_SHELL') == '1'
if with_shell:
    packages.add('dash')
for p in [local / 'bin/python3.12', *local.rglob('*.so'), *local.rglob('*.so.*')]:
    if not p.is_file():
        continue
    result = subprocess.run(['ldd', str(p)], text=True, capture_output=True)
    if 'not found' in result.stdout:
        raise RuntimeError(f'Unresolved ELF dependency: {p}: {result.stdout}')
    for name in re.findall(r'(?:=>\s*)?(/[^\s()]+)', result.stdout):
        path = Path(name).resolve()
        if str(path).startswith('/usr/local/'):
            continue
        # Debian13 merged-/usr owns these paths. Ask dpkg, never infer versions.
        owner = subprocess.check_output(['dpkg-query', '-S', str(path)], text=True).split(': /', 1)[0]
        packages.add(owner.split(':')[0])

# Copy the installed dependency closure too, including provider modules and
# licensing/version packages that are not discoverable through static ldd.
pending = list(packages)
while pending:
    package = pending.pop()
    fields = subprocess.check_output(['dpkg-query', '-W', '-f=${Depends}, ${Pre-Depends}', package], text=True)
    for group in fields.split(','):
        if not group.strip():
            continue
        for alternative in group.split('|'):
            dependency = alternative.strip().split()[0]
            if dependency.endswith(':any'):
                dependency = dependency[:-4]
            installed = subprocess.run(['dpkg-query', '-W', '-f=${db:Status-Status}', dependency], text=True, capture_output=True)
            if installed.returncode == 0 and installed.stdout == 'installed':
                if dependency not in packages:
                    packages.add(dependency)
                    pending.append(dependency)
                break
        else:
            raise RuntimeError('Unresolved installed package dependency: ' + group)

for package in packages:
    name = package.split(':')[0]
    if name in {'libacl1', 'libmount1', 'libblkid1', 'util-linux', 'mount', 'gzip', 'ncurses-bin', 'libtinfo6', 'libsqlite3-0', 'libuuid1'}:
        raise RuntimeError('Unexpected administration/terminal dependency: ' + package)

status = root / 'var/lib/dpkg/status.d'
status.mkdir(parents=True, exist_ok=True)
info = root / 'var/lib/dpkg/info'
info.mkdir(parents=True, exist_ok=True)
records = []
for package in sorted(packages):
    control = subprocess.check_output(['dpkg-query', '-s', package], text=True)
    name = next(line.split(': ', 1)[1] for line in control.splitlines() if line.startswith('Package: '))
    # Replace controls only for complete packages actually copied below.
    for p in status.iterdir():
        if p.is_file() and f'Package: {name}\n' in p.read_text():
            p.unlink()
    files = subprocess.check_output(['dpkg-query', '-L', package], text=True).splitlines()
    for filename in files:
        src = Path(filename)
        if filename == '/.' or not (src.exists() or src.is_symlink()):
            continue
        dest = root / filename.lstrip('/')
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            dest.symlink_to(os.readlink(src))
        elif src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            shutil.copy2(src, dest)
    (status / name).write_text(control)
    (info / (name + '.list')).write_text('\n'.join(files) + '\n')
    # Refresh the exact file checksums as well as controls after replacement.
    architecture = next(line.split(': ', 1)[1] for line in control.splitlines() if line.startswith('Architecture: '))
    for filename in (f'{name}:{architecture}.md5sums', f'{name}.md5sums'):
        sums = Path('/var/lib/dpkg/info') / filename
        if sums.exists():
            shutil.copyfile(sums, info / (name + '.md5sums'))
            shutil.copyfile(sums, status / (name + '.md5sums'))
            break
    records.append({'package': name, 'version': next(line.split(': ', 1)[1] for line in control.splitlines() if line.startswith('Version: '))})
# Retain the base's existing controls; remove only actual duplicates of copied packages.
legacy = root / 'var/lib/dpkg/status'
if legacy.exists():
    names = {x['package'] for x in records}
    legacy.write_text('\n\n'.join(block for block in legacy.read_text().split('\n\n') if not any(f'Package: {name}\n' in block + '\n' for name in names)) + '\n')
if with_shell:
    shell = root / 'usr/bin/sh'
    if shell.exists() or shell.is_symlink():
        shell.unlink()
    shell.symlink_to('dash')
(root / 'etc/passwd').write_text('root:x:0:0:root:/root:/sbin/nologin\nkpsorder:x:10001:10001::/nonexistent:/sbin/nologin\n')
(root / 'etc/group').write_text('root:x:0:\nkpsorder:x:10001:\n')
for p in root.rglob('*'):
    if p.is_file() and not p.is_symlink():
        p.chmod(p.stat().st_mode & ~0o6000)
manifest = root / 'usr/local/share/kps/runtime-packages.json'
manifest.parent.mkdir(parents=True, exist_ok=True)
manifest.write_text(json.dumps({'suite': 'trixie', 'python': '3.12', 'rollback_shell': with_shell, 'complete_packages': records}, indent=2) + '\n')
shutil.copyfile('/tmp/container_entrypoint.py', root / 'usr/local/bin/kps-order-start.py')
print(manifest.read_text())
