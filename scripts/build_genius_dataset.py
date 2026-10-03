"""Build an offline plaintext Genius prototype and a guarded Library ID plan."""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import struct

from decrypt_genius import decrypt_pages, validate_database
from emulate_genius import MusicCore, database_rows
from emulate_ytmusic_candidates import candidate_graph, controlled_configs, synthetic_rows
from genius_dataset import append_dataset
from genius_format import pack_similarities, parse_metadata, parse_similarities
from music_identity_map import IdentityMap, load_library
from rewrite_genius import encrypt_pages, table_snapshot


def allocate_rows(graph, matcher, reference_metadata, reference_similarities):
    """Use disjoint track IDs and group values, retaining graph order/equality."""
    temporary, metadata, similarities, _ = synthetic_rows(graph, matcher)
    tracks = {track['persistent_id']: track for track in matcher.tracks}
    occupied = set(reference_metadata) | set(reference_similarities)
    for track in matcher.tracks:
        value = track.get('genius_id')
        if not isinstance(value, str) or len(value) != 16:
            raise ValueError('Snapshot needs every track Genius ID')
        occupied.add(int(value, 16))
    ids, next_id = {}, 0x70000001
    for pid in sorted(temporary):
        if int(tracks[pid]['genius_id'], 16):
            raise ValueError('Selected track already has a Genius ID')
        while next_id in occupied:
            next_id += 1
        if next_id > 0xFFFFFFFF:
            raise ValueError('Genius uint32 ID range exhausted')
        ids[pid] = next_id
        occupied.add(next_id)
        next_id += 1
    remap = {temporary[pid]: ids[pid] for pid in ids}
    old_words = [parse_metadata(blob) for blob in reference_metadata.values()]
    words = {identifier: parse_metadata(blob) for identifier, blob in metadata.items()}
    groups = {}
    for index in (1, 2, 3):
        used = {row[index] for row in old_words}
        cursor = 1
        groups[index] = {}
        for value in sorted({row[index] for row in words.values()}):
            while cursor in used:
                cursor += 1
            groups[index][value] = cursor
            used.add(cursor)
            cursor += 1
    final_metadata = {remap[identifier]: struct.pack('<4Q', 0, *(groups[index][row[index]] for index in (1, 2, 3)))
                      for identifier, row in words.items()}
    final_similarities = {remap[identifier]: pack_similarities(0, [remap[target] for target in parse_similarities(blob)[1]])
                          for identifier, blob in similarities.items()}
    return ids, final_metadata, final_similarities


def build(reference, graph, matcher, executable, limit=25):
    if not 1 <= limit <= 100:
        raise ValueError('Limit must be 1..100')
    config, original_metadata, original_similarities = database_rows(reference)
    ids, metadata, similarities = allocate_rows(graph, matcher, original_metadata, original_similarities)
    controlled = controlled_configs(config)['without-compatible-genre']
    with sqlite3.connect(':memory:') as connection:
        connection.deserialize(reference)
        delta = append_dataset(connection, metadata, similarities, controlled)
        rewritten = connection.serialize()
        expected_tables = table_snapshot(connection)
    integrity = validate_database(rewritten)
    # SQLite reserves nonce space. Logical rows, rather than random reserved bytes,
    # are the roundtrip contract. Disposable key and ciphertext never leave RAM.
    key = secrets.token_bytes(16)
    recovered = decrypt_pages(encrypt_pages(rewritten, key), key)
    validate_database(recovered)
    with sqlite3.connect(':memory:') as connection:
        connection.deserialize(recovered)
        if table_snapshot(connection) != expected_tables:
            raise ValueError('AES codec roundtrip changed logical rows')
    persisted_config, persisted_metadata, persisted_similarities = database_rows(rewritten)
    root = ids[graph['root_pid']]
    expected = MusicCore(executable, controlled, metadata, similarities).generate(root, limit)
    actual = MusicCore(executable, persisted_config, persisted_metadata, persisted_similarities).generate(root, limit)
    if actual['result_genius_ids'] != expected['result_genius_ids']:
        raise ValueError('Serialized database changed Music core playlist')
    by_id = {identifier: pid for pid, identifier in ids.items()}
    playlist = [by_id[int(value, 16)] for value in actual['result_genius_ids']]
    return rewritten, ids, {'database_delta': delta, 'integrity': integrity,
                           'codec_selftest': 'AES-128 OFB logical roundtrip passed; disposable key discarded',
                           'selection': {**actual, 'playlist_pids': playlist},
                           'serialization_playlist_equivalence': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('track-snapshot', 'identity-map', 'observations', 'genius-reference', 'executable', 'output-directory'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--limit', type=int, default=25)
    args = parser.parse_args()
    inputs = {name: getattr(args, name).resolve() for name in
              ('track_snapshot', 'identity_map', 'observations', 'genius_reference', 'executable')}
    output = args.output_directory.resolve()
    if output.exists() or any(path == output or path.is_relative_to(output) for path in inputs.values()) or output.is_relative_to(inputs['executable'].parent):
        parser.error('Output directory must be new and outside inputs and executable directory')
    tracks, library_hash, provenance = load_library(track_snapshot=inputs['track_snapshot'])
    blobs = {name: path.read_bytes() for name, path in inputs.items() if name != 'executable'}
    matcher = IdentityMap(json.loads(blobs['identity_map']), tracks, library_hash)
    graph = candidate_graph(json.loads(blobs['observations']), matcher, library_hash)
    clear, ids, checks = build(blobs['genius_reference'], graph, matcher, inputs['executable'], args.limit)
    assignments = {'schema_version': 1, 'source_library_sha256': library_hash,
                   'assignments': [{'persistent_id': pid, 'genius_id': f'{identifier:016X}'} for pid, identifier in sorted(ids.items())]}
    report = {'schema_version': 1, 'library_input': provenance, 'input_sha256': {
        **{name: hashlib.sha256(blob).hexdigest() for name, blob in blobs.items()},
        'executable': hashlib.sha256(inputs['executable'].read_bytes()).hexdigest()},
        'root_pid': graph['root_pid'], 'root_genius_id': f"{ids[graph['root_pid']]:016X}",
        'dataset_tracks': len(ids), 'directed_relations': len(graph['ordered_target_pids']),
        'genius_plaintext_sha256': hashlib.sha256(clear).hexdigest(), **checks,
        'config_profile': 'without-compatible-genre', 'identity_status': 'unverified',
        'genre': 'zero placeholder; genre filter excluded', 'network_requests': 0,
        'library_written': False, 'target_encryption_pending': True,
        'auxiliary_tables_rebased': False, 'music_app_acceptance_verified': False,
        'ipod_acceptance_verified': False,
        'usage': 'Plaintext experiment only. Final Library binary and its Genius key are required before deployment.'}
    output.mkdir(parents=True)
    with (output / 'Genius.experimental.sqlite').open('xb') as handle:
        handle.write(clear)
    for name, document in [('library-assignments.json', assignments), ('report.json', report)]:
        with (output / name).open('x', encoding='utf-8') as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    print(json.dumps({'output': str(output), 'tracks': len(ids), 'playlist_length': len(checks['selection']['playlist_pids']),
                      'target_encryption_pending': True}))


if __name__ == '__main__':
    main()
