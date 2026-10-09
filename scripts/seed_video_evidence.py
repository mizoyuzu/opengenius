"""Build and validate strict video-ID evidence from saved radio observations.

The sidecar supports seed selection only. It records no recommendation edges
and never upgrades a metadata candidate to a verified recording identity.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from evaluate_ytmusic_batch import validate_observations
from music_identity_map import IdentityMap, load_library, version_signature


PRODUCER = 'opengenius-seed-video-evidence-v1'
VIDEO_RE = re.compile(r'[A-Za-z0-9_-]{11}\Z')
SHA_RE = re.compile(r'[a-f0-9]{64}\Z')
ROW_KEYS = {'persistent_id', 'video_id', 'input_path', 'input_sha256', 'source_relation'}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _candidate_for_video(snapshot, video_id, persistent_id, matcher):
    """Return true only when every saved radio occurrence has one strict match."""
    rows = [row for row in snapshot['observations']
            if row['source'] == 'ytmusic' and row['relation'] == 'radio'
            and row['track']['video_id'] == video_id]
    if not rows:
        return False
    for row in rows:
        candidates = matcher.match(row['track'])
        if (len(candidates) != 1 or candidates[0].get('persistent_id') != persistent_id
                or candidates[0].get('artist_credit_match') != 'complete'):
            return False
        local = candidates[0].get('local_metadata') or {}
        if version_signature(local.get('title') or '') != version_signature(row['track'].get('title') or ''):
            return False
    return True


def build_seed_video_evidence(graph_paths, matcher, library_hash, identity_map_sha256):
    """Create deterministic PID→radio-video hints from raw snapshot paths.

    Invalid or unbound input files are skipped and counted. Candidate records
    contain their actual source path and content hash for later revalidation.
    """
    if not SHA_RE.fullmatch(library_hash or '') or not SHA_RE.fullmatch(identity_map_sha256 or ''):
        raise ValueError('Library and identity-map hashes must be SHA-256')
    if not isinstance(graph_paths, (list, tuple, set)):
        raise ValueError('Snapshot paths must be a list, tuple, or set')
    candidates = {}
    skipped = []
    radio_count = 0
    for raw_path in graph_paths:
        expected_sha = None
        if isinstance(raw_path, dict):
            source = raw_path.get('input')
            if isinstance(source, dict):
                raw_path, expected_sha = source.get('path'), source.get('sha256')
            else:
                raw_path = None
        if not isinstance(raw_path, (str, Path)):
            skipped.append({'path': None, 'reason': 'invalid_snapshot_path'})
            continue
        path = Path(raw_path).resolve()
        try:
            data = path.read_bytes()
            snapshot = json.loads(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            skipped.append({'path': str(path), 'reason': 'unreadable_or_invalid_snapshot'})
            continue
        try:
            validate_observations(snapshot, library_hash)
        except ValueError as error:
            skipped.append({'path': str(path), 'reason': str(error)})
            continue
        if (snapshot.get('library_sha256') != library_hash
                or snapshot.get('identity_map_sha256') != identity_map_sha256):
            skipped.append({'path': str(path), 'reason': 'source_hash_mismatch'})
            continue
        raw_sha = _sha(data)
        if expected_sha is not None and expected_sha != raw_sha:
            skipped.append({'path': str(path), 'reason': 'graph_input_sha256_mismatch'})
            continue
        by_video = {}
        for observation in snapshot['observations']:
            if observation.get('relation') != 'radio' or observation.get('source') != 'ytmusic':
                continue
            radio_count += 1
            by_video.setdefault(observation['track']['video_id'], []).append(observation)
        for video_id, observations in by_video.items():
            # Match every repeated observation for the video; one partial or
            # conflicting row makes that video unsuitable as a seed hint.
            matching = []
            valid = True
            for observation in observations:
                found = matcher.match(observation['track'])
                if len(found) != 1 or found[0].get('artist_credit_match') != 'complete':
                    valid = False
                    break
                matching.append(found[0]['persistent_id'])
            if not valid or len(set(matching)) != 1:
                continue
            pid = matching[0]
            if not _candidate_for_video(snapshot, video_id, pid, matcher):
                continue
            candidates.setdefault(pid, []).append({
                'persistent_id': pid, 'video_id': video_id,
                'input_path': str(path), 'input_sha256': raw_sha,
                'source_relation': 'radio',
            })
    # One stable video per PID. Repeated saved evidence for the same video is
    # collapsed; ties are resolved by video ID and then source path.
    video_pids = {}
    for pid, hints in candidates.items():
        for hint in hints:
            video_pids.setdefault(hint['video_id'], set()).add(pid)
    ambiguous_videos = {video for video, pids in video_pids.items() if len(pids) != 1}
    rows = []
    for pid in sorted(candidates):
        eligible = [row for row in candidates[pid] if row['video_id'] not in ambiguous_videos]
        if eligible:
            rows.append(min(eligible, key=lambda row: (row['video_id'], row['input_path'])))
    rows.sort(key=lambda row: row['persistent_id'])
    return {
        'schema_version': 1, 'producer': PRODUCER,
        'library_sha256': library_hash,
        'identity_map_sha256': identity_map_sha256,
        'rows': rows,
        'audit': {
            'input_snapshot_count': len(graph_paths),
            'radio_observation_count': radio_count,
            'accepted_seed_video_count': len(rows),
            'ambiguous_video_mapping_count': len(ambiguous_videos),
            'skipped_snapshot_count': len(skipped),
            'skipped_snapshots': skipped,
            'identity_status': 'unverified_metadata_candidate',
            'edges_created': 0,
        },
    }


def select_seed_video_evidence(document, evidence_path, seeds, tracks, matcher,
                               library_hash, identity_map_sha256):
    """Validate an evidence sidecar and return exact requested seed hints."""
    if (not isinstance(document, dict) or type(document.get('schema_version')) is not int
            or document['schema_version'] != 1 or document.get('producer') != PRODUCER
            or document.get('library_sha256') != library_hash
            or document.get('identity_map_sha256') != identity_map_sha256
            or not SHA_RE.fullmatch(library_hash or '')
            or not SHA_RE.fullmatch(identity_map_sha256 or '')):
        raise ValueError('Seed-video evidence schema or source hashes differ')
    rows = document.get('rows')
    if not isinstance(rows, list):
        raise ValueError('Seed-video evidence requires rows')
    by_pid = {track.get('persistent_id'): track for track in tracks}
    if len(by_pid) != len(tracks):
        raise ValueError('Duplicate Library persistent IDs')
    requested = {seed.get('persistent_id') for seed in seeds}
    if None in requested:
        raise ValueError('Requested seed has no persistent ID')
    selected, seen_videos = {}, set()
    sidecar_dir = Path(evidence_path).resolve().parent
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            raise ValueError('Malformed seed-video evidence row')
        pid, video_id = row['persistent_id'], row['video_id']
        if (not isinstance(pid, str) or not re.fullmatch('[0-9A-F]{16}', pid)
                or pid not in by_pid or pid in selected
                or not isinstance(video_id, str) or not VIDEO_RE.fullmatch(video_id)
                or video_id in seen_videos or row.get('source_relation') != 'radio'
                or not isinstance(row.get('input_path'), str)
                or not isinstance(row.get('input_sha256'), str)
                or not SHA_RE.fullmatch(row['input_sha256'])):
            raise ValueError('Invalid or duplicate seed-video evidence row')
        source_path = Path(row['input_path'])
        if not source_path.is_absolute():
            source_path = sidecar_dir / source_path
        try:
            source_data = source_path.read_bytes()
            if _sha(source_data) != row['input_sha256']:
                raise ValueError('Seed-video source snapshot hash changed')
            snapshot = json.loads(source_data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Seed-video source snapshot is unreadable') from None
        validate_observations(snapshot, library_hash)
        if (snapshot.get('library_sha256') != library_hash
                or snapshot.get('identity_map_sha256') != identity_map_sha256):
            raise ValueError('Seed-video source snapshot has foreign source hashes')
        if not _candidate_for_video(snapshot, video_id, pid, matcher):
            raise ValueError('Seed-video evidence no longer has one complete-credit duration match')
        selected[pid] = {
            'video_id': video_id,
            'source': 'saved_recommendation_metadata_candidate_unverified',
            'input_path': str(source_path.resolve()),
            'input_sha256': row['input_sha256'],
        }
        seen_videos.add(video_id)
    return {pid: selected[pid] for pid in requested if pid in selected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--identity-map', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, action='append', default=[])
    parser.add_argument('--snapshot-directory', type=Path, action='append', default=[])
    parser.add_argument('--graph-provenance', type=Path, action='append', default=[],
                        help='Saved raw graph list; extracts each graph input.path')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error('Output exists; choose a new path')
    tracks, library_hash, _ = load_library(track_snapshot=args.track_snapshot)
    map_bytes = args.identity_map.read_bytes()
    map_hash = _sha(map_bytes)
    matcher = IdentityMap(json.loads(map_bytes), tracks, library_hash)
    paths = [path.resolve() for path in args.snapshot]
    for directory in args.snapshot_directory:
        paths.extend(sorted(path.resolve() for path in directory.glob('*.json')))
    for graph_file in args.graph_provenance:
        graphs = json.loads(graph_file.read_text(encoding='utf-8'))
        if not isinstance(graphs, list):
            parser.error('--graph-provenance must contain a list of raw graphs')
        for graph in graphs:
            source = graph.get('input') if isinstance(graph, dict) else None
            if not isinstance(source, dict) or not isinstance(source.get('path'), str):
                parser.error('Each raw graph needs input.path provenance')
            source_path = Path(source['path'])
            if not source_path.is_absolute():
                source_path = Path(__file__).resolve().parents[1] / source_path
            paths.append({'input': {**source, 'path': str(source_path.resolve())}})
    if not paths:
        parser.error('Supply at least one --snapshot or --snapshot-directory')
    document = build_seed_video_evidence(paths, matcher, library_hash, map_hash)
    document['identity_map_input_sha256'] = map_hash
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'output': str(output),
                      'accepted_seed_video_count': len(document['rows']),
                      'skipped_snapshot_count': document['audit']['skipped_snapshot_count'],
                      'edges_created': 0}))


if __name__ == '__main__':
    main()
