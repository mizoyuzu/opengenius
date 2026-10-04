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
MAX_NESTED_RECORDS = 250_000
MAX_PLAYLIST_MEMBERS = 1_000_000
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
        sections.append((section, position, position + size))
        position += size

    # Parse tracks first so playlist references can be checked regardless of
    # section ordering. Only the record layouts read by libgpod are interpreted.
    track_ids = set()
    dbids, dbid2s = set(), set()
    dbid_nonzero_records = dbid2_nonzero_records = dbid2_available_records = 0
    dbid_equal_dbid2_nonzero_records = track_record_count = 0
    track_sections = []
    for section, start, end in sections:
        if section['type'] == 1:
            nested = _parse_track_section(data, start, end, section['header_bytes'])
            section['nested_records'] = nested
            if nested['status'] == 'invalid':
                return dict(status='invalid_nested_records', error=nested['error'],
                            header_bytes=header, sections=[item[0] for item in sections],
                            nested_records_validated=False, checksum_validated=False)
            if nested['status'] in ('validated', 'validated_with_unparsed_trailing_bytes'):
                track_ids.update(nested.pop('_track_ids'))
                dbids.update(nested.pop('_dbids'))
                dbid2s.update(nested.pop('_dbid2s'))
                dbid_nonzero_records += nested['dbid_nonzero_records']
                dbid2_nonzero_records += nested['dbid2_nonzero_records']
                dbid2_available_records += nested['dbid2_available_records']
                dbid_equal_dbid2_nonzero_records += nested['dbid_equal_dbid2_nonzero_records']
                track_record_count += nested['track_records']
                track_sections.append(nested)

    playlist_sections = []
    referenced_track_ids = set()
    for section, start, end in sections:
        if section['type'] in (2, 3):
            nested = _parse_playlist_section(data, start, end, section['header_bytes'], track_ids)
            section['nested_records'] = nested
            if nested['status'] == 'invalid':
                return dict(status='invalid_nested_records', error=nested['error'],
                            header_bytes=header, sections=[item[0] for item in sections],
                            nested_records_validated=False, checksum_validated=False)
            if nested['status'] in ('validated', 'validated_with_unparsed_trailing_bytes'):
                referenced_track_ids.update(nested.pop('_referenced_ids'))
                playlist_sections.append(nested)

    known_nested = [item for item, _, _ in sections if item['type'] in (1, 2, 3)]
    nested_validated = bool(known_nested) and all(
        item.get('nested_records', {}).get('status') == 'validated' for item in known_nested)
    return dict(status='structure_valid', header_bytes=header,
                database_version=struct.unpack_from('<I', data, 16)[0],
                sections=[item[0] for item in sections],
                nested_records_validated=nested_validated,
                nested_coverage='libgpod_track_and_playlist_records',
                track_records=track_record_count,
                track_ids_unique=len(track_ids),
                track_ids_duplicate_records=track_record_count - len(track_ids),
                dbid_nonzero_records=dbid_nonzero_records,
                dbid_unique_values=len(dbids),
                dbid_duplicate_records=dbid_nonzero_records - len(dbids),
                dbid2_available_records=dbid2_available_records,
                dbid2_nonzero_records=dbid2_nonzero_records,
                dbid2_unique_values=len(dbid2s),
                dbid2_duplicate_records=dbid2_nonzero_records - len(dbid2s),
                dbid_equal_dbid2_nonzero_records=dbid_equal_dbid2_nonzero_records,
                playlists=sum(item['playlist_records'] for item in playlist_sections),
                playlist_members=sum(item['membership_entries'] for item in playlist_sections),
                playlist_unique_track_ids_referenced=len(referenced_track_ids),
                dangling_playlist_members=sum(item['dangling_members'] for item in playlist_sections),
                checksum_validated=False)


def _bounded_chunk(data, offset, limit, magic, min_header):
    if offset < 0 or limit - offset < 12 or data[offset:offset + 4] != magic:
        raise ValueError('missing_' + magic.decode('ascii'))
    header, total = struct.unpack_from('<II', data, offset + 4)
    if header < min_header or total < header or offset + total > limit:
        raise ValueError('invalid_' + magic.decode('ascii') + '_bounds')
    return header, total


def _count_header(data, offset, limit, magic):
    if offset < 0 or limit - offset < 12 or data[offset:offset + 4] != magic:
        raise ValueError('missing_' + magic.decode('ascii'))
    header = struct.unpack_from('<I', data, offset + 4)[0]
    if header < 12 or offset + header > limit:
        raise ValueError('invalid_' + magic.decode('ascii') + '_bounds')
    return header


def _scan_mhods(data, offset, limit, count):
    if count > MAX_NESTED_RECORDS:
        raise ValueError('mhod_limit')
    position = offset
    for _ in range(count):
        header, total = _bounded_chunk(data, position, limit, b'mhod', 16)
        position += total
    return position


def _parse_track_section(data, start, end, section_header):
    payload = start + section_header
    try:
        header = _count_header(data, payload, end, b'mhlt')
        count = struct.unpack_from('<I', data, payload + 8)[0]
        if count > MAX_NESTED_RECORDS:
            raise ValueError('track_limit')
        position = payload + header
        track_ids, dbids, dbid2s = set(), set(), set()
        dbid_nonzero = dbid2_nonzero = dbid2_available = dbid_equal = records = 0
        for _ in range(count):
            header, total = _bounded_chunk(data, position, end, b'mhit', 0x9c)
            record_end = position + total
            track_id = struct.unpack_from('<I', data, position + 16)[0]
            dbid = struct.unpack_from('<Q', data, position + 112)[0]
            child_count = struct.unpack_from('<I', data, position + 12)[0]
            child_end = _scan_mhods(data, position + header, record_end, child_count)
            if child_end != record_end:
                raise ValueError('mhit_child_size_mismatch')
            track_ids.add(track_id)
            if dbid:
                dbid_nonzero += 1
                dbids.add(dbid)
            if header >= 0xf4:
                dbid2_available += 1
                dbid2 = struct.unpack_from('<Q', data, position + 168)[0]
                if dbid2:
                    dbid2_nonzero += 1
                    dbid2s.add(dbid2)
                dbid_equal += bool(dbid and dbid == dbid2)
            records += 1
            position = record_end
        trailing = end - position
        return dict(status='validated' if trailing == 0 else 'validated_with_unparsed_trailing_bytes',
                    unparsed_trailing_bytes=trailing, track_records=records,
                    dbid_nonzero_records=dbid_nonzero, dbid_unique_values=len(dbids),
                    dbid_duplicate_records=dbid_nonzero - len(dbids),
                    dbid2_available_records=dbid2_available,
                    dbid2_nonzero_records=dbid2_nonzero, dbid2_unique_values=len(dbid2s),
                    dbid2_duplicate_records=dbid2_nonzero - len(dbid2s),
                    dbid_equal_dbid2_nonzero_records=dbid_equal, _track_ids=track_ids,
                    _dbids=dbids, _dbid2s=dbid2s)
    except ValueError as error:
        if str(error).startswith(('missing_mhlt',)):
            return dict(status='unsupported_nested_layout')
        return dict(status='invalid', error=str(error))


def _parse_playlist_section(data, start, end, section_header, track_ids):
    payload = start + section_header
    try:
        header = _count_header(data, payload, end, b'mhlp')
        count = struct.unpack_from('<I', data, payload + 8)[0]
        if count > MAX_NESTED_RECORDS:
            raise ValueError('playlist_limit')
        position = payload + header
        memberships = dangling = playlists = 0
        referenced = set()
        for _ in range(count):
            playlist_header, playlist_total = _bounded_chunk(data, position, end, b'mhyp', 48)
            playlist_end = position + playlist_total
            children = struct.unpack_from('<I', data, position + 12)[0]
            member_count = struct.unpack_from('<I', data, position + 16)[0]
            if memberships + member_count > MAX_PLAYLIST_MEMBERS:
                raise ValueError('playlist_member_limit')
            cursor = _scan_mhods(data, position + playlist_header, playlist_end, children)
            for _ in range(member_count):
                member_header, member_total = _bounded_chunk(data, cursor, playlist_end, b'mhip', 36)
                member_end = cursor + member_total
                child_count = struct.unpack_from('<I', data, cursor + 12)[0]
                track_id = struct.unpack_from('<I', data, cursor + 24)[0]
                child_end = _scan_mhods(data, cursor + member_header, playlist_end, child_count)
                # Older iTunes wrote mhip.total == mhip.header even when child
                # MHODs follow. libgpod advances using those child lengths.
                if member_total == member_header and child_count:
                    cursor = child_end
                else:
                    if child_end > member_end:
                        raise ValueError('mhip_child_size_mismatch')
                    cursor = member_end
                memberships += 1
                dangling += track_id not in track_ids
                referenced.add(track_id)
            if cursor != playlist_end:
                raise ValueError('mhyp_child_size_mismatch')
            playlists += 1
            position = playlist_end
        trailing = end - position
        return dict(status='validated' if trailing == 0 else 'validated_with_unparsed_trailing_bytes',
                    unparsed_trailing_bytes=trailing, playlist_records=playlists,
                    membership_entries=memberships, dangling_members=dangling,
                    _referenced_ids=referenced)
    except ValueError as error:
        if str(error).startswith(('missing_mhlp',)):
            return dict(status='unsupported_nested_layout')
        return dict(status='invalid', error=str(error))


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
