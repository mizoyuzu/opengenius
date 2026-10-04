#!/usr/bin/env python3
"""Compare private native Music snapshots without displaying account identifiers."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from decrypt_genius import derive_key, decrypt_pages, extract_key_header, validate_database
from inspect_music_library import decode_musicdb, parse_tracks
from rewrite_genius import table_snapshot


def audit(directory, executable):
    directory = Path(directory)
    report = json.loads((directory / 'report.json').read_text())
    counts, snapshots, tracks = {}, {}, {}
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
    by_pid = {stage: {t['persistent_id']: t for t in rows} for stage, rows in tracks.items()}
    same_ids = set(by_pid['before']) == set(by_pid['after'])
    gids_preserved = same_ids and all(by_pid['before'][pid]['genius_id'] == by_pid['after'][pid]['genius_id'] for pid in by_pid['before'])
    return {'schema_version': 1, 'track_counts': {s: len(t) for s, t in tracks.items()},
            'persistent_ids_preserved': same_ids, 'genius_ids_preserved': gids_preserved,
            'genius_logical_tables_preserved': snapshots['before'] == snapshots['after'],
            'table_counts': counts, 'native_library_loaded': report.get('status') == 'library_loaded',
            'native_exit_verified': report.get('after_copy_consistent_exit', False),
            'media_locations_verified': sum(r.get('location_verified', False) for r in report.get('relink_results') or []),
            'native_playback_progress_verified': sum(bool((r.get('playback') or {}).get('position_advanced')) for r in report.get('seed_results', [])),
            'genius_menu_statuses': [r['genius_menu_status'] for r in report.get('seed_results', [])],
            'native_genius_generation_observed': report.get('actual_genius_generation_observed', False),
            'ipod_acceptance_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--executable', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.directory, args.executable), indent=2))

if __name__ == '__main__':
    main()
