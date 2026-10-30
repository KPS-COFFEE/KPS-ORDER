#!/usr/bin/env python3
"""Create and use the owner's KPS Driver key on the owner's computer only."""
import argparse
import getpass
import hashlib
import os
from pathlib import Path
import shutil
import subprocess


SOURCE_ROOT = Path(__file__).resolve().parents[2]
ANDROID_ROOT = Path(__file__).resolve().parents[1]
PASSWORD_ENV = 'KPS_DRIVER_LOCAL_KEY_PASSWORD'


def external_path(value):
    path = Path(value).expanduser().resolve()
    if path == SOURCE_ROOT or SOURCE_ROOT in path.parents:
        raise ValueError('Keep signing keys and backups outside the source checkout.')
    if any((parent / '.git').exists() for parent in [path.parent, *path.parents]):
        raise ValueError('Signing material cannot be stored inside a Git checkout.')
    return path


def private_directory(path):
    """Only accept a new directory, so unrelated folder permissions are untouched."""
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    if os.name == 'nt':
        identity = subprocess.check_output(['whoami'], text=True).strip()
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                        identity + ':(OI)(CI)F'], check=True, stdout=subprocess.DEVNULL)
    else:
        path.chmod(0o700)


def keytool_path():
    executable = shutil.which('keytool')
    if not executable:
        java_home = os.environ.get('JAVA_HOME')
        if java_home:
            candidate = Path(java_home) / 'bin' / ('keytool.exe' if os.name == 'nt' else 'keytool')
            if candidate.is_file():
                executable = str(candidate)
    if not executable:
        raise ValueError('JDK 17 keytool is required. Set JAVA_HOME to the Android Studio JDK.')
    return executable


def key_environment(password):
    environment = os.environ.copy()
    environment[PASSWORD_ENV] = password
    return environment


def certificate(keytool, keystore, alias, environment):
    # Export only the public certificate; never print the keystore or password.
    result = subprocess.run([keytool, '-exportcert', '-keystore', str(keystore),
                             '-alias', alias, '-storepass:env', PASSWORD_ENV],
                            env=environment, check=True, capture_output=True)
    return result.stdout


def create_key(keystore, backup, alias, password):
    keytool = keytool_path()
    if len(password) < 12:
        raise ValueError('Use a password with at least 12 characters.')
    if keystore == backup or keystore.parent == backup.parent:
        raise ValueError('Select separate new folders for the key and backup.')
    if keystore.parent.exists() or backup.parent.exists():
        raise ValueError('Use new dedicated folders. Existing folders/keys are never replaced.')
    environment = key_environment(password)
    private_directory(keystore.parent)
    private_directory(backup.parent)
    # The durable owner-controlled location is the only place a production key is made.
    subprocess.run([keytool, '-genkeypair', '-storetype', 'PKCS12',
                    '-keystore', str(keystore), '-alias', alias,
                    '-keyalg', 'RSA', '-keysize', '3072', '-validity', '10000',
                    '-dname', 'CN=KPS Driver, O=KPS COFFEE',
                    '-storepass:env', PASSWORD_ENV, '-keypass:env', PASSWORD_ENV],
                   env=environment, check=True)
    keystore.chmod(0o600)
    public_certificate = certificate(keytool, keystore, alias, environment)
    # Exclusive creation protects an existing backup, even on an accidental rerun.
    with backup.open('xb') as target, keystore.open('rb') as source:
        shutil.copyfileobj(source, target)
    backup.chmod(0o600)
    if hashlib.sha256(backup.read_bytes()).digest() != hashlib.sha256(keystore.read_bytes()).digest():
        raise ValueError('Backup verification failed. Preserve the original key.')
    fingerprint = hashlib.sha256(public_certificate).hexdigest()
    (keystore.parent / 'certificate-sha256.txt').write_text(fingerprint + '\n', encoding='ascii')
    print('Owner key created; encrypted backup verified. Public certificate SHA-256: ' + fingerprint)
    print('Keep the password in your password manager, separately from both key copies.')


def build_release(keystore, alias, password):
    if not keystore.is_file():
        raise ValueError('Existing signing key not found. No replacement key will be generated.')
    keytool = keytool_path()
    environment = key_environment(password)
    fingerprint = hashlib.sha256(certificate(keytool, keystore, alias, environment)).hexdigest()
    gradle = shutil.which('gradle')
    if not gradle:
        raise ValueError('Gradle 8.11.1 is required on PATH; Android SDK 35 must also be installed.')
    environment.update(KPS_DRIVER_KEYSTORE=str(keystore), KPS_DRIVER_KEY_ALIAS=alias,
                       KPS_DRIVER_STORE_PASSWORD=password, KPS_DRIVER_KEY_PASSWORD=password)
    subprocess.run([gradle, ':app:assembleRelease', ':app:lintRelease', '--no-daemon',
                    '--no-configuration-cache'], cwd=ANDROID_ROOT, env=environment, check=True)
    apk = ANDROID_ROOT / 'app/build/outputs/apk/release/app-release.apk'
    if not apk.is_file():
        raise ValueError('Signed release APK was not produced.')
    sdk = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
    if not sdk:
        raise ValueError('Set ANDROID_HOME to verify the generated APK before use.')
    verifier = Path(sdk) / 'build-tools/35.0.0' / ('apksigner.bat' if os.name == 'nt' else 'apksigner')
    if not verifier.is_file():
        raise ValueError('Install Android SDK Build Tools 35.0.0 to verify the APK.')
    result = subprocess.run([str(verifier), 'verify', '--verbose', '--print-certs', str(apk)],
                            capture_output=True, text=True, check=True)
    digest_line = 'Signer #1 certificate SHA-256 digest: ' + fingerprint
    if digest_line not in result.stdout:
        raise ValueError('The APK signer does not match the owner key. Do not distribute it.')
    print('Release signature verified against owner key. APK: ' + str(apk))
    print('Signing completed. Play Protect and physical-device acceptance remain separate checks.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'build'])
    parser.add_argument('--keystore', required=True, type=external_path)
    parser.add_argument('--backup', type=external_path)
    parser.add_argument('--alias', default='kps-driver')
    args = parser.parse_args()
    if not __import__('sys').stdin.isatty():
        parser.error('Run interactively on the owner computer; passwords must not come from chat or CI.')
    if args.action == 'create':
        if not args.backup:
            parser.error('Specify --backup on a separate owner-controlled backup location.')
        print('Creates the permanent KPS Driver signing identity. Use only if no company release key exists.')
        if input('Type CREATE KPS DRIVER KEY to continue: ') != 'CREATE KPS DRIVER KEY':
            parser.error('Cancelled; no key created.')
    password = getpass.getpass('Signing key password (hidden): ')
    if args.action == 'create':
        if password != getpass.getpass('Confirm password (hidden): '):
            parser.error('Passwords do not match; no key created.')
        create_key(args.keystore, args.backup, args.alias, password)
    else:
        build_release(args.keystore, args.alias, password)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        # CalledProcessError includes arguments, but passwords are never arguments.
        raise SystemExit(str(error))
