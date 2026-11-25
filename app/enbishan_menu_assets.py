"""Immutable owner-reviewed menu artwork. No URL fetches or arbitrary file access."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads(Path(__file__).with_suffix('.json').read_text())


def valid_bundled(path):
    entry = next((v for v in MANIFEST.values() if v['path'] == path), None)
    if not entry or not path.startswith('/assets/enbishan-menu/'):
        return False
    asset = ROOT / 'public' / path.lstrip('/')
    if not asset.is_file():
        return False
    stat = asset.stat()
    return _verified(str(asset), entry['sha256'], stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=64)
def _verified(filename, expected_hash, modified_ns, size):
    # Immutable deployment assets are hashed once; replacement invalidates the
    # cache through stat metadata. The bounded cache retains booleans, not bytes.
    with open(filename, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == expected_hash


def resolve(reference):
    entry = MANIFEST.get(reference)
    if entry and valid_bundled(entry['path']):
        return entry['path']
    return None
