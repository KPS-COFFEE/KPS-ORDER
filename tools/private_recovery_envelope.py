"""Private-shell recovery transfer. Public key goes to source; only ciphertext returns.

Never run migrations or source writes. Keep the private key separately protected.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b'KPSPUB1\x00'


def write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def seal(data, public_pem):
    public = serialization.load_pem_public_key(public_pem)
    if not isinstance(public, rsa.RSAPublicKey) or public.key_size not in {3072,4096}:
        raise ValueError('RSA public key of at least 3072 bits required')
    key, nonce = secrets.token_bytes(32), secrets.token_bytes(12)
    wrapped = public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=MAGIC))
    header = MAGIC + len(wrapped).to_bytes(2, 'big') + wrapped + nonce
    return header + AESGCM(key).encrypt(nonce, data, header)


def unseal(envelope, private_pem):
    if not envelope.startswith(MAGIC) or len(envelope) < len(MAGIC)+2:
        raise ValueError('Invalid encrypted recovery envelope')
    start = len(MAGIC)+2
    size = int.from_bytes(envelope[len(MAGIC):start], 'big')
    if size not in {384,512} or len(envelope) < start+size+12+16:
        raise ValueError('Invalid encrypted recovery envelope length')
    private = serialization.load_pem_private_key(private_pem, password=None)
    if not isinstance(private, rsa.RSAPrivateKey) or private.key_size < 3072:
        raise ValueError('Invalid private recovery key')
    key = private.decrypt(envelope[start:start+size], padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=MAGIC))
    nonce, header = envelope[start+size:start+size+12], envelope[:start+size+12]
    return AESGCM(key).decrypt(nonce, envelope[len(header):], header)


def snapshot(expected_revision):
    """In-memory adaptation to the exact running 541 source; no file/code migration."""
    from app import db, exports
    revision = os.getenv('RENDER_GIT_COMMIT') or os.getenv('RUNTIME_REVISION')
    if revision != expected_revision:
        raise ValueError('Runtime revision differs from explicitly approved source')
    # Migration074 is absent from the old export registry. It is auth material.
    if 'owner_phone_challenges' not in exports.TABLES:
        sequence = list(exports.TABLES)
        sequence.insert(sequence.index('sessions')+1, 'owner_phone_challenges')
        exports.TABLES = tuple(sequence)
        exports.AUTH_TABLES = set(exports.AUTH_TABLES) | {'owner_phone_challenges'}
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL TIME ZONE 'UTC'")
        actual = conn.execute('SELECT version,sha256 FROM schema_migrations ORDER BY version').fetchall()
        expected = [{'version':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                    for p in sorted((db.ROOT/'migrations').glob('*.sql'))]
        if actual != expected:
            raise ValueError('Source migration checksums differ from running checkout')
        contents, manifest = exports.build_archive(conn, full=True)
        states = []
        for row in conn.execute('SELECT sequencename,start_value,min_value,max_value,increment_by,cycle,cache_size FROM pg_sequences WHERE schemaname=current_schema() ORDER BY sequencename'):
            from psycopg import sql
            state = conn.execute(sql.SQL('SELECT last_value,is_called FROM {}').format(sql.Identifier(row['sequencename']))).fetchone()
            states.append(dict(row, **state))
    manifest['source_revision'] = revision
    name = 'metadata/sequences.json'
    value = json.dumps(states, sort_keys=True, separators=(',', ':')).encode()
    manifest['files'][name] = {'sha256':hashlib.sha256(value).hexdigest(),'size_bytes':len(value)}
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(contents)) as original, zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for member in original.namelist():
            if member not in {'manifest.json',name}:
                archive.writestr(member,original.read(member))
        archive.writestr(name,value)
        archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(',', ':'),default=exports._plain).encode())
    result = output.getvalue()
    exports.verify_archive(result,require_full=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest='action',required=True)
    keygen = actions.add_parser('keygen')
    keygen.add_argument('--private-key',required=True)
    keygen.add_argument('--public-key',required=True)
    source = actions.add_parser('snapshot')
    source.add_argument('--public-key-base64',required=True)
    source.add_argument('--expected-revision',required=True)
    source.add_argument('--output',required=True)
    chunk = actions.add_parser('chunk')
    chunk.add_argument('--input',required=True)
    chunk.add_argument('--offset',type=int,required=True)
    chunk.add_argument('--length',type=int,default=3072)
    assemble = actions.add_parser('assemble')
    assemble.add_argument('--chunks',nargs='+',required=True,help='Ordered local base64-only chunk files')
    assemble.add_argument('--expected-sha256',required=True)
    assemble.add_argument('--output',required=True)
    restore = actions.add_parser('restore')
    restore.add_argument('--input',required=True)
    restore.add_argument('--private-key',required=True)
    restore.add_argument('--expected-sha256',required=True)
    restore.add_argument('--assets-output',required=True)
    restore.add_argument('--evidence-output',required=True)
    args = parser.parse_args()
    if args.action == 'keygen':
        private = rsa.generate_private_key(public_exponent=65537,key_size=3072)
        write_private(args.private_key,private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        write_private(args.public_key,private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
        print('Local private/public recovery key files created; no private key output.')
    elif args.action == 'snapshot':
        data = seal(snapshot(args.expected_revision),base64.b64decode(args.public_key_base64,validate=True))
        write_private(args.output,data)
        print(json.dumps({'encrypted_bytes':len(data),'encrypted_sha256':hashlib.sha256(data).hexdigest(),'source_revision':args.expected_revision}))
    elif args.action == 'chunk':
        if args.offset < 0 or not 1 <= args.length <= 12288:
            raise ValueError('Chunk bounds exceed private transfer limit')
        with open(args.input,'rb') as source:
            if source.read(len(MAGIC)) != MAGIC:
                raise ValueError('Ciphertext envelope required')
            source.seek(args.offset)
            data = source.read(args.length)
        print('KPS_CIPHERTEXT_BEGIN',args.offset,len(data),hashlib.sha256(data).hexdigest())
        encoded = base64.b64encode(data).decode()
        for pos in range(0,len(encoded),64): print(encoded[pos:pos+64])
        print('KPS_CIPHERTEXT_END')
    elif args.action == 'assemble':
        data = b''.join(base64.b64decode(''.join(Path(p).read_text().split()),validate=True) for p in args.chunks)
        if not data.startswith(MAGIC) or hashlib.sha256(data).hexdigest() != args.expected_sha256:
            raise ValueError('Ciphertext transfer checksum mismatch')
        write_private(args.output,data)
        print('Ciphertext reconstructed and checksum verified; no plaintext created.')
    else:
        from app.exports import restore_archive
        import uuid
        data = Path(args.input).read_bytes()
        if hashlib.sha256(data).hexdigest() != args.expected_sha256:
            raise ValueError('Stored ciphertext digest mismatch')
        plaintext = unseal(data,Path(args.private_key).read_bytes())
        result = restore_archive(plaintext,target_url=os.environ['KPS_RESTORE_DATABASE_URL'],
                                 target_schema='kps_restore_'+uuid.uuid4().hex,asset_directory=args.assets_output)
        result['encrypted_source_sha256'] = args.expected_sha256
        write_private(args.evidence_output,json.dumps(result,sort_keys=True,separators=(',', ':')).encode())
        print('Isolated restore verified; plaintext remained in process memory.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Source/crypto/DB exception messages can contain sensitive details.
        print('Private recovery operation failed; no sensitive error details emitted.')
        raise SystemExit(1) from None
