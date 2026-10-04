"""Bounded, read-only iPod database inventory; no device acceptance claim.

Binary section interpretation follows libgpod itdb_itunesdb.c. Similarity
envelopes follow the Music version-1 observations, not verified iPod semantics.
Identifiers, CUIDs, keys, schema SQL, paths, and BLOB contents are never emitted.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import struct
import time

MAX_BYTES = 32 * 1024 * 1024
KNOWN_FILES = ('iTunesDB', 'iTunesCDB', 'Genius.itdb', 'iTunesSD',
               'Library.itdb', 'Locations.itdb', 'Dynamic.itdb', 'Extras.itdb')
KNOWN_TABLES = ('genius_metadata', 'genius_similarities', 'genius_config',
                'item', 'db_info', 'container', 'container_seed')


def _no_symlinks(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('symlink_rejected')


def read_bounded(path):
    path = Path(os.path.abspath(path))
    _no_symlinks(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('not_regular_file')
        if info.st_size > MAX_BYTES:
            raise ValueError('size_limit')
        data = source.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('size_limit')
    return data


def inspect_itunesdb(data):
    if len(data) < 20 or data[:4] != b'mhbd':
        return dict(status='invalid_header')
    header, total = struct.unpack_from('<II', data, 4)
    if header < 20 or header > total or total != len(data):
        return dict(status='invalid_bounds')
    # Compressed CDB is deliberately not decompressed or interpreted.
    if len(data) >= 0xac and header >= 0xac and struct.unpack_from('<H', data, 0xa8)[0]:
        return dict(status='unsupported_compressed', header_bytes=header)
    sections, position = [], header
    while position < total:
        if len(sections) >= 1024:
            return dict(status='section_limit')
        if position + 16 > total or data[position:position + 4] != b'mhsd':
            return dict(status='invalid_section_header')
        length, size, kind = struct.unpack_from('<III', data, position + 4)
        if length < 16 or size < length or position + size > total:
            return dict(status='invalid_section_bounds')
        section = dict(type=kind, header_bytes=length, total_bytes=size)
        if kind == 9:
            section.update(cuid_present=size > length, cuid_bytes=size - length,
                           expected_cuid_length=size - length == 32)
        sections.append(section)
        position += size
    return dict(status='structure_valid', header_bytes=header,
                database_version=struct.unpack_from('<I', data, 16)[0], sections=sections,
                nested_records_validated=False, checksum_validated=False)


def _unsigned(value):
    if not isinstance(value, int):
        raise ValueError('invalid_id_type')
    return value & ((1 << 64) - 1)


def inspect_sqlite(data):
    connection = sqlite3.connect(':memory:')
    try:
        connection.deserialize(data)
        connection.execute('PRAGMA query_only=ON')
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_BYTES)
        deadline = time.monotonic() + 3
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = dict(status='sqlite_readable', user_version=connection.execute('PRAGMA user_version').fetchone()[0],
                      schema_version=connection.execute('PRAGMA schema_version').fetchone()[0],
                      table_count=len(tables), unknown_table_count=len(tables - set(KNOWN_TABLES)),
                      tables={})
        for name in KNOWN_TABLES:
            if name in tables:
                result['tables'][name] = connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        if {'genius_metadata', 'genius_similarities'} <= tables:
            if result['tables']['genius_metadata'] > 100000 or result['tables']['genius_similarities'] > 100000:
                result['relations'] = dict(status='row_limit', complete=False, ipod_profile_verified=False)
                return result
            metadata = {_unsigned(row[0]) for row in connection.execute('SELECT genius_id FROM genius_metadata')}
            edges = dangling = missing_seeds = unsupported = 0
            for seed, version, blob in connection.execute('SELECT genius_id,version,data FROM genius_similarities'):
                if time.monotonic() > deadline:
                    raise ValueError('time_limit')
                missing_seeds += _unsigned(seed) not in metadata
                if version != 1 or not isinstance(blob, bytes) or len(blob) < 12:
                    unsupported += 1
                    continue
                compression, size, count = struct.unpack_from('<III', blob)
                if compression != 0 or size != len(blob) - 8 or count != (len(blob) - 12) // 8 or len(blob) != 12 + count * 8:
                    unsupported += 1
                    continue
                for (target,) in struct.iter_unpack('<Q', memoryview(blob)[12:]):
                    edges += 1
                    if edges > 2000000 or (edges % 1000 == 0 and time.monotonic() > deadline):
                        raise ValueError('relation_limit')
                    dangling += target not in metadata
            result['relations'] = dict(envelope_profile='observed_music_v1_uncompressed',
                                       examined_edges=edges, dangling_edges=dangling,
                                       seeds_missing_metadata=missing_seeds, unsupported_rows=unsupported,
                                       complete=unsupported == 0, ipod_profile_verified=False)
        return result
    except (sqlite3.Error, ValueError, TypeError, OverflowError):
        return dict(status='sqlite_invalid_or_unsupported')
    finally:
        connection.close()


def inspect_file(path):
    try:
        data = read_bounded(path)
    except (OSError, ValueError) as error:
        return dict(status=str(error) if isinstance(error, ValueError) else 'unreadable')
    result = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    if Path(path).name == 'iTunesCDB':
        result.update(format='itunes_cdb', status='unsupported_compressed')
    elif data.startswith(b'mhbd'):
        result.update(format='itunes_db', **inspect_itunesdb(data))
    elif data.startswith(b'SQLite format 3\0'):
        result.update(format='sqlite', **inspect_sqlite(data))
    elif len(data) >= 4096 and len(data) % 4096 == 0 and data[16:24] == bytes.fromhex('100001010c402020'):
        result.update(format='possible_music_encrypted_sqlite', status='unsupported_encryption',
                      header_profile_only=True)
    else:
        result.update(format='unknown', status='unsupported')
    return result


def inspect(path):
    path = Path(os.path.abspath(path))
    _no_symlinks(path)
    if path.is_file():
        files = {'input_file': inspect_file(path)}
    elif path.is_dir():
        directory = path / 'iPod_Control' / 'iTunes' if (path / 'iPod_Control').exists() else path
        _no_symlinks(directory)
        files = {}
        for name in KNOWN_FILES:
            candidate = directory / name
            if candidate.exists() or candidate.is_symlink():
                files[name] = inspect_file(candidate)
        # SQLite-library directories on some devices; only fixed known files.
        for folder in ('iTunes Library.itlp', 'iTunes Library'):
            subdirectory = directory / folder
            if subdirectory.exists() or subdirectory.is_symlink():
                _no_symlinks(subdirectory)
                for name in KNOWN_FILES:
                    candidate = subdirectory / name
                    if candidate.exists() or candidate.is_symlink():
                        files[folder + '/' + name] = inspect_file(candidate)
    else:
        raise ValueError('input_missing')
    return dict(report_version=1, max_file_bytes=MAX_BYTES, files=files,
                read_only=True, ipod_acceptance_verified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.output:
            _no_symlinks(args.output.absolute())
            source = args.input.resolve()
            target = args.output.resolve()
            if target == source or (source.is_dir() and target.is_relative_to(source)) or args.output.exists():
                raise ValueError('output_must_be_new_and_outside_input')
        report = inspect(args.input)
        encoded = json.dumps(report, indent=2) + '\n'
        if args.output:
            with args.output.open('x') as destination:
                destination.write(encoded)
        else:
            print(encoded, end='')
    except (ValueError, OSError):
        parser.exit(2, 'Input/output rejected; no source data changed.\n')


if __name__ == '__main__':
    main()
