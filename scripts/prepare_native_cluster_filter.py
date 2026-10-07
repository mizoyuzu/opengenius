"""Prepare a separate native-format hard cluster filter experiment, offline.

Positive local bucket IDs occupy metadata word 0. They are not Apple genre IDs.
This does not implement the Web preferred-then-fill selection policy.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import struct

from decrypt_genius import derive_key, decrypt_pages, extract_key_header, validate_database
from genius_format import pack_config, parse_config, parse_similarities
from music_clusters import classify_tracks, scope_graphs
from music_identity_map import IdentityMap, load_library
from rewrite_genius import encrypt_pages, table_snapshot
from update_genius_bundle import read_bundle, sha, state_rows, update_bundle, update_sql


def compile_filter(clear, baseline, classifications, *, tags=(), kind=None, excluded_kinds=()):
    """Validate a synthetic baseline; change only word 0 and config data.

    Bucket 1: requested scope; 2: outside/unknown; 3: explicitly excluded.
    In-scope seeds allow bucket 1, outside seeds allow 1+2, excluded seeds
    allow none. The seed itself is retained by the observed Music core.
    """
    ids, metadata, _ = state_rows(baseline)
    # Retain the updater's strict SQL/config/schema checks, without weakening
    # its prohibition against genre filters or nonzero first metadata words.
    _, before = update_sql(clear, baseline, baseline)
    graphs = [dict(root_pid=pid, ordered_target_pids=[]) for pid in ids]
    _, scope = scope_graphs(graphs, classifications, tags, kind, excluded_kinds, mode='prefer')
    preferred = set(scope['preferred_pids']) & ids.keys()
    if (tags or kind is not None) and not preferred:
        raise ValueError('No assigned Genius tracks match the requested scope')
    buckets = {pid: (3 if classifications[pid]['kind'] in excluded_kinds
                     else 1 if pid in preferred else 2) for pid in ids}
    replacement = {gid: struct.pack('<Q', buckets[pid]) + metadata[gid][8:]
                   for pid, gid in ids.items()}
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.deserialize(clear)
        schema = connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        raw_config = connection.execute('SELECT data FROM genius_config WHERE id=1').fetchone()[0]
        config = parse_config(raw_config)
        config['filters'].append(dict(type=2, metadata_index=0, records=[(1, [1]), (2, [1, 2]), (3, [])]))
        packed = pack_config(config)
        expected = dict(before)
        expected['genius_metadata'] = [(gid, 1, blob) for gid, blob in sorted(replacement.items())]
        # Read the real column position rather than assume data is last.
        columns = [row[1] for row in connection.execute('PRAGMA table_info(genius_config)')]
        position = columns.index('data')
        expected['genius_config'] = [tuple(packed if i == position else value for i, value in enumerate(row))
                                     for row in before['genius_config']]
        with connection:
            connection.executemany('UPDATE genius_metadata SET data=? WHERE genius_id=?',
                                   [(blob, gid) for gid, blob in sorted(replacement.items())])
            connection.execute('UPDATE genius_config SET data=? WHERE id=1', (packed,))
            if (table_snapshot(connection) != expected or connection.execute(
                    'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall() != schema):
                raise ValueError('Unexpected native filter SQL or schema change')
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('Native filter SQL integrity failed')
        result = connection.serialize()
    audit = dict(schema_version=1, policy='native_hard_scope_for_matching_seeds', scope=scope,
                 bucket_counts={str(bucket): sum(value == bucket for value in buckets.values()) for bucket in (1, 2, 3)},
                 pid_buckets=buckets, similarities_preserved=True, metadata_tail_preserved=True,
                 web_preference_encoded=False, music_app_acceptance_verified=False, ipod_acceptance_verified=False)
    return result, expected, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'baseline-state', 'track-snapshot', 'identity-map', 'cluster-config', 'executable', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--cluster-tag', action='append', default=[])
    parser.add_argument('--kind')
    parser.add_argument('--exclude-kind', action='append', default=[])
    args = parser.parse_args()
    output = args.output.resolve()
    inputs = {name: path for name, path in vars(args).items() if isinstance(path, Path) and name != 'output'}
    if output.exists() or any(output == path.resolve() or output.is_relative_to(path.resolve())
                              or path.resolve().is_relative_to(output) for path in inputs.values()):
        parser.error('Output must be new and separate from every input')
    snapshots = {name: path.read_bytes() for name, path in inputs.items() if name != 'bundle'}
    baseline = json.loads(snapshots['baseline_state'])
    tracks, library_hash, _ = load_library(track_snapshot=args.track_snapshot)
    matcher = IdentityMap(json.loads(snapshots['identity_map']), tracks, library_hash)
    classifications = classify_tracks(json.loads(snapshots['cluster_config']), tracks, library_hash)
    ids, _, relations = state_rows(baseline)
    reverse = {gid: pid for pid, gid in ids.items()}
    roots = [dict(root_pid=pid, ordered_target_pids=[reverse[gid] for gid in parse_similarities(relations[ids[pid]])[1]])
             for pid in ids]
    files = read_bundle(args.bundle)
    # Validate the complete Library, key/state pair and synthetic source labels.
    _, source_audit, plan = update_bundle(files, baseline, roots, matcher, library_hash,
                                           sha(snapshots['identity_map']), args.executable)
    if plan['new_pids'] or plan['relation_deltas']:
        raise ValueError('Unexpected baseline delta')
    key, _ = derive_key(args.executable, extract_key_header(files['Library Preferences.musicdb']))
    clear, expected, audit = compile_filter(decrypt_pages(files['Genius.itdb'], key), baseline, classifications,
                                            tags=args.cluster_tag, kind=args.kind, excluded_kinds=args.exclude_kind)
    encrypted = encrypt_pages(clear, key)
    recovered = decrypt_pages(encrypted, key)
    counts = validate_database(recovered)
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.deserialize(recovered)
        if table_snapshot(connection) != expected:
            raise ValueError('Native filter encrypted round trip differs')
    prepared = {**files, 'Genius.itdb': encrypted}
    audit.update(source_file_sha256={name: sha(blob) for name, blob in files.items()},
                 output_file_sha256={name: sha(blob) for name, blob in prepared.items()},
                 input_sha256={name: sha(blob) for name, blob in snapshots.items()},
                 baseline_state_sha256=baseline['state_sha256'], compiler_sha256=sha(Path(__file__).read_bytes()),
                 library_track_count=source_audit['library_track_count'], table_counts=counts,
                 encrypted_round_trip_verified=True, local_genius_state_preserved=True, network_requests=0,
                 incremental_update_supported=False)
    if read_bundle(args.bundle) != files or any(path.read_bytes() != snapshots[name]
                                                for name, path in inputs.items() if name != 'bundle'):
        raise ValueError('Inputs changed during preparation')
    output.mkdir(mode=0o700)
    bundle = output / 'Music Library.musiclibrary'
    bundle.mkdir()
    for name, blob in prepared.items():
        target = bundle / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    (output / 'report.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({name: audit[name] for name in ('policy', 'bucket_counts', 'library_track_count')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
