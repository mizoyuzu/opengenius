#!/usr/bin/env python3
"""Compare private native Music snapshots without displaying account identifiers."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from decrypt_genius import derive_key, decrypt_pages, extract_key_header, validate_database
from inspect_music_library import decode_musicdb, parse_tracks
from genius_format import parse_similarities, unsigned_id
from rewrite_genius import table_snapshot


def audit_ordinary_playlists(report, manifest):
    """Recompute persistence from native observations and the external input plan."""
    plans = manifest.get('recommendation_playlists', [])
    observed = report.get('ordinary_recommendation_playlists') or {}
    created, reopened = observed.get('creation') or [], observed.get('reopened') or []
    verified = bool(plans) and len(plans) == len(created) == len(reopened)
    for plan, first, later in zip(plans, created, reopened):
        verified &= (first.get('playlist_pid') == later.get('playlist_pid')
                     and bool(first.get('playlist_pid'))
                     and first.get('name') == later.get('name') == plan['name']
                     and first.get('genius') is False and later.get('genius') is False
                     and first.get('persistent_ids') == later.get('persistent_ids') == plan['persistent_ids'])
    verified &= (observed.get('music_exited_before_reopen') is True
                 and report.get('after_copy_consistent_exit') is True)
    return {'ordinary_playlist_count_requested': len(plans),
            'ordinary_playlist_persistence_verified': bool(verified),
            'ordinary_playlist_validation_requested': bool(plans)}


def audit_relation_graph(connection):
    """Count supported Music relation envelopes; emit no track identifiers."""
    metadata = {unsigned_id(row[0]) for row in connection.execute('SELECT genius_id FROM genius_metadata')}
    edges = dangling = missing_sources = unsupported = rows = 0
    for gid, version, blob in connection.execute('SELECT genius_id,version,data FROM genius_similarities'):
        rows += 1
        missing_sources += unsigned_id(gid) not in metadata
        if version != 1 or not isinstance(blob, bytes):
            unsupported += 1
            continue
        try:
            _, targets = parse_similarities(blob)
        except ValueError:
            unsupported += 1
            continue
        edges += len(targets)
        dangling += sum(target not in metadata for target in targets)
    return {'envelope_profile': 'observed_music_v1_uncompressed',
            'similarity_rows': rows, 'examined_directed_edges': edges,
            'dangling_targets': dangling, 'sources_missing_metadata': missing_sources,
            'unsupported_rows': unsupported,
            'complete_reference_integrity_verified': not (dangling or missing_sources or unsupported)}


def audit(directory, executable, manifest=None):
    directory = Path(directory)
    report = json.loads((directory / 'report.json').read_text())
    counts, snapshots, tracks, graphs = {}, {}, {}, {}
    for stage in ('before', 'after'):
        root = directory / stage
        recorded = {entry['path']: entry for entry in report[f'{stage}_files']}
        for name in ('Library.musicdb', 'Genius.itdb', 'Library Preferences.musicdb'):
            if name not in recorded or hashlib.sha256((root / name).read_bytes()).hexdigest() != recorded[name]['sha256']:
                raise ValueError('native snapshot differs from recorded hash')
        tracks[stage], _ = parse_tracks(decode_musicdb((root / 'Library.musicdb').read_bytes()))
        key, _ = derive_key(executable, extract_key_header((root / 'Library Preferences.musicdb').read_bytes()))
        clear = decrypt_pages((root / 'Genius.itdb').read_bytes(), key)
        counts[stage] = validate_database(clear)
        with sqlite3.connect(':memory:') as connection:
            connection.deserialize(clear)
            snapshots[stage] = table_snapshot(connection)
            graphs[stage] = audit_relation_graph(connection)
    by_pid = {stage: {t['persistent_id']: t for t in rows} for stage, rows in tracks.items()}
    same_ids = set(by_pid['before']) == set(by_pid['after'])
    gids_preserved = same_ids and all(by_pid['before'][pid]['genius_id'] == by_pid['after'][pid]['genius_id'] for pid in by_pid['before'])
    result = {'schema_version': 1, 'track_counts': {s: len(t) for s, t in tracks.items()},
            'persistent_ids_preserved': same_ids, 'genius_ids_preserved': gids_preserved,
            'genius_logical_tables_preserved': snapshots['before'] == snapshots['after'],
            'table_counts': counts, 'genius_relation_graphs': graphs,
            'native_library_loaded': report.get('status') == 'library_loaded',
            'native_exit_verified': report.get('after_copy_consistent_exit', False),
            'media_locations_verified': sum(r.get('location_verified', False) for r in report.get('relink_results') or []),
            'native_playback_progress_verified': sum(bool((r.get('playback') or {}).get('position_advanced')) for r in report.get('seed_results', [])),
            'genius_menu_statuses': [r['genius_menu_status'] for r in report.get('seed_results', [])],
            'native_genius_generation_observed': report.get('actual_genius_generation_observed', False),
            'ipod_acceptance_verified': False}
    if manifest is not None:
        result.update(audit_ordinary_playlists(report, manifest))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, help='Independent requested ordinary-playlist plan')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text()) if args.manifest else None
    print(json.dumps(audit(args.directory, args.executable, manifest), indent=2))

if __name__ == '__main__':
    main()
