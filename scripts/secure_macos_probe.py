#!/usr/bin/env python3
"""Authenticated encrypted transport for private disposable-runner probes."""
import argparse
import io
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import zipfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b'OGMAC01\0'
MAX_TOTAL = 512 * 1024 * 1024

def key_bytes(path=None):
    value = Path(path).read_text().strip() if path else os.environ['MACOS_PROBE_KEY']
    key = bytes.fromhex(value)
    if len(key) != 32:
        raise ValueError('invalid transport key')
    return key

def seal_directory(source, target, key):
    source, target = Path(source), Path(target)
    if source.is_symlink() or target.exists() or target.is_symlink() or target.resolve().is_relative_to(source.resolve()):
        raise ValueError('unsafe source or encrypted destination')
    buffer = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*')):
            if path.is_symlink():
                raise ValueError('symlinks forbidden')
            if path.is_file():
                total += path.stat().st_size
                if total > MAX_TOTAL:
                    raise ValueError('input too large')
                archive.write(path, path.relative_to(source).as_posix())
    nonce = os.urandom(12)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(MAGIC + nonce + AESGCM(key).encrypt(nonce, buffer.getvalue(), MAGIC))
    target.chmod(0o600)

def open_directory(source, target, key):
    source, target = Path(source), Path(target)
    if target.exists() or target.is_symlink() or source.is_symlink() or source.stat().st_size > MAX_TOTAL:
        raise ValueError('unsafe destination or oversized archive')
    blob = source.read_bytes()
    if blob[:8] != MAGIC:
        raise ValueError('invalid encrypted archive')
    plain = AESGCM(key).decrypt(blob[8:20], blob[20:], MAGIC)
    with zipfile.ZipFile(io.BytesIO(plain)) as archive:
        entries = archive.infolist()
        names = set()
        total = 0
        for entry in entries:
            name = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if (name.is_absolute() or '..' in name.parts or '\\' in entry.filename
                    or not name.parts or entry.is_dir() or (mode & 0o170000) == 0o120000
                    or name.as_posix() in names):
                raise ValueError('unsafe archive entry')
            names.add(name.as_posix())
            total += entry.file_size
            if total > MAX_TOTAL or len(entries) > 10000:
                raise ValueError('oversized archive')
        target.mkdir(parents=True, mode=0o700)
        for entry in entries:
            path = target.joinpath(*PurePosixPath(entry.filename).parts)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(archive.read(entry))
            path.chmod(0o600)

def run_private(input_archive, output_archive):
    key = key_bytes()
    root = Path('data/private-native')
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    source, output = root / 'input', root / 'output'
    output.mkdir(mode=0o700)
    status = 1
    child_env = dict(os.environ)
    child_env.pop('MACOS_PROBE_KEY', None)
    try:
        open_directory(input_archive, source, key)
        with (output / 'collector.log').open('wb') as log:
            result = subprocess.run([sys.executable, 'scripts/probe_macos_real_library.py',
                                     '--input-directory', str(source.resolve()), '--output', str((output / 'probe').resolve())],
                                    stdout=log, stderr=log, timeout=900, check=False, env=child_env)
            status = result.returncode
    except Exception as error:
        # Keep even private runner failure reports free of credentials.
        (output / 'transport-error.txt').write_text(type(error).__name__)
    finally:
        seal_directory(output, output_archive, key)
    print('Private native probe finished; encrypted result prepared.')
    return status

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['seal', 'open', 'run'])
    parser.add_argument('source')
    parser.add_argument('target')
    parser.add_argument('--key-file')
    args = parser.parse_args()
    if args.operation == 'run':
        return run_private(args.source, args.target)
    operation = seal_directory if args.operation == 'seal' else open_directory
    operation(args.source, args.target, key_bytes(args.key_file))
    return 0

if __name__ == '__main__':
    sys.exit(main())
