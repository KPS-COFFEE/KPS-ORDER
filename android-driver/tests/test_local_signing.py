"""Exercise the owner-local key lifecycle with disposable test-only keys."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / 'tools/local_signing.py'
spec = importlib.util.spec_from_file_location('local_signing', MODULE_PATH)
signing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signing)


class LocalSigningTests(unittest.TestCase):
    def test_real_encrypted_key_backup_and_identity(self):
        # These test keys are never used for KPS release signing and are destroyed.
        with tempfile.TemporaryDirectory(prefix='kps-signing-TEST-ONLY-') as directory:
            root = Path(directory)
            key = root / 'owner/test-only.p12'
            backup = root / 'backup/test-only.p12'
            password = 'Disposable-test-password-only!'
            signing.create_key(key, backup, 'test-only', password)
            original = key.read_bytes()
            self.assertEqual(original, backup.read_bytes())
            cert = signing.certificate(signing.keytool_path(), key, 'test-only',
                                       signing.key_environment(password))
            self.assertEqual(hashlib.sha256(cert).hexdigest(),
                             (key.parent / 'certificate-sha256.txt').read_text().strip())
            with self.assertRaises(subprocess.CalledProcessError):
                signing.certificate(signing.keytool_path(), key, 'test-only',
                                    signing.key_environment('wrong-password'))
            with self.assertRaises(ValueError):
                signing.create_key(key, backup, 'test-only', password)
            self.assertEqual(original, key.read_bytes())

    def test_keys_and_backups_cannot_live_in_checkout(self):
        with self.assertRaises(ValueError):
            signing.external_path(str(signing.SOURCE_ROOT / 'private/key.p12'))

    def test_other_git_checkout_is_also_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.git').write_text('gitdir: elsewhere')
            with self.assertRaises(ValueError):
                signing.external_path(str(root / 'private/key.p12'))

    def test_noninteractive_creation_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = subprocess.run(['python3', str(MODULE_PATH), 'create',
                                     '--keystore', str(root / 'key/file.p12'),
                                     '--backup', str(root / 'backup/file.p12')],
                                    input='', text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / 'key').exists())


if __name__ == '__main__':
    unittest.main()
