"""Inspect actual compiled APKs; never equate package integrity with Play Protect approval."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sdk = Path(os.environ.get('ANDROID_HOME') or os.environ['ANDROID_SDK_ROOT'])
tools = sorted((sdk / 'build-tools').glob('*'), key=lambda p: tuple(int(n) for n in re.findall(r'\d+', p.name)))
assert tools, 'Android build-tools are required'
tool = tools[-1]
EXPECTED = {
    'android.permission.INTERNET',
    'android.permission.ACCESS_COARSE_LOCATION',
    'android.permission.ACCESS_FINE_LOCATION',
    'android.permission.FOREGROUND_SERVICE',
    'android.permission.FOREGROUND_SERVICE_LOCATION',
    'android.permission.POST_NOTIFICATIONS',
}


def inspect(apk, release):
    badging = subprocess.check_output([str(tool / 'aapt'), 'dump', 'badging', str(apk)], text=True)
    permissions = subprocess.check_output([str(tool / 'aapt'), 'dump', 'permissions', str(apk)], text=True)
    found = set(re.findall(r"uses-permission(?:-sdk-\d+)?: name='([^']+)'", permissions))
    assert found == EXPECTED, f'Unexpected compiled permissions: {found ^ EXPECTED}'
    assert "package: name='sa.kps.driver'" in badging
    assert "sdkVersion:'26'" in badging and "targetSdkVersion:'35'" in badging
    assert "launchable-activity: name='sa.kps.driver.MainActivity'" in badging
    assert not release or 'application-debuggable' not in badging
    # Check the actual binary manifest, not only the source XML.
    manifest = subprocess.check_output([str(tool / 'aapt'), 'dump', 'xmltree', str(apk), 'AndroidManifest.xml'], text=True)
    for attr in ('allowBackup', 'usesCleartextTraffic'):
        line = next(line for line in manifest.splitlines() if f'android:{attr}(' in line)
        assert line.rstrip().endswith('0x0'), f'{attr} must be false'
    cert = subprocess.run([str(tool / 'apksigner'), 'verify', '--verbose', '--print-certs', str(apk)], text=True, capture_output=True)
    if release:
        assert cert.returncode != 0, 'This preparation job must not silently use any signing key'
    else:
        assert cert.returncode == 0, cert.stderr
        assert 'Android Debug' in cert.stdout
    return {
        'variant': 'release-unsigned' if release else 'debug-inspection-only',
        'sha256': hashlib.sha256(apk.read_bytes()).hexdigest(),
        'size_bytes': apk.stat().st_size,
        'permissions': sorted(found),
        'signature_valid': cert.returncode == 0,
        'certificate_report': cert.stdout if not release else 'Unsigned by design; company signing required',
        'play_protect_approval': 'NOT_ESTABLISHED',
        'installable_distribution_release': False,
    }


reports = [inspect(ROOT / 'app/build/outputs/apk/debug/app-debug.apk', False),
           inspect(ROOT / 'app/build/outputs/apk/release/app-release-unsigned.apk', True)]
dest = ROOT / 'build/reports/release-audit.json'
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps(reports, indent=2) + '\n')
print(json.dumps(reports, indent=2))
