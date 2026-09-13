"""Cross-platform database transfer, with optional passphrase protection."""
import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .store import Store
from .vault import _dpapi
from .worker import worker_lock


def wrapping_key(passphrase, salt):
    if len(passphrase) < 16:
        raise ValueError('Use a transfer passphrase of at least 16 characters.')
    return base64.urlsafe_b64encode(Scrypt(salt=salt,length=32,n=2**15,r=8,p=1).derive(passphrase.encode()))


def write_private(path, data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(data)


def export_bundle(directory, output, passphrase):
    output=Path(output)
    if output.exists():raise ValueError('Output already exists.')
    if not (Path(directory)/'engine.sqlite3').is_file():raise ValueError('Source database not found.')
    salt=os.urandom(16) if passphrase is not None else None
    wrapper=Fernet(wrapping_key(passphrase,salt)) if salt is not None else None
    store=Store(directory)
    try:
        with worker_lock(store.directory):
            fd, temporary=tempfile.mkstemp(prefix='transfer-',suffix='.sqlite3',dir=store.directory)
            os.close(fd)
            try:
                snapshot=sqlite3.connect(temporary)
                try:
                    store.db.backup(snapshot)
                    snapshot.execute("UPDATE settings SET value='0' WHERE key='worker_heartbeat'")
                    snapshot.commit()
                    # A standalone backup must not retain WAL header flags.
                    snapshot.execute('PRAGMA journal_mode=DELETE')
                finally:snapshot.close()
                database=Path(temporary).read_bytes()
            finally:
                for suffix in ('','-wal','-shm','-journal'):
                    Path(temporary+suffix).unlink(missing_ok=True)
            key_path=store.directory/('vault.dpapi' if os.name=='nt' else 'vault.key')
            protected=key_path.read_bytes()
            key=_dpapi(protected,True) if os.name=='nt' else protected
            payload=json.dumps({'database':base64.b64encode(database).decode(),'key':key.decode()}).encode()
            if wrapper:
                envelope={'version':1,'salt':base64.b64encode(salt).decode(),'token':wrapper.encrypt(payload).decode()}
            else:
                # Explicit portable mode: the key travels with the database.
                # This is not encrypted protection for the transfer file.
                envelope={'version':2,'protection':'none','payload':json.loads(payload)}
            write_private(output,json.dumps(envelope).encode())
    finally:store.close()


def restore_bundle(source, directory, passphrase):
    directory=Path(directory)
    if directory.exists():raise ValueError('Restore requires a new data directory; existing data is never overwritten.')
    try:
        envelope=json.loads(Path(source).read_bytes())
        if envelope['version']==2 and envelope.get('protection')=='none' and passphrase is None:
            payload=envelope['payload']
        elif envelope['version']==1 and passphrase is not None:
            salt=base64.b64decode(envelope['salt'],validate=True)
            if len(salt)!=16:raise ValueError()
            wrapper=Fernet(wrapping_key(passphrase,salt))
            payload=json.loads(wrapper.decrypt(envelope['token'].encode()))
        else:raise ValueError()
        database=base64.b64decode(payload['database'],validate=True)
        key=payload['key'].encode()
        cipher=Fernet(key)
        with sqlite3.connect(':memory:') as check:
            check.deserialize(database)
            if check.execute('PRAGMA quick_check').fetchone()!=('ok',):raise ValueError()
            for row in check.execute('SELECT secret FROM accounts'):
                cipher.decrypt(row[0])
    except (ValueError,KeyError,TypeError,InvalidToken,sqlite3.DatabaseError):
        raise ValueError('Could not unlock or validate the transfer bundle.') from None
    protected=_dpapi(key) if os.name=='nt' else key
    directory.mkdir(mode=0o700,parents=False)
    key_path=directory/('vault.dpapi' if os.name=='nt' else 'vault.key')
    database_path=directory/'engine.sqlite3'
    try:
        write_private(key_path,protected)
        write_private(database_path,database)
    except Exception:
        # Only remove the two files created in this new, explicitly named directory.
        key_path.unlink(missing_ok=True)
        database_path.unlink(missing_ok=True)
        directory.rmdir()
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['export','restore'])
    parser.add_argument('bundle',type=Path)
    parser.add_argument('--data-dir',type=Path,default=Path('data'))
    parser.add_argument('--no-passphrase',action='store_true',help='Portable file with its vault key included; keep the file private.')
    args=parser.parse_args()
    try:
        passphrase=None if args.no_passphrase else getpass.getpass('Transfer passphrase (at least 16 characters): ')
        if args.operation=='export':
            if not args.no_passphrase and getpass.getpass('Confirm passphrase: ')!=passphrase:raise ValueError('Passphrases do not match.')
            export_bundle(args.data_dir,args.bundle,passphrase)
        else:restore_bundle(args.bundle,args.data_dir,passphrase)
        if args.operation=='export':
            print('Portable transfer saved with its vault key. Keep this file private.' if args.no_passphrase else 'Encrypted transfer saved.')
        else:print('Data restored. Start the VPS services when ready.')
    except (ValueError,OSError) as exc:
        parser.exit(2,str(exc)+'\n')


if __name__=='__main__':main()
