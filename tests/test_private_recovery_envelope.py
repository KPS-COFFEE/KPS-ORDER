"""Synthetic public-key transfer tests; never access a production source."""
from pathlib import Path
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest
from tools.private_recovery_envelope import seal, unseal, snapshot, write_private, MAGIC


@pytest.fixture(scope='module')
def keys():
    key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    return (key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()),
            key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))


def test_only_public_key_required_on_source_and_envelope_authenticates(keys):
    private,public=keys
    value=b'synthetic-auth-and-fiscal-archive'
    envelope=seal(value,public)
    assert envelope.startswith(MAGIC) and value not in envelope
    assert unseal(envelope,private)==value
    assert envelope!=seal(value,public)
    with pytest.raises(InvalidTag):unseal(envelope[:-1]+bytes([envelope[-1]^1]),private)
    with pytest.raises(ValueError):unseal(envelope[:100],private)


def test_wrong_private_key_cannot_read_snapshot(keys):
    _,public=keys
    wrong=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    wrong_pem=wrong.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    with pytest.raises(ValueError):unseal(seal(b'synthetic',public),wrong_pem)


def test_private_output_exclusive_0600(tmp_path):
    path=tmp_path/'encrypted.bin'
    write_private(path,b'synthetic ciphertext')
    assert path.stat().st_mode&0o777==0o600
    with pytest.raises(FileExistsError):write_private(path,b'overwrite')


def test_source_revision_mismatch_stops_before_database(monkeypatch):
    monkeypatch.setenv('RENDER_GIT_COMMIT','synthetic-other-revision')
    with pytest.raises(ValueError,match='revision'):snapshot('synthetic-expected-revision')
