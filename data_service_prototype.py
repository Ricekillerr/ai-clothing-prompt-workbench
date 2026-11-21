"""Isolated generation transaction experiment; not a Windows service.

Only temporary directories are accepted. No production paths, Windows ACLs,
named pipes, service identity, ZIP handling, or application integration exist.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from pathlib import Path

IDENT = re.compile(r'^[0-9a-f]{32}$')
ASSET = re.compile(r'^references/[0-9a-f]{32}\.(png|mp4)$')


class UnsafeGeneration(RuntimeError):
    pass


def _write_atomic(path, data):
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temp.open('xb') as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)
    if os.name != 'nt':
        fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


class GenerationExperiment:
    def __init__(self, root):
        root = Path(root)
        if root.is_symlink() or not root.is_dir():
            raise UnsafeGeneration('test root must exist and not be a link')
        if not root.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise UnsafeGeneration('only temporary test roots are allowed')
        self.root = root.resolve()
        self.generations = self.root / 'generations'
        if self.generations.is_symlink(): raise UnsafeGeneration('unsafe generation path')
        self.generations.mkdir(exist_ok=True)
        self.current = self.root / 'CURRENT'
        self.journal = self.root / 'transaction.json'
        for path in (self.current, self.journal):
            if path.is_symlink(): raise UnsafeGeneration('unsafe control path')

    def _pointer(self):
        if not self.current.exists(): return None
        value = self.current.read_text(encoding='ascii').strip()
        if not IDENT.fullmatch(value): raise UnsafeGeneration('invalid CURRENT')
        return value

    def inspect(self, operation, arguments):
        """Local read-only operation dispatcher; caller authentication is not implemented."""
        if operation not in {'status', 'verify'} or not isinstance(arguments, dict) or arguments:
            raise UnsafeGeneration('unknown operation or arguments')
        ident = self._pointer()
        if operation == 'verify' and ident is not None:
            self._validate(ident)
        return {'generation_id': ident, 'transaction_pending': self.journal.exists()}

    def _validate(self, ident):
        if not isinstance(ident, str) or not IDENT.fullmatch(ident): raise UnsafeGeneration('invalid id')
        directory = self.generations / ident
        if directory.is_symlink() or not directory.is_dir(): raise UnsafeGeneration('missing generation')
        manifest_path = directory / 'manifest.json'
        if manifest_path.is_symlink(): raise UnsafeGeneration('unsafe manifest')
        try: manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc: raise UnsafeGeneration('invalid manifest') from exc
        if not isinstance(manifest, dict) or 'db.sqlite3' not in manifest: raise UnsafeGeneration('missing database')
        files = []
        for path in directory.rglob('*'):
            if path.is_symlink(): raise UnsafeGeneration('links forbidden')
            if path.is_file() and path != manifest_path: files.append(path.relative_to(directory).as_posix())
        if set(files) != set(manifest): raise UnsafeGeneration('file mismatch')
        for name, digest in manifest.items():
            if name != 'db.sqlite3' and not ASSET.fullmatch(name): raise UnsafeGeneration('invalid asset id')
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
                raise UnsafeGeneration('hash mismatch')
        db = sqlite3.connect((directory / 'db.sqlite3').as_uri() + '?mode=ro', uri=True)
        try:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise UnsafeGeneration('database corrupt')
            if db.execute('PRAGMA foreign_key_check').fetchall(): raise UnsafeGeneration('foreign key errors')
        finally: db.close()

    def recover(self, checkpoint=lambda _: None):
        if not self.journal.exists():
            ident = self._pointer()
            if ident is not None: self._validate(ident)
            return 'clean'
        try: record = json.loads(self.journal.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc: raise UnsafeGeneration('invalid transaction') from exc
        if (not isinstance(record, dict) or set(record) != {'old', 'new', 'state'}
                or record['state'] not in {'PREPARED', 'COMMITTED'}
                or not IDENT.fullmatch(str(record['new']))
                or (record['old'] is not None and not IDENT.fullmatch(str(record['old'])))):
            raise UnsafeGeneration('invalid transaction')
        old, new = record['old'], record['new']
        pointer = self._pointer()
        if pointer not in {old, new}: raise UnsafeGeneration('unexpected CURRENT')
        target = new if record['state'] == 'COMMITTED' else old
        if target is not None:
            self._validate(target)
            _write_atomic(self.current, (target + '\n').encode('ascii'))
        elif self.current.exists():
            self.current.unlink()
        checkpoint('recovery_pointer')
        if self._pointer() != target: raise UnsafeGeneration('pointer verification failed')
        archive = self.root / ('transaction-' + new + '.json')
        if archive.exists(): raise UnsafeGeneration('duplicate transaction archive')
        os.replace(self.journal, archive)
        return 'recovered'

    def install(self, database, assets, checkpoint=lambda _: None):
        self.recover()
        if not isinstance(database, bytes) or not isinstance(assets, dict): raise UnsafeGeneration('bad input')
        if any(not ASSET.fullmatch(name) or not isinstance(data, bytes) for name, data in assets.items()):
            raise UnsafeGeneration('bad asset identifier')
        old = self._pointer()
        if old is not None: self._validate(old)
        ident = uuid.uuid4().hex
        folder = self.generations / ident
        folder.mkdir()
        payloads = {'db.sqlite3': database, **assets}
        manifest = {}
        for name, data in payloads.items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as out:
                out.write(data); out.flush(); os.fsync(out.fileno())
            manifest[name] = hashlib.sha256(data).hexdigest()
        _write_atomic(folder / 'manifest.json', json.dumps(manifest, sort_keys=True).encode('utf-8'))
        self._validate(ident)
        checkpoint('generation_ready')
        record = {'old': old, 'new': ident, 'state': 'PREPARED'}
        _write_atomic(self.journal, json.dumps(record, sort_keys=True).encode('utf-8'))
        checkpoint('journal_prepared')
        _write_atomic(self.current, (ident + '\n').encode('ascii'))
        checkpoint('pointer_switched')
        self._validate(ident)
        record['state'] = 'COMMITTED'
        _write_atomic(self.journal, json.dumps(record, sort_keys=True).encode('utf-8'))
        checkpoint('journal_committed')
        self.recover()
        return ident
