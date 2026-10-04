"""Audit downloaded synthetic native reload artifacts without displaying keys."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from decrypt_genius import decrypt_pages, derive_key, extract_key_header, validate_database
from inspect_music_library import decode_musicdb, parse_tracks
from rewrite_genius import table_snapshot


def audit(directory, executable):
    report = json.loads((directory / 'report.json').read_text())
    if report.get('fixture_kind') != 'synthetic-tone-only':
        raise ValueError('Require a synthetic reload artifact')
    snapshots = {}
    tracks = {}
    counts = {}
    for stage in ('before', 'after'):
        root = directory / stage
        library = (root / 'Library.musicdb').read_bytes()
        genius = (root / 'Genius.itdb').read_bytes()
        preferences = (root / 'Library Preferences.musicdb').read_bytes()
        for name, blob in [('Library.musicdb', library), ('Genius.itdb', genius), ('Library Preferences.musicdb', preferences)]:
            if hashlib.sha256(blob).hexdigest() != report['files'][name][f'{stage}_sha256']:
                raise ValueError(f'{stage} {name} differs from native report')
        tracks[stage], _ = parse_tracks(decode_musicdb(library))
        key, _ = derive_key(executable, extract_key_header(preferences))
        clear = decrypt_pages(genius, key)
        counts[stage] = validate_database(clear)
        with sqlite3.connect(':memory:') as connection:
            connection.deserialize(clear)
            snapshots[stage] = table_snapshot(connection)
    expected = sorted(report['expected_tracks'], key=lambda t: t['persistent_id'])
    ordered = {stage: sorted(rows, key=lambda t: t['persistent_id']) for stage, rows in tracks.items()}
    return {'schema_version': 1, 'fixture_kind': 'synthetic-tone-only',
            'native_tracks_loaded': report.get('expected_tracks_loaded', False),
            'native_exit_verified': report.get('after_copy_consistent_exit', False),
            'before_tracks_match_expected': ordered['before'] == expected,
            'after_tracks_match_expected': ordered['after'] == expected,
            'genius_logical_tables_preserved': snapshots['before'] == snapshots['after'],
            'table_counts': counts,
            'actual_genius_generation_tested': report.get('actual_genius_generation_tested', False),
            'ipod_acceptance_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact_directory', type=Path)
    parser.add_argument('--executable', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.artifact_directory, args.executable), indent=2))


if __name__ == '__main__':
    main()
