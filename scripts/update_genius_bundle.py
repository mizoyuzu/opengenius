"""Update a separate OpenGenius synthetic bundle while preserving established IDs.

Checks the complete baseline SQL rows and PID/GID assignments before editing.
Does not install a live library, activate Apple Genius, or contact a service.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from decrypt_genius import derive_key, decrypt_pages, extract_key_header, validate_database
from genius_format import parse_config, parse_metadata, parse_similarities
from inspect_music_library import decode_musicdb, parse_tracks
from music_identity_map import IdentityMap, load_library
from plan_genius_update import make_synthetic_state, plan_genius_update, PRODUCER, _hash
from prepare_music_genius_state import inspect_state_pair
from rewrite_genius import encrypt_pages, table_snapshot
from rewrite_music_ids import encode_library, patch_ids

CORE = ('Library.musicdb', 'Library Preferences.musicdb', 'Genius.itdb')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def state_rows(state):
    if (state.get('producer') != PRODUCER or type(state.get('schema_version')) is not int
            or state.get('schema_version') != 1 or not isinstance(state.get('rows'), list) or not state['rows']
            or state.get('state_sha256') != _hash({k: v for k, v in state.items() if k != 'state_sha256'})):
        raise ValueError('Invalid synthetic state')
    ids, metadata, relations = {}, {}, {}
    for row in state['rows']:
        pid, gid = row['persistent_id'], row['genius_id']
        if pid in ids or type(gid) is not int or not 0 < gid <= 0xffffffff or gid in metadata:
            raise ValueError('Invalid state assignment')
        ids[pid] = gid
        metadata[gid] = bytes.fromhex(row['metadata_hex'])
        relations[gid] = bytes.fromhex(row['similarities_hex'])
        if parse_metadata(metadata[gid])[0] != 0:
            raise ValueError('Non-synthetic metadata')
    for blob in relations.values():
        word, targets = parse_similarities(blob)
        if word != 0 or len(set(targets)) != len(targets) or not set(targets) <= metadata.keys():
            raise ValueError('Invalid synthetic relations')
    return ids, metadata, relations


def update_sql(clear, baseline, next_state):
    old_ids, old_metadata, old_relations = state_rows(baseline)
    ids, metadata, relations = state_rows(next_state)
    if (baseline['source'] != next_state['source']
            or any(ids.get(pid) != gid or metadata.get(gid) != old_metadata[gid]
                   for pid, gid in old_ids.items())):
        raise ValueError('Existing assignments or metadata changed')
    validate_database(clear)
    with sqlite3.connect(':memory:') as connection:
        connection.deserialize(clear)
        before = table_snapshot(connection)
        schema = connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        required = {'genius_metadata', 'genius_similarities', 'genius_config'}
        if any(rows for name, rows in before.items() if name not in required):
            raise ValueError('Nonempty auxiliary data requires a separate rebasing workflow')
        for name, blobs in (('genius_metadata', old_metadata), ('genius_similarities', old_relations)):
            if before[name] != [(gid, 1, blob) for gid, blob in sorted(blobs.items())]:
                raise ValueError('Baseline SQL rows differ from synthetic state')
        config_rows = connection.execute('SELECT id,version,data FROM genius_config').fetchall()
        if len(config_rows) != 1 or config_rows[0][:2] != (1, 2):
            raise ValueError('Unsupported configuration rows')
        config = parse_config(config_rows[0][2])
        if config['version'] != 2 or any(item['type'] == 2 for item in config['filters']):
            raise ValueError('Synthetic update cannot use compatible_genre')
        expected = dict(before)
        expected['genius_metadata'] = [(gid, 1, blob) for gid, blob in sorted(metadata.items())]
        expected['genius_similarities'] = [(gid, 1, blob) for gid, blob in sorted(relations.items())]
        with connection:
            connection.executemany('INSERT INTO genius_metadata VALUES(?,1,?)',
                                   [(gid, metadata[gid]) for gid in sorted(metadata.keys() - old_metadata.keys())])
            connection.executemany('UPDATE genius_similarities SET data=? WHERE genius_id=?',
                                   [(relations[gid], gid) for gid in sorted(old_relations)])
            connection.executemany('INSERT INTO genius_similarities VALUES(?,1,?)',
                                   [(gid, relations[gid]) for gid in sorted(relations.keys() - old_relations.keys())])
            if (table_snapshot(connection) != expected or connection.execute(
                    'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall() != schema):
                raise ValueError('Unexpected SQL or schema change')
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('Updated database integrity failed')
        return connection.serialize(), expected


def update_bundle(files, baseline, roots, matcher, library_hash, identity_hash, executable):
    plan = plan_genius_update(baseline, roots, matcher, library_hash, identity_hash)
    old_ids, _, _ = state_rows(baseline)
    ids, _, _ = state_rows(plan['next_state'])
    expanded = decode_musicdb(files['Library.musicdb'])
    # Keep the established codec guard; this path does not bypass legacy refusal.
    if encode_library(expanded, files['Library.musicdb']) != files['Library.musicdb']:
        raise ValueError('Source Library codec does not reproduce its bytes')
    tracks, _ = parse_tracks(expanded)
    source_tracks = {t['persistent_id']: t for t in matcher.tracks}
    if any(t.get('genius_id') != '0000000000000000' for t in source_tracks.values()):
        raise ValueError('Source snapshot must describe the original zero-ID Library')
    expected = {pid: {**track, 'genius_id': f'{old_ids.get(pid, 0):016X}'} for pid, track in source_tracks.items()}
    if len(tracks) != len(expected) or {t['persistent_id']: t for t in tracks} != expected:
        raise ValueError('Source Library metadata or PID/GID mapping differs from baseline')
    pair = inspect_state_pair(files)
    if not pair['key_headers_match']:
        raise ValueError('Library key headers differ')
    key, _ = derive_key(executable, extract_key_header(files['Library Preferences.musicdb']))
    clear, expected_tables = update_sql(decrypt_pages(files['Genius.itdb'], key), baseline, plan['next_state'])
    encrypted = encrypt_pages(clear, key)
    recovered = decrypt_pages(encrypted, key)
    counts = validate_database(recovered)
    with sqlite3.connect(':memory:') as connection:
        connection.deserialize(recovered)
        if table_snapshot(connection) != expected_tables:
            raise ValueError('Encrypted SQL round trip differs')
    assignments = [dict(persistent_id=pid, genius_id=f'{ids[pid]:016X}') for pid in plan['new_pids']]
    changes = []
    library = files['Library.musicdb']
    if assignments:
        patched, changes = patch_ids(expanded, assignments)
        library = encode_library(patched, library)
    saved, _ = parse_tracks(decode_musicdb(library))
    if {t['persistent_id']: t for t in saved} != {
            pid: {**track, 'genius_id': f'{ids.get(pid, 0):016X}'} for pid, track in source_tracks.items()}:
        raise ValueError('Unexpected saved track metadata')
    output = {**files, 'Library.musicdb': library, 'Genius.itdb': encrypted}
    if inspect_state_pair(output) != pair:
        raise ValueError('Local Genius state changed')
    report = dict(schema_version=1, **plan['audit'], library_track_count=len(saved),
                  changed_relation_rows=len(plan['relation_deltas']), changed_id_slots=changes,
                  source_file_sha256={name: sha(blob) for name, blob in files.items()},
                  output_file_sha256={name: sha(blob) for name, blob in output.items()},
                  config_and_auxiliary_tables_preserved=True, local_genius_state_preserved=True,
                  encrypted_round_trip_verified=True, sql_integrity_check='ok', table_counts=counts,
                  previous_state_sha256=baseline['state_sha256'], next_state_sha256=plan['next_state']['state_sha256'],
                  network_requests=0, music_app_acceptance_verified=False, ipod_acceptance_verified=False)
    return output, report, plan


def read_bundle(directory):
    files, total = {}, 0
    if directory.is_symlink():
        raise ValueError('Symlink bundle is unsupported')
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink bundle entries are unsupported')
        if path.is_file():
            size = path.stat().st_size
            total += size
            if size > 64 * 1024 * 1024 or total > 512 * 1024 * 1024:
                raise ValueError('Bundle size exceeds offline update limit')
            files[str(path.relative_to(directory))] = path.read_bytes()
    if not set(CORE) <= files.keys():
        raise ValueError('Bundle lacks required core files')
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'track-snapshot', 'identity-map', 'evaluation', 'executable', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--baseline-state', type=Path)
    source.add_argument('--baseline-evaluation', type=Path, help='Bootstrap state only when complete baseline SQL matches')
    args = parser.parse_args()
    output = args.output.resolve()
    paths = [value.resolve() for name, value in vars(args).items() if isinstance(value, Path) and name != 'output']
    if output.exists() or any(output == path or output.is_relative_to(path) or path.is_relative_to(output) for path in paths):
        parser.error('Output must be new and separate from every input')
    input_bytes = {name: path.read_bytes() for name, path in vars(args).items()
                   if isinstance(path, Path) and name not in ('bundle', 'output')}
    tracks, library_hash, _ = load_library(track_snapshot=args.track_snapshot)
    identity_hash = sha(input_bytes['identity_map'])
    matcher = IdentityMap(json.loads(input_bytes['identity_map']), tracks, library_hash)
    evaluation = json.loads(input_bytes['evaluation'])
    if evaluation.get('library_sha256') != library_hash:
        parser.error('Evaluation belongs to a different Library')
    if args.baseline_state:
        baseline = json.loads(input_bytes['baseline_state'])
    else:
        old = json.loads(input_bytes['baseline_evaluation'])
        if old.get('library_sha256') != library_hash:
            parser.error('Baseline evaluation belongs to a different Library')
        baseline = make_synthetic_state(old['relation_rows'], matcher, library_hash, identity_hash)
    files = read_bundle(args.bundle)
    prepared, report, plan = update_bundle(files, baseline, evaluation['relation_rows'], matcher,
                                          library_hash, identity_hash, args.executable)
    report['input_sha256'] = {name: sha(blob) for name, blob in input_bytes.items()}
    report['updater_sha256'] = sha(Path(__file__).read_bytes())
    if read_bundle(args.bundle) != files or any(
            path.read_bytes() != input_bytes[name] for name, path in vars(args).items()
            if isinstance(path, Path) and name not in ('bundle', 'output')):
        raise ValueError('Inputs changed during preparation')
    output.mkdir(mode=0o700)
    bundle = output / 'Music Library.musiclibrary'
    bundle.mkdir()
    for name, blob in prepared.items():
        target = bundle / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    for name, document in (('report.json', report), ('plan.json', plan), ('state.json', plan['next_state'])):
        (output / name).write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({name: report[name] for name in ('old_tracks', 'added_tracks', 'total_tracks', 'directed_edges')}))


if __name__ == '__main__':
    main()
