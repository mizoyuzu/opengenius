"""Independently audit an incremental synthetic Genius bundle copy.

Reads and decrypts both bundles, compares them with their sealed synthetic
states, and optionally runs a few roots through the local MusicCore emulator.
It never writes a bundle or launches Music.app.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from decrypt_genius import derive_key, decrypt_pages, extract_key_header, validate_database
from emulate_genius import MusicCore, database_rows
from genius_format import parse_similarities
from genius_sync_revision import revisions_for_assignments
from inspect_music_library import decode_musicdb, parse_tracks
from music_identity_map import IdentityMap, load_library
from plan_genius_update import _source
from rewrite_genius import table_snapshot
from update_genius_bundle import read_bundle, state_rows


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _state_db_rows(state, db_metadata, db_relations, label):
    ids, metadata, relations = state_rows(state)
    if db_metadata != metadata or db_relations != relations:
        raise ValueError(label + ' SQL rows differ from synthetic state')
    known = set(db_metadata)
    edges = 0
    nonempty = 0
    for gid, blob in db_relations.items():
        _, targets = parse_similarities(blob)
        if not set(targets) <= known:
            raise ValueError(label + ' relation targets an unknown Genius ID')
        edges += len(targets)
        nonempty += bool(targets)
    return ids, metadata, relations, edges, nonempty


def audit_rows(baseline_state, output_state, baseline_config, output_config,
               baseline_metadata, baseline_relations, output_metadata,
               output_relations, baseline_tracks, output_tracks):
    """Audit decoded DB/state/Library rows; exposed for fixture-based tests."""
    old_ids, old_metadata, _, old_edges, old_nonempty = _state_db_rows(
        baseline_state, baseline_metadata, baseline_relations, 'Baseline')
    new_ids, new_metadata, new_relations, new_edges, new_nonempty = _state_db_rows(
        output_state, output_metadata, output_relations, 'Output')
    if baseline_state.get('source') != output_state.get('source'):
        raise ValueError('Baseline and output states describe different sources')
    if any(new_ids.get(pid) != gid or new_metadata.get(gid) != old_metadata[gid]
           for pid, gid in old_ids.items()):
        raise ValueError('Existing PID/GID assignment or metadata changed')
    if baseline_config != output_config:
        raise ValueError('Genius configuration changed')
    for label, tracks, ids in (('Baseline', baseline_tracks, old_ids),
                               ('Output', output_tracks, new_ids)):
        by_pid = {row.get('persistent_id'): row for row in tracks}
        if len(by_pid) != len(tracks) or set(ids) - by_pid.keys():
            raise ValueError(label + ' Library PID set does not cover state assignments')
        nonzero = {pid: int(row.get('genius_id') or '0', 16)
                   for pid, row in by_pid.items() if row.get('genius_id') and int(row['genius_id'], 16)}
        if nonzero != ids:
            raise ValueError(label + ' Library Genius IDs differ from state assignments')
    if set(old_ids) - set(new_ids):
        raise ValueError('Output state dropped existing PIDs')
    return {
        'existing_pid_count': len(old_ids),
        'existing_pid_gid_and_metadata_preserved': True,
        'output_indexed_pid_count': len(new_ids),
        'added_indexed_pid_count': len(set(new_ids) - set(old_ids)),
        'baseline_directed_edge_count': old_edges,
        'output_directed_edge_count': new_edges,
        'baseline_nonempty_outgoing_row_count': old_nonempty,
        'output_nonempty_outgoing_row_count': new_nonempty,
        'genius_config_preserved': True,
        'baseline_relation_targets_known': True,
        'output_relation_targets_known': True,
        'baseline_library_track_count': len(baseline_tracks),
        'output_library_track_count': len(output_tracks),
    }


def _decrypt_bundle(files, executable):
    preferences = files['Library Preferences.musicdb']
    header = extract_key_header(preferences)
    key, steps = derive_key(executable, header)
    clear = decrypt_pages(files['Genius.itdb'], key)
    counts = validate_database(clear)
    config, metadata, relations = database_rows(clear)
    return clear, config, metadata, relations, counts, steps


def _track_rows(files):
    return parse_tracks(decode_musicdb(files['Library.musicdb']))[0]


def _sync_revision_audit(expanded, ids, metadata, relations, require_sync_revisions=False):
    """Verify the saved itma revision fields against independently read SQL BLOBs."""
    assignments = [dict(persistent_id=pid, genius_id=gid) for pid, gid in ids.items()]
    expected = revisions_for_assignments(assignments, metadata, relations)
    expected = {(int(pid, 16) if isinstance(pid, str) else pid): value
                for pid, value in expected.items()}
    start = expanded.find(b'ltma')
    if start < 0 or start + 12 > len(expanded):
        raise ValueError('Output Library has no ltma track list')
    import struct
    list_header, count = struct.unpack_from('<II', expanded, start + 4)
    if list_header < 12 or count > 100000 or start + list_header > len(expanded):
        raise ValueError('Output Library has invalid ltma bounds')
    cursor = start + list_header
    actual, seen = {}, set()
    for _ in range(count):
        if cursor + 24 > len(expanded) or expanded[cursor:cursor + 4] != b'itma':
            raise ValueError('Output Library has an invalid itma record')
        header, total = struct.unpack_from('<II', expanded, cursor + 4)
        if header != 376 or total < header or cursor + total > len(expanded):
            raise ValueError('Output Library has unsupported itma bounds')
        pid = struct.unpack_from('<Q', expanded, cursor + 16)[0]
        if pid in seen:
            raise ValueError('Output Library has duplicate track PIDs')
        seen.add(pid)
        if pid in expected:
            actual[pid] = struct.unpack_from('<I', expanded, cursor + 0x68)[0]
        cursor += total
    missing = set(expected) - actual.keys()
    mismatched = [pid for pid, revision in expected.items()
                  if pid in actual and (actual[pid] == 0 or actual[pid] != revision)]
    nonzero = sum(value != 0 for value in actual.values())
    matched = len(expected) - len(missing) - len(mismatched)
    if require_sync_revisions and (missing or mismatched):
        raise ValueError('Output Library sync revisions differ from Genius SQL BLOBs')
    return {
        'sync_revision_algorithm': 'opengenius-crc32-v1',
        'sync_revision_target_count': len(expected),
        'sync_revision_matching_count': matched,
        'sync_revision_nonzero_count': nonzero,
        'sync_revision_missing_count': len(missing),
        'sync_revision_mismatch_count': len(mismatched),
        'sync_revision_strict': bool(require_sync_revisions),
    }


def _library_record_offsets(expanded, label):
    import struct
    start = expanded.find(b'ltma')
    if start < 0 or start + 12 > len(expanded):
        raise ValueError(label + ' Library has no ltma track list')
    list_header, count = struct.unpack_from('<II', expanded, start + 4)
    if list_header < 12 or count > 100000 or start + list_header > len(expanded):
        raise ValueError(label + ' Library has invalid ltma bounds')
    cursor = start + list_header
    offsets = {}
    for _ in range(count):
        if cursor + 24 > len(expanded) or expanded[cursor:cursor + 4] != b'itma':
            raise ValueError(label + ' Library has an invalid itma record')
        header, total = struct.unpack_from('<II', expanded, cursor + 4)
        if header != 376 or total < header or cursor + total > len(expanded):
            raise ValueError(label + ' Library has unsupported itma bounds')
        pid = struct.unpack_from('<Q', expanded, cursor + 16)[0]
        if pid in offsets:
            raise ValueError(label + ' Library has duplicate track PIDs')
        offsets[pid] = cursor
        cursor += total
    return offsets


def _audit_library_change_scope(baseline_expanded, output_expanded, target_pids):
    """Require identical expanded bytes outside target itma ID and revision fields."""
    import struct
    target_pids = {int(pid, 16) if isinstance(pid, str) else pid for pid in target_pids}
    before_offsets = _library_record_offsets(baseline_expanded, 'Baseline')
    after_offsets = _library_record_offsets(output_expanded, 'Output')
    if before_offsets.keys() != after_offsets.keys():
        raise ValueError('Baseline and output Library PID sets differ')
    restored = bytearray(output_expanded)
    for pid, offset in after_offsets.items():
        before = before_offsets[pid]
        if (offset != before or struct.unpack_from('<II', output_expanded, offset + 4)
                != struct.unpack_from('<II', baseline_expanded, before + 4)):
            raise ValueError('Library record layout changed')
        if pid in target_pids:
            restored[offset + 0x68:offset + 0x6C] = baseline_expanded[before + 0x68:before + 0x6C]
            restored[offset + 0xCC:offset + 0xD0] = baseline_expanded[before + 0xCC:before + 0xD0]
    if restored != baseline_expanded:
        raise ValueError('Unexpected expanded Library changes outside Genius ID and sync revision fields')
    return True


def _sql_snapshot(clear):
    with sqlite3.connect(':memory:') as connection:
        connection.deserialize(clear)
        schema = connection.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        return schema, table_snapshot(connection)


def _assert_snapshot(tracks, source_tracks, label):
    by_pid = {track['persistent_id']: track for track in tracks}
    expected = {track['persistent_id']: {key: value for key, value in track.items()
                                         if key != 'genius_id'} for track in source_tracks}
    if set(by_pid) != set(expected):
        raise ValueError(label + ' Library track PID set differs from original snapshot')
    for pid, row in expected.items():
        actual = dict(by_pid[pid])
        actual.pop('genius_id', None)
        if actual != row:
            raise ValueError(label + ' Library track metadata differs from original snapshot')


def _core_sample(executable, config, metadata, relations, pid_to_gid, root_pids):
    if not root_pids:
        return None
    if len(root_pids) > 5 or len(set(root_pids)) != len(root_pids):
        raise ValueError('MusicCore sampling permits at most five distinct roots')
    reverse = {gid: pid for pid, gid in pid_to_gid.items()}
    results = []
    core = MusicCore(executable, config, metadata, relations, random_seed=0)
    for pid in root_pids:
        if pid not in pid_to_gid:
            raise ValueError('MusicCore sample root is not indexed')
        generated = core.generate(pid_to_gid[pid], 25)
        try:
            generated_pids = [reverse[int(value, 16)] for value in generated['result_genius_ids']]
        except (KeyError, ValueError):
            raise ValueError('MusicCore returned an unknown Genius ID') from None
        reachable, pending = set(), [pid_to_gid[pid]]
        while pending:
            current = pending.pop()
            if current in reachable:
                continue
            reachable.add(current)
            pending.extend(parse_similarities(relations[current])[1])
        if any(pid_to_gid[target] not in reachable for target in generated_pids):
            raise ValueError('MusicCore sample returned a track outside the persisted relation graph')
        results.append({'root_pid': pid, 'all_generated_tracks_reachable': True, 'generated_count': len(generated_pids),
                        'playlist_pids': generated_pids,
                        'instructions': generated['instructions']})
    return results


def audit_bundles(baseline_bundle, output_bundle, baseline_state, output_state,
                  track_snapshot, identity_map, executable, sample_roots=(),
                  require_sync_revisions=False):
    paths = [Path(p) for p in (baseline_bundle, output_bundle, baseline_state,
                               output_state, track_snapshot, identity_map, executable)]
    input_hashes_before = {str(path): _sha(path.read_bytes()) for path in paths
                           if path.is_file()}
    baseline_files, output_files = read_bundle(baseline_bundle), read_bundle(output_bundle)
    baseline_state_doc = json.loads(Path(baseline_state).read_text())
    output_state_doc = json.loads(Path(output_state).read_text())
    tracks, library_hash, provenance = load_library(track_snapshot=Path(track_snapshot))
    identity_bytes = Path(identity_map).read_bytes()
    matcher = IdentityMap(json.loads(identity_bytes), tracks, library_hash)
    expected_source, _ = _source(matcher, library_hash, _sha(identity_bytes))
    if (baseline_state_doc.get('source') != expected_source
            or output_state_doc.get('source') != expected_source):
        raise ValueError('State source hashes or track labels differ from supplied inputs')

    base_clear, base_config, base_metadata, base_relations, base_counts, base_steps = _decrypt_bundle(
        baseline_files, executable)
    out_clear, out_config, out_metadata, out_relations, out_counts, out_steps = _decrypt_bundle(
        output_files, executable)
    base_schema, base_tables = _sql_snapshot(base_clear)
    out_schema, out_tables = _sql_snapshot(out_clear)
    if base_schema != out_schema:
        raise ValueError('Genius SQL schema changed')
    if base_tables.keys() != out_tables.keys():
        raise ValueError('Genius SQL table set changed')
    for table in base_tables.keys() - {'genius_metadata', 'genius_similarities'}:
        if base_tables[table] != out_tables[table]:
            raise ValueError('Genius configuration or auxiliary SQL rows changed')
    base_tracks, out_tracks = _track_rows(baseline_files), _track_rows(output_files)
    baseline_expanded = decode_musicdb(baseline_files['Library.musicdb'])
    output_expanded = decode_musicdb(output_files['Library.musicdb'])
    _assert_snapshot(base_tracks, tracks, 'Baseline')
    _assert_snapshot(out_tracks, tracks, 'Output')
    audit = audit_rows(baseline_state_doc, output_state_doc, base_config, out_config,
                       base_metadata, base_relations, out_metadata, out_relations,
                       base_tracks, out_tracks)
    sync_audit = _sync_revision_audit(output_expanded, state_rows(output_state_doc)[0],
                                      out_metadata, out_relations,
                                      require_sync_revisions=require_sync_revisions)
    sync_audit['expanded_library_change_scope_verified'] = _audit_library_change_scope(
        baseline_expanded, output_expanded,
        {int(pid, 16) if isinstance(pid, str) else pid
         for pid in state_rows(output_state_doc)[0]})
    if set(baseline_files) != set(output_files):
        raise ValueError('Bundle file set changed')
    changed_files = [name for name in sorted(baseline_files)
                     if baseline_files[name] != output_files[name]]
    allowed = {'Library.musicdb', 'Genius.itdb'}
    if set(changed_files) - allowed:
        raise ValueError('Unexpected bundle files changed')
    if baseline_files['Library Preferences.musicdb'] != output_files['Library Preferences.musicdb']:
        raise ValueError('Library Preferences changed')
    for label, clear, counts in (('Baseline', base_clear, base_counts), ('Output', out_clear, out_counts)):
        if validate_database(clear) != counts:
            raise ValueError(label + ' SQL table counts changed during audit')
    sampled = _core_sample(executable, out_config, out_metadata, out_relations,
                           state_rows(output_state_doc)[0], sample_roots)
    input_hashes_after = {str(path): _sha(path.read_bytes()) for path in paths
                          if path.is_file()}
    if input_hashes_before != input_hashes_after:
        raise ValueError('An input changed during audit')
    if read_bundle(baseline_bundle) != baseline_files or read_bundle(output_bundle) != output_files:
        raise ValueError('A bundle changed during audit')
    return {
        'schema_version': 1,
        'source_library_sha256': library_hash,
        'source_library_input': provenance,
        'identity_map_sha256': _sha(identity_bytes),
        'baseline_state_sha256': baseline_state_doc['state_sha256'],
        'output_state_sha256': output_state_doc['state_sha256'],
        'baseline_bundle_sha256': {name: _sha(blob) for name, blob in baseline_files.items()},
        'output_bundle_sha256': {name: _sha(blob) for name, blob in output_files.items()},
        'changed_bundle_files': changed_files,
        'music_key_derivation_instructions': {'baseline': base_steps, 'output': out_steps},
        'sqlite_integrity': {'baseline': 'ok', 'output': 'ok'},
        'table_counts': {'baseline': base_counts, 'output': out_counts},
        **audit,
        **sync_audit,
        'music_core_sample': sampled,
        'network_requests': 0,
        'music_app_launched': False,
        'music_app_acceptance_verified': False,
        'ipod_acceptance_verified': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline-bundle', 'output-bundle', 'baseline-state', 'output-state',
                 'track-snapshot', 'identity-map', 'executable', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--sample-root', action='append', default=[],
                        help='optional output-state root PID; repeat at most five times')
    parser.add_argument('--require-sync-revisions', action='store_true',
                        help='reject output copies whose saved itma revisions do not match Genius SQL BLOBs')
    args = parser.parse_args()
    destination = args.output.resolve()
    inputs = [getattr(args, name.replace('-', '_')).resolve() for name in
              ('baseline-bundle', 'output-bundle', 'baseline-state', 'output-state',
               'track-snapshot', 'identity-map', 'executable')]
    if destination.exists() or any(destination == path or destination.is_relative_to(path)
                                   or path.is_relative_to(destination) for path in inputs):
        parser.error('Output must be new and separate from every input')
    if len(args.sample_root) > 5 or any(re.fullmatch(r'[A-F0-9]{16}', pid) is None
                                        for pid in args.sample_root):
        parser.error('--sample-root requires up to five uppercase 16-hex PIDs')
    try:
        report = audit_bundles(args.baseline_bundle, args.output_bundle,
                               args.baseline_state, args.output_state,
                               args.track_snapshot, args.identity_map, args.executable,
                               args.sample_root, require_sync_revisions=args.require_sync_revisions)
        report['input_sha256'] = {str(path): _sha(path.read_bytes()) for path in inputs
                                  if path.is_file()}
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as error:
        parser.error(str(error))
    print(json.dumps({'output': str(destination),
                      'existing_pid_count': report['existing_pid_count'],
                      'output_indexed_pid_count': report['output_indexed_pid_count'],
                      'output_directed_edge_count': report['output_directed_edge_count'],
                      'sync_revision_matching_count': report['sync_revision_matching_count'],
                      'sync_revision_mismatch_count': report['sync_revision_mismatch_count'],
                      'music_core_sample_count': len(report['music_core_sample'] or [])}))


if __name__ == '__main__':
    main()
