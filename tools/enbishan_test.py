"""Fail-closed local dual-brand test entry point. No production fallback."""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def main():
    url = os.getenv('TEST_DATABASE_URL', '')
    parts = urlsplit(url)
    if (parts.scheme not in {'postgresql', 'postgres'} or parts.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or not parts.path.removeprefix('/').startswith('test_enbishan_') or parts.query or parts.fragment):
        raise SystemExit('Set TEST_DATABASE_URL to a local test_enbishan_* database. No runtime fallback is permitted.')
    env = {k:v for k,v in os.environ.items() if k not in {'DATABASE_URL', 'TAWKED_API_KEY', 'ENBISHAN_TAWKED_API_KEY', 'RESEND_API_KEY',
           'TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_VERIFY_SERVICE_SID', 'RENDER_API_KEY',
           'MAIL_OUTBOX_KEY', 'SMTP_PASSWORD'} and not k.startswith('KPS_PAYMENTS_')}
    env.update(APP_ENV='testing', OTP_PROVIDER='disabled', EMAIL_PROVIDER='disabled',
               EMAIL_WORKER_ENABLED='false', REPORT_WORKER_ENABLED='false')
    output = ROOT/'test-results'/'enbishan'
    output.mkdir(parents=True, exist_ok=True)
    args = sys.argv[1:] or ['tests/test_enbishan_dual_brand.py']
    return subprocess.call([sys.executable,'-m','pytest','-q',*args,
                            '--junitxml='+str(output/'results.xml')],cwd=ROOT,env=env)


if __name__ == '__main__':
    raise SystemExit(main())
