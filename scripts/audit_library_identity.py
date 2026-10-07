"""Audit Library titles, version qualifiers, aliases, and duplicate metadata groups.

This report preserves source metadata and never resolves a duplicate or asserts
recording identity. It reads either a saved track snapshot or a Library bundle.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from music_identity_map import IdentityMap, load_library, normalize, version_facets


def audit_library_identity(tracks, identity_map, library_hash, library_input=None,
                           identity_map_sha256=None, input_hashes=None, script_sha256=None):
    """Return a complete, non-resolving audit of the supplied Library snapshot."""
    IdentityMap(identity_map, tracks, library_hash)
    alias_rows = defaultdict(list)
    alias_state_counts = Counter()
    for row in identity_map.get('track_title_aliases', []):
        for alias in row.get('aliases', []):
            enabled = bool(alias.get('enabled'))
            status = alias.get('status', 'unspecified')
            alias_state_counts['enabled' if enabled else 'pending'] += 1
            alias_state_counts['status:' + str(status)] += 1
            alias_rows[row['persistent_id']].append({
                'value': alias['value'], 'enabled': enabled,
                'status': status, 'reason': alias.get('reason'),
            })

    duplicate_groups = defaultdict(list)
    track_rows = []
    marker_counts = Counter()
    kind_counts = Counter()
    for track in tracks:
        title = track.get('title') or ''
        artist = track.get('artist') or ''
        facets = version_facets(title)
        marker_counts.update(facets['markers'])
        kind_counts.update(facets['kinds'])
        duplicate_groups[(normalize(title), normalize(artist))].append(track)
        track_rows.append({
            'persistent_id': track['persistent_id'],
            'title': title,
            'artist': artist,
            'album': track.get('album'),
            'duration_ms': track.get('duration_ms'),
            'version_facets': facets,
            'title_aliases': alias_rows.get(track['persistent_id'], []),
            'identity_status': 'unverified',
        })

    duplicate_rows = []
    for (title_key, artist_key), rows in duplicate_groups.items():
        if len(rows) < 2:
            continue
        duplicate_rows.append({
            'normalized_title': title_key,
            'normalized_artist': artist_key,
            'persistent_ids': sorted(row['persistent_id'] for row in rows),
            'records': [{
                'persistent_id': row['persistent_id'],
                'title': row.get('title') or '',
                'artist': row.get('artist') or '',
                'album': row.get('album'),
                'duration_ms': row.get('duration_ms'),
            } for row in rows],
            'auto_resolved': False,
            'identity_status': 'unverified',
        })
    duplicate_rows.sort(key=lambda row: (row['normalized_title'], row['normalized_artist']))

    pending = alias_state_counts['pending']
    enabled = alias_state_counts['enabled']
    return {
        'schema_version': 1,
        'library_sha256': library_hash,
        'identity_map_sha256': identity_map_sha256,
        'input_hashes': input_hashes or {},
        'script_sha256': script_sha256,
        'network_requests': 0,
        'identity_status': 'unverified',
        'recording_identity_claims': 0,
        'library_input': library_input,
        'summary': {
            'track_count': len(tracks),
            'alias_count': pending + enabled,
            'pending_alias_count': pending,
            'enabled_alias_count': enabled,
            'duplicate_title_artist_group_count': len(duplicate_rows),
            'duplicate_title_artist_record_count': sum(len(group['records']) for group in duplicate_rows),
            'version_marker_track_counts': dict(sorted(marker_counts.items())),
            'kind_track_counts': dict(sorted(kind_counts.items())),
        },
        'alias_status_counts': dict(sorted(alias_state_counts.items())),
        'tracks': track_rows,
        'duplicate_title_artist_groups': duplicate_rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('bundle', nargs='?', type=Path)
    source.add_argument('--track-snapshot', type=Path, help='Saved track metadata; does not read the original Library')
    parser.add_argument('--identity-map', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New JSON report path')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error('output must be a new file')
    if args.bundle and output.is_relative_to(args.bundle.resolve()):
        parser.error('output must be outside the input Library bundle')
    input_paths = [args.identity_map.resolve()]
    if args.track_snapshot:
        input_paths.append(args.track_snapshot.resolve())
    if output in input_paths:
        parser.error('output must be separate from all input files')
    tracks, library_hash, provenance = load_library(args.bundle, args.track_snapshot)
    map_bytes = args.identity_map.read_bytes()
    identity_map = json.loads(map_bytes)
    input_hashes = {'identity_map_sha256': hashlib.sha256(map_bytes).hexdigest()}
    if args.track_snapshot:
        input_hashes['track_snapshot_sha256'] = provenance['snapshot_sha256']
    else:
        input_hashes['library_sha256'] = library_hash
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = audit_library_identity(
        tracks, identity_map, library_hash, provenance,
        identity_map_sha256=input_hashes['identity_map_sha256'],
        input_hashes=input_hashes, script_sha256=script_hash)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'output': str(output), **report['summary']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
