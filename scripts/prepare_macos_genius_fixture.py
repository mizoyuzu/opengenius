"""Prepare a bounded synthetic-tone fixture for native Music reload, never a personal Library."""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import struct

from decrypt_genius import derive_key, decrypt_pages, extract_key_header, validate_database
from emulate_genius import MusicCore
from genius_format import pack_config, pack_similarities
from inspect_music_library import decode_musicdb, parse_tracks
from rewrite_genius import encrypt_pages, table_snapshot
from rewrite_music_ids import encode_library, patch_ids


def prepare(bundle, executable):
    files = {name: (bundle / name).read_bytes() for name in
             ('Library.musicdb', 'Genius.itdb', 'Library Preferences.musicdb')}
    source = files['Library.musicdb']
    expanded = decode_musicdb(source)
    tracks, _ = parse_tracks(expanded)
    if (not 2 <= len(tracks) <= 128 or {t.get('title') for t in tracks} != {f'synthetic-tone-{i}' for i in range(1,len(tracks)+1)}
            or any(t.get('duration_ms') != 3000 or t.get('genius_id') != '0000000000000000' for t in tracks)):
        raise ValueError('Only a 2..128 sequential synthetic-tone zero-ID Library is supported')
    if encode_library(expanded, source) != source:
        raise ValueError('Source Library encoding is not reproducible')
    assignments = [{'persistent_id': t['persistent_id'], 'genius_id': f'{0x70000001+i:016X}'}
                   for i, t in enumerate(sorted(tracks, key=lambda t: t['persistent_id']))]
    patched, changes = patch_ids(expanded, assignments)
    files['Library.musicdb'] = encode_library(patched, source)
    key, _ = derive_key(executable, extract_key_header(files['Library Preferences.musicdb']))
    clear = decrypt_pages(files['Genius.itdb'], key)
    validate_database(clear)
    # Test-only relation graph: two generated tones link to each other.
    identifiers = [int(a['genius_id'], 16) for a in assignments]
    metadata = {gid: struct.pack('<4Q', 0, 1, 1, index + 1) for index, gid in enumerate(identifiers)}
    relations = {gid: pack_similarities(0, [identifiers[(i + step) % len(identifiers)]
                  for step in range(1, min(32, len(identifiers)-1) + 1)]) for i, gid in enumerate(identifiers)}
    config = pack_config({'version': 2, 'filters': [{'type': 1, 'parameters': [20, 50, 10, 10]}],
                          'flags': 0, 'result_words': [1, 25]})
    with sqlite3.connect(':memory:') as c:
        c.deserialize(clear)
        before = table_snapshot(c)
        if any(before.values()):
            raise ValueError('Require an entirely empty Genius template')
        schema = c.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        for table, rows in (('genius_metadata', metadata), ('genius_similarities', relations)):
            c.executemany(f'INSERT INTO {table}(genius_id,version,data) VALUES(?,1,?)', rows.items())
        c.execute('INSERT INTO genius_config(id,version,default_num_results,min_num_results,data) VALUES(1,2,25,1,?)', (config,))
        if c.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall() != schema:
            raise ValueError('Schema changed')
        c.commit()
        rewritten = c.serialize()
        expected = table_snapshot(c)
    counts = validate_database(rewritten)
    encrypted = encrypt_pages(rewritten, key)
    with sqlite3.connect(':memory:') as c:
        c.deserialize(decrypt_pages(encrypted, key))
        if table_snapshot(c) != expected:
            raise ValueError('Encrypted fixture did not preserve rows')
    files['Genius.itdb'] = encrypted
    native = MusicCore(executable, config, metadata, relations).generate(identifiers[0], 25)
    selected = [int(g, 16) for g in native['result_genius_ids']]
    if not (2 <= len(selected) <= min(25, len(identifiers)) and len(set(selected)) == len(selected)
            and identifiers[0] in selected and set(selected) <= set(identifiers)):
        raise ValueError('Music core did not select valid synthetic fixture relations')
    expected_tracks = [{**t, 'genius_id': next(a['genius_id'] for a in assignments if a['persistent_id'] == t['persistent_id'])}
                       for t in tracks]
    fixture = {'schema_version': 1, 'fixture_kind': 'synthetic-tone-only', 'app_template_version': '1.6.6',
               'files': {name: {'base64': base64.b64encode(blob).decode(), 'sha256': hashlib.sha256(blob).hexdigest()}
                         for name, blob in files.items()}, 'expected_tracks': expected_tracks}
    report = {'schema_version': 1, 'source_library_sha256': hashlib.sha256(source).hexdigest(),
              'source_genius_plaintext_sha256': hashlib.sha256(clear).hexdigest(), 'assignments': changes,
              'fixture_file_sha256': {name: row['sha256'] for name, row in fixture['files'].items()},
              'table_counts': counts, 'encryption_roundtrip_verified': True,
              'emulated_selection_genius_ids': native['result_genius_ids'],
              'track_count': len(tracks), 'relation_edges': sum(min(32,len(identifiers)-1) for _ in identifiers),
              'relation_source': 'Deliberate bounded ring graph, not observed music recommendations',
              'music_app_acceptance_verified': False, 'ipod_acceptance_verified': False}
    return fixture, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--output-directory', type=Path, required=True)
    args = parser.parse_args()
    output = args.output_directory.resolve()
    if output.exists() or output == args.bundle.resolve() or args.bundle.resolve().is_relative_to(output):
        parser.error('Output must be new and separate from inputs')
    fixture, report = prepare(args.bundle, args.executable)
    payload = base64.b64encode(gzip.compress(json.dumps(fixture, ensure_ascii=False).encode(), mtime=0)).decode()
    output.mkdir(parents=True)
    (output / 'fixture.json.gz').write_bytes(gzip.compress(json.dumps(fixture, ensure_ascii=False).encode(),mtime=0))
    documents = [('fixture.json', fixture), ('report.json', report)]
    if len(payload) <= 60000:
        documents.append(('dispatch.json', {'fixture': payload}))
    for name, doc in documents:
        (output / name).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'payload_characters': len(payload), 'table_counts': report['table_counts']}))


if __name__ == '__main__':
    main()
