#!/usr/bin/env python3
"""Rebase observed real-song Genius relations onto a fresh native Music copy."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from build_real_library_experiment import initialize_empty_database
from decrypt_genius import derive_key, decrypt_pages, extract_key_header, validate_database
from genius_format import pack_similarities, parse_similarities, unsigned_id
from inspect_music_library import decode_musicdb, parse_tracks
from rewrite_genius import encrypt_pages, table_snapshot
from rewrite_music_ids import encode_library, patch_ids

CORE = ('Library.musicdb', 'Genius.itdb', 'Library Preferences.musicdb')
SUPPORT = ('Application.musicdb', 'Extras.itdb', 'Preferences.plist', 'sentinel')


def prepare(native, mapping_path, original, input_manifest, executable, output):
    native, original, output = Path(native).resolve(), Path(original).resolve(), Path(output).resolve()
    if output.exists() or any(output.is_relative_to(p) or p.is_relative_to(output) for p in (native, original)):
        raise ValueError('output must be new and separate')
    mapping = json.loads(Path(mapping_path).read_text())
    manifest = json.loads(Path(input_manifest).read_text())
    imported = mapping['tracks']
    if not mapping['all_selected_media_imported'] or any(t['status'] != 'imported' for t in imported):
        raise ValueError('require a complete verified native import')
    by_original = {t['original_pid']: t for t in imported}
    if len(by_original) != len(imported) or set(by_original) != {t['persistent_id'] for t in manifest['tracks']}:
        raise ValueError('native mapping differs from selected media')
    blobs = {name: (native / name).read_bytes() for name in CORE}
    # Verify these are the files actually collected from native Music.
    native_report = json.loads((Path(mapping_path).parent / 'report.json').read_text())
    if not native_report.get('after_copy_consistent_exit'):
        raise ValueError('require verified native exit')
    recorded = {e['path']: e['sha256'] for e in native_report['after_files']}
    if any(hashlib.sha256(blob).hexdigest() != recorded[name] for name, blob in blobs.items()):
        raise ValueError('native snapshot hash mismatch')
    fresh_tracks, _ = parse_tracks(decode_musicdb(blobs['Library.musicdb']))
    native_ids = {t['persistent_id'] for t in imported}
    if len(native_ids) != len(imported) or native_ids != {t['persistent_id'] for t in fresh_tracks}:
        raise ValueError('native Persistent IDs differ from mapping')
    original_tracks, _ = parse_tracks(decode_musicdb((original / 'Library.musicdb').read_bytes()))
    original_ids = {t['persistent_id']: int(t['genius_id'], 16) for t in original_tracks}
    assignments = [{'persistent_id': t['persistent_id'], 'genius_id': f"{original_ids[t['original_pid']]:016X}"} for t in imported]
    wanted = {int(t['genius_id'], 16) for t in assignments}
    if 0 in wanted or len(wanted) != len(imported):
        raise ValueError('require unique assigned observed Genius IDs')
    old_key, _ = derive_key(executable, extract_key_header((original / 'Library Preferences.musicdb').read_bytes()))
    old_clear = decrypt_pages((original / 'Genius.itdb').read_bytes(), old_key)
    with sqlite3.connect(':memory:') as connection:
        connection.deserialize(old_clear)
        metadata = {unsigned_id(i): bytes(b) for i, version, b in connection.execute('SELECT * FROM genius_metadata') if unsigned_id(i) in wanted and version == 1}
        relations = {}
        edge_count = 0
        for i, version, b in connection.execute('SELECT * FROM genius_similarities'):
            if unsigned_id(i) in wanted:
                if version != 1:
                    raise ValueError('unsupported similarity version')
                _, targets = parse_similarities(b)
                selected = [t for t in targets if t in wanted]
                relations[unsigned_id(i)] = pack_similarities(0, selected)
                edge_count += len(selected)
        config, = connection.execute('SELECT data FROM genius_config WHERE id=1 AND version=2').fetchone()
    if set(metadata) != wanted or set(relations) != wanted:
        raise ValueError('missing observed Genius rows')
    new_key, _ = derive_key(executable, extract_key_header(blobs['Library Preferences.musicdb']))
    clear = decrypt_pages(blobs['Genius.itdb'], new_key)
    new_clear, expected = initialize_empty_database(clear, bytes(config), metadata, relations, 25)
    encrypted = encrypt_pages(new_clear, new_key)
    recovered = decrypt_pages(encrypted, new_key)
    validate_database(recovered)
    with sqlite3.connect(':memory:') as connection:
        connection.deserialize(recovered)
        if table_snapshot(connection) != expected:
            raise ValueError('encrypted dataset did not roundtrip')
    patched, changes = patch_ids(decode_musicdb(blobs['Library.musicdb']), assignments)
    library = encode_library(patched, blobs['Library.musicdb'])
    output.mkdir(parents=True, mode=0o700)
    bundle = output / 'Music Library.musiclibrary'; bundle.mkdir()
    for name in CORE + SUPPORT:
        if (native / name).is_file() and not (native / name).is_symlink():
            shutil.copyfile(native / name, bundle / name)
    (bundle / 'Library.musicdb').write_bytes(library)
    (bundle / 'Genius.itdb').write_bytes(encrypted)
    rebased_manifest = {'schema_version': 1, 'expected_library_track_count': len(imported),
                        'tracks': [{'persistent_id': t['persistent_id'], 'title': t['title'], 'relative_media_path': t['relative_media_path']} for t in imported],
                        'seeds': [by_original[p]['persistent_id'] for p in manifest['seeds']]}
    (output / 'manifest.json').write_text(json.dumps(rebased_manifest, ensure_ascii=False))
    summary = {'native_tracks': len(imported), 'genius_ids_patched': len(changes), 'observed_edges_retained': edge_count,
               'account_preferences_changed': False, 'native_reload_verified': False}
    (output / 'preparation-report.json').write_text(json.dumps(summary, indent=2))
    return summary



def prepare_full(native, original, input_manifest, executable, output):
    """Keep all native PIDs while adding the original observed Genius dataset."""
    native, original, output = Path(native).resolve(), Path(original).resolve(), Path(output).resolve()
    if output.exists() or any(output.is_relative_to(p) or p.is_relative_to(output) for p in (native, original)):
        raise ValueError('output must be new and separate')
    report = json.loads((native.parent / 'report.json').read_text())
    if not report.get('after_copy_consistent_exit') or report.get('status') != 'library_loaded':
        raise ValueError('require a verified native load and exit')
    blobs = {name: (native / name).read_bytes() for name in CORE}
    recorded = {entry['path']: entry['sha256'] for entry in report['after_files']}
    if any(hashlib.sha256(blob).hexdigest() != recorded[name] for name, blob in blobs.items()):
        raise ValueError('native snapshot hash mismatch')
    native_tracks, _ = parse_tracks(decode_musicdb(blobs['Library.musicdb']))
    old_tracks, _ = parse_tracks(decode_musicdb((original / 'Library.musicdb').read_bytes()))
    if {t['persistent_id'] for t in native_tracks} != {t['persistent_id'] for t in old_tracks}:
        raise ValueError('full native Persistent IDs differ from original')
    if any(int(t['genius_id'], 16) for t in native_tracks):
        raise ValueError('native source already has Genius IDs')
    assignments = [{'persistent_id': t['persistent_id'], 'genius_id': t['genius_id']}
                   for t in old_tracks if int(t['genius_id'], 16)]
    wanted = {int(t['genius_id'], 16) for t in assignments}
    old_key, _ = derive_key(executable, extract_key_header((original / 'Library Preferences.musicdb').read_bytes()))
    old_clear = decrypt_pages((original / 'Genius.itdb').read_bytes(), old_key)
    with sqlite3.connect(':memory:') as connection:
        connection.deserialize(old_clear)
        metadata = {unsigned_id(i): bytes(blob) for i, version, blob in connection.execute('SELECT * FROM genius_metadata') if version == 1}
        relations = {unsigned_id(i): bytes(blob) for i, version, blob in connection.execute('SELECT * FROM genius_similarities') if version == 1}
        config, = connection.execute('SELECT data FROM genius_config WHERE id=1 AND version=2').fetchone()
    if set(metadata) != wanted or set(relations) != wanted:
        raise ValueError('observed Genius rows differ from Library assignments')
    key, _ = derive_key(executable, extract_key_header(blobs['Library Preferences.musicdb']))
    clear = decrypt_pages(blobs['Genius.itdb'], key)
    new_clear, expected = initialize_empty_database(clear, bytes(config), metadata, relations, 25)
    encrypted = encrypt_pages(new_clear, key)
    recovered = decrypt_pages(encrypted, key)
    validate_database(recovered)
    with sqlite3.connect(':memory:') as connection:
        connection.deserialize(recovered)
        if table_snapshot(connection) != expected:
            raise ValueError('encrypted dataset did not roundtrip')
    patched, changes = patch_ids(decode_musicdb(blobs['Library.musicdb']), assignments)
    library = encode_library(patched, blobs['Library.musicdb'])
    manifest = json.loads(Path(input_manifest).read_text())
    if manifest['expected_library_track_count'] != len(native_tracks):
        raise ValueError('full native Library count differs from manifest')
    by_pid = {t['persistent_id']: t for t in native_tracks}
    if any(t['persistent_id'] not in by_pid or by_pid[t['persistent_id']].get('title') != t['title'] for t in manifest['tracks']):
        raise ValueError('selected native tracks differ from manifest')
    output.mkdir(parents=True, mode=0o700)
    bundle = output / 'Music Library.musiclibrary'; bundle.mkdir()
    for name in CORE + SUPPORT:
        if (native / name).is_file() and not (native / name).is_symlink():
            shutil.copyfile(native / name, bundle / name)
    (bundle / 'Library.musicdb').write_bytes(library)
    (bundle / 'Genius.itdb').write_bytes(encrypted)
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False))
    summary = {'native_tracks': len(native_tracks), 'genius_ids_patched': len(changes),
               'observed_edges_retained': sum(len(parse_similarities(blob)[1]) for blob in relations.values()),
               'account_preferences_changed': False, 'native_reload_verified': False}
    (output / 'preparation-report.json').write_text(json.dumps(summary, indent=2))
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('native', 'original', 'manifest', 'executable', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--mapping', type=Path)
    p.add_argument('--full-library', action='store_true')
    a = p.parse_args()
    if a.full_library:
        result = prepare_full(a.native, a.original, a.manifest, a.executable, a.output)
    else:
        if not a.mapping:
            p.error('--mapping is required for imported subset rebasing')
        result = prepare(a.native, a.mapping, a.original, a.manifest, a.executable, a.output)
    print(json.dumps(result))

if __name__ == '__main__':
    main()
