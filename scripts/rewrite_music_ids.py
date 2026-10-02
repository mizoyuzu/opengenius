"""Patch new Genius IDs into an experimental Library.musicdb copy.

Only the observed 160-byte hfma / 376-byte itma profile is supported.
This does not create Genius metadata, validate an app load, or update iPods.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from genius_format import parse_id
from inspect_music_library import decode_musicdb, parse_tracks, u32


def encode_library(expanded, template):
    if (len(template) < 160 or template[:4] != b'hfma' or u32(template, 4) != 160
            or u32(template, 8) != len(template) or u32(template, 128) != len(template)
            or u32(template, 84) != 102400):
        raise ValueError('Unsupported Library hfma header profile')
    parse_tracks(expanded)
    compressed = zlib.compress(expanded, 1)
    length = 160 + len(compressed)
    if length > 0xFFFFFFFF:
        raise ValueError('Library exceeds uint32 length')
    header = bytearray(template[:160])
    struct.pack_into('<I', header, 8, length)
    struct.pack_into('<I', header, 128, length)
    encrypted_size = min(u32(header, 84), len(compressed)) // 16 * 16
    cipher = Cipher(algorithms.AES(b'BHUILuilfghuila3'), modes.ECB()).encryptor()
    payload = cipher.update(compressed[:encrypted_size]) + cipher.finalize() + compressed[encrypted_size:]
    encoded = bytes(header) + payload
    if decode_musicdb(encoded) != expanded:
        raise ValueError('Library did not round-trip')
    return encoded


def patch_ids(expanded, assignments):
    tracks, _ = parse_tracks(expanded)
    by_pid = {t['persistent_id']: t for t in tracks}
    if len(by_pid) != len(tracks):
        raise ValueError('Duplicate Persistent ID in source library')
    if any('genius_id' not in track for track in tracks):
        raise ValueError('Unsupported track header profile in source library')
    if not isinstance(assignments, list) or not assignments:
        raise ValueError('Expected nonempty ID assignments')
    occupied = {int(t['genius_id'], 16) for t in tracks if 'genius_id' in t} - {0}
    requested = {}
    assigned_ids = set()
    for assignment in assignments:
        if not isinstance(assignment, dict):
            raise ValueError('Invalid assignment')
        pid = f"{parse_id(assignment.get('persistent_id')):016X}"
        gid = parse_id(assignment.get('genius_id'))
        track = by_pid.get(pid)
        if pid in requested or not track or 'genius_id' not in track:
            raise ValueError('Duplicate, missing or unsupported track')
        if int(track['genius_id'], 16) != 0:
            raise ValueError('Refusing to replace an existing Genius ID')
        if not 0 < gid <= 0xFFFFFFFF or gid in occupied or gid in assigned_ids:
            raise ValueError('Invalid, duplicate or occupied Genius ID')
        requested[pid] = gid
        assigned_ids.add(gid)
    start = expanded.find(b'ltma')
    offset = start + u32(expanded, start + 4)
    result = bytearray(expanded)
    changes = []
    for track in tracks:
        if track['persistent_id'] in requested:
            gid = requested[track['persistent_id']]
            struct.pack_into('<I', result, offset + 0xCC, gid)
            changes.append(dict(persistent_id=track['persistent_id'], genius_id=f'{gid:016X}',
                                expanded_offset=offset + 0xCC))
        offset += u32(expanded, offset + 8)
    # Compare complete expanded bytes after restoring just the permitted slots.
    restored = bytearray(result)
    for change in changes:
        position = change['expanded_offset']
        restored[position:position + 4] = expanded[position:position + 4]
    if restored != expanded:
        raise ValueError('Unexpected change outside Genius ID slots')
    actual, _ = parse_tracks(result)
    for track in actual:
        before = by_pid[track['persistent_id']]
        expected = dict(before)
        if track['persistent_id'] in requested:
            expected['genius_id'] = f"{requested[track['persistent_id']]:016X}"
        if track != expected:
            raise ValueError('Unexpected track changes')
    return bytes(result), changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--assignments', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    bundle, output = args.bundle.resolve(), args.output.resolve()
    report_path = Path(str(output) + '.report.json')
    for path in [output, report_path]:
        if path.exists() or path.is_relative_to(bundle):
            parser.error('Output exists or is inside the source library')
    original = (bundle / 'Library.musicdb').read_bytes()
    source_hash = hashlib.sha256(original).hexdigest()
    manifest = json.loads(args.assignments.read_text())
    if manifest.get('schema_version') != 1 or manifest.get('source_library_sha256') != source_hash:
        parser.error('Assignment version/source hash mismatch')
    expanded = decode_musicdb(original)
    # This profile must also reproduce the unmodified source byte for byte.
    if encode_library(expanded, original) != original:
        parser.error('Source does not match the observed reproducible compression profile')
    patched, changes = patch_ids(expanded, manifest.get('assignments'))
    encoded = encode_library(patched, original)
    report = dict(schema_version=1, source_library_sha256=source_hash,
                  output_sha256=hashlib.sha256(encoded).hexdigest(), assignments=changes,
                  expanded_round_trip_verified=True, unchanged_source_round_trip_verified=True,
                  track_count=len(parse_tracks(patched)[0]),
                  header_changes_allowed=[8, 128], genius_database_generated=False,
                  music_app_acceptance_verified=False, ipod_acceptance_verified=False,
                  opaque_header_fields='preserved; validation semantics unknown')
    with output.open('xb') as file:
        file.write(encoded)
    with report_path.open('x') as file:
        json.dump(report, file, indent=2)
        file.write('\n')
    print(f'Wrote experimental Library copy; {len(changes)} new Genius IDs assigned')
    print('Genius DB metadata and Music/iPod acceptance are not verified')


if __name__ == '__main__':
    main()
