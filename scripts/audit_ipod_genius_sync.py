"""Read-only comparison of the observed Music/iPod Genius track profiles.

On the observed Music 1.5.6.11 / iTunesDB 117 profiles, changing itma+0x68
from 0 to 1 caused native sync to copy the seed's Genius rows and store 1 at
mhit+0x1f0. Device Genius IDs at mhit+0x1e4 matched all 286 host IDs.
These field observations do not establish on-device Genius generation.
Only aggregate counts are printed; identifiers and database contents stay local.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import struct
import zlib

MAX_BYTES = 32 * 1024 * 1024
MAX_TRACKS = 100000


def read_file(path):
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError('unsupported input')
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError('input too large')
    return data


def word(data, offset):
    if offset < 0 or offset + 4 > len(data):
        raise ValueError('truncated field')
    return struct.unpack_from('<I', data, offset)[0]


def decode_host(data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    if len(data) < 160 or data[:4] != b'hfma' or word(data, 4) != 160 or word(data, 8) != len(data):
        raise ValueError('unsupported host database')
    payload = data[160:]
    size = min(word(data, 84), len(payload)) // 16 * 16
    decoder = Cipher(algorithms.AES(b'BHUILuilfghuila3'), modes.ECB()).decryptor()
    compressed = decoder.update(payload[:size]) + decoder.finalize() + payload[size:]
    inflater = zlib.decompressobj()
    clear = inflater.decompress(compressed, 64 * 1024 * 1024 + 1)
    if len(clear) > 64 * 1024 * 1024 or not inflater.eof:
        raise ValueError('invalid host compression')
    return clear


def record(data, offset, limit, magic, minimum):
    if offset + 12 > limit or data[offset:offset + 4] != magic:
        raise ValueError('missing record')
    header, total = word(data, offset + 4), word(data, offset + 8)
    if header < minimum or total < header or offset + total > limit:
        raise ValueError('invalid record bounds')
    return header, total


def host_tracks(clear):
    start = clear.find(b'ltma')
    if start < 0:
        raise ValueError('missing host track list')
    header, count = word(clear, start + 4), word(clear, start + 8)
    if header < 12 or count > MAX_TRACKS or start + header > len(clear):
        raise ValueError('invalid host track list')
    cursor, tracks = start + header, {}
    for _ in range(count):
        header, total = record(clear, cursor, len(clear), b'itma', 376)
        if header != 376:
            raise ValueError('unsupported host track profile')
        pid = struct.unpack_from('<Q', clear, cursor + 16)[0]
        if not pid or pid in tracks:
            raise ValueError('invalid host track identity')
        tracks[pid] = (word(clear, cursor + 0xcc), word(clear, cursor + 0x68))
        cursor += total
    return tracks


def device_tracks(data):
    header, total = record(data, 0, len(data), b'mhbd', 20)
    if total != len(data):
        raise ValueError('invalid device database length')
    if header >= 0xac and struct.unpack_from('<H', data, 0xa8)[0]:
        raise ValueError('compressed device database unsupported')
    cursor, tracks, found = header, {}, False
    while cursor < len(data):
        sh, size = record(data, cursor, len(data), b'mhsd', 16)
        end = cursor + size
        if word(data, cursor + 12) == 1:
            if found:
                raise ValueError('duplicate device track list')
            found = True
            pos = cursor + sh
            if pos + 12 > end or data[pos:pos + 4] != b'mhlt':
                raise ValueError('missing device track list')
            lh, count = word(data, pos + 4), word(data, pos + 8)
            if lh < 12 or count > MAX_TRACKS or pos + lh > end:
                raise ValueError('invalid device track list')
            pos += lh
            for _ in range(count):
                rh, rs = record(data, pos, end, b'mhit', 624)
                if rh != 624:
                    raise ValueError('unsupported device track profile')
                pid = struct.unpack_from('<Q', data, pos + 0x70)[0]
                if not pid or pid in tracks:
                    raise ValueError('invalid device track identity')
                tracks[pid] = (struct.unpack_from('<Q', data, pos + 0x1e4)[0],
                               word(data, pos + 0x1f0))
                pos += rs
            if pos != end:
                raise ValueError('unparsed device track data')
        cursor = end
    if not found:
        raise ValueError('missing device track section')
    return tracks


def compare(host, device, *, checksum_mapping_verified=False):
    genius = {pid: row for pid, row in host.items() if row[0]}
    return {
        'host_tracks': len(host), 'device_tracks': len(device),
        'matched_track_pids': len(host.keys() & device.keys()),
        'host_genius_tracks': len(genius),
        'host_genius_tracks_missing_on_device': sum(pid not in device for pid in genius),
        'matched_nonzero_genius_ids': sum(pid in device and device[pid][0] == row[0]
                                        for pid, row in genius.items()),
        'mismatched_genius_ids': sum(device[pid][0] != row[0] for pid, row in host.items() if pid in device),
        'host_genius_tracks_with_zero_checksum': sum(row[1] == 0 for row in genius.values()),
        'host_genius_tracks_with_nonzero_checksum': sum(row[1] != 0 for row in genius.values()),
        'mismatched_genius_checksums': sum(device[pid][1] != row[1]
                                          for pid, row in genius.items() if pid in device),
        'host_checksum_offset': 'itma+0x68',
        'device_checksum_offset': 'mhit+0x1f0',
        'checksum_field_native_verified': checksum_mapping_verified,
        'read_only': True, 'firmware_genius_generation_verified': False,
    }


def extras_counts(data):
    if not data.startswith(b'SQLite format 3\0'):
        raise ValueError('unsupported Extras format')
    connection = sqlite3.connect(':memory:')
    try:
        connection.deserialize(data)
        connection.execute('PRAGMA query_only=ON')
        return {name: connection.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                for name in ('genius_metadata', 'genius_similarities', 'genius_config')}
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('host_bundle', type=Path)
    parser.add_argument('device_snapshot', type=Path)
    args = parser.parse_args()
    try:
        host_data = read_file(args.host_bundle / 'Library.musicdb')
        device_data = read_file(args.device_snapshot / 'iTunesDB')
        host = host_tracks(decode_host(host_data))
        device = device_tracks(device_data)
        verified = (host_data[16:48].split(b'\0', 1)[0] == b'1.5.6.11'
                    and word(device_data, 16) == 117)
        report = compare(host, device, checksum_mapping_verified=verified)
        report['device_extras_rows'] = extras_counts(read_file(args.device_snapshot / 'Extras.itdb'))
        print(json.dumps(report, indent=2))
    except (ValueError, OSError, sqlite3.Error, struct.error, ImportError):
        parser.exit(2, 'Unsupported or unreadable input; no database was modified.\n')


if __name__ == '__main__':
    main()
