"""Feed unverified local YTMusic metadata candidates to the actual Music core.

Offline synthetic graph only: no Apple database writes or recording confirmation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from genius_format import pack_config, pack_similarities, parse_config
from music_identity_map import IdentityMap, group_candidate_observations, load_library
from probe_ytmusic import normalize


def candidate_graph(snapshot, matcher, library_hash):
    if snapshot.get('library_sha256') not in (None, library_hash):
        raise ValueError('Observations belong to a different Library')
    rows, root_candidates = [], set()
    for observation in snapshot['observations']:
        if observation.get('source') != 'ytmusic' or observation.get('relation') not in ('radio', 'related'):
            raise ValueError('Only actual YTMusic radio/related observations are supported')
        track = observation['track']
        if not track.get('video_id'):
            raise ValueError('Observation needs a video ID')
        candidates = matcher.match(track)
        if observation['is_seed']:
            root_candidates.update(candidate['persistent_id'] for candidate in candidates)
        rows.append({'source': observation['source'], 'relation': observation['relation'],
                     'video_id': track['video_id'], 'is_seed': observation['is_seed'],
                     'position': observation['position'], 'observed_at': observation['observed_at'],
                     'exact_candidates': [], 'alias_candidates': candidates,
                     'identity_status': 'unverified'})
    if len(root_candidates) != 1:
        raise ValueError('Require exactly one duration-supported root PID')
    root = next(iter(root_candidates))
    groups = group_candidate_observations(rows)
    preferred = {(group['source'], group['video_id']): group['preferred_metadata_pid'] for group in groups}
    targets, seen = [], {root}
    for row in rows:
        if row['is_seed']:
            continue
        pid = preferred[(row['source'], row['video_id'])]
        if pid is not None and pid not in seen:
            targets.append(pid)
            seen.add(pid)
    return {'root_pid': root, 'ordered_target_pids': targets, 'observations': rows,
            'candidate_groups': groups, 'identity_status': 'unverified'}


def synthetic_rows(graph, matcher):
    pids = sorted({graph['root_pid'], *graph['ordered_target_pids']})
    if len(pids) > 0x10000000:
        raise ValueError('Temporary Genius ID range exhausted')
    ids = {pid: 0x70000001 + index for index, pid in enumerate(pids)}
    tracks = {track['persistent_id']: track for track in matcher.tracks}
    credits = {pid: tuple(sorted(matcher.artist_credits(tracks[pid].get('artist') or ''))) for pid in pids}
    albums = {pid: normalize(tracks[pid].get('album') or '') for pid in pids}
    songs = {pid: (credits[pid], normalize(tracks[pid].get('title') or '')) for pid in pids}
    def group_ids(values):
        return {value: index + 1 for index, value in enumerate(sorted(set(values.values())))}
    artist_ids, album_ids, song_ids = group_ids(credits), group_ids(albums), group_ids(songs)
    metadata = {ids[pid]: struct.pack('<4Q', 0, artist_ids[credits[pid]], album_ids[albums[pid]], song_ids[songs[pid]])
                for pid in pids}
    similarities = {identifier: pack_similarities(0, []) for identifier in ids.values()}
    similarities[ids[graph['root_pid']]] = pack_similarities(0, [ids[pid] for pid in graph['ordered_target_pids']])
    mapping = [{'persistent_id': pid, 'temporary_genius_id': f'{ids[pid]:016X}',
                'metadata_words': list(struct.unpack('<4Q', metadata[ids[pid]])),
                'canonical_artist_credits': credits[pid], 'identity_status': 'unverified'} for pid in pids]
    return ids, metadata, similarities, mapping


def controlled_configs(config):
    parsed = parse_config(config)
    result = {}
    for profile in ('without-compatible-genre', 'relations-only'):
        filtered = {**parsed, 'filters': [item for item in parsed['filters']
                    if (item['type'] != 2 if profile == 'without-compatible-genre' else item['type'] == 1)]}
        if not filtered['filters']:
            raise ValueError('Controlled configuration needs remaining filters')
        result[profile] = pack_config(filtered)
    return result


def run_experiment(executable, config, graph, matcher, limit=25):
    if not 1 <= limit <= 100:
        raise ValueError('Limit must be 1..100')
    from emulate_genius import MusicCore
    ids, metadata, similarities, mapping = synthetic_rows(graph, matcher)
    by_id = {identifier: pid for pid, identifier in ids.items()}
    runs = []
    for profile, controlled in controlled_configs(config).items():
        core = MusicCore(executable, controlled, metadata, similarities)
        generated = core.generate(ids[graph['root_pid']], limit)
        runs.append({'profile': profile, 'config_sha256': hashlib.sha256(controlled).hexdigest(),
                     **generated, 'playlist_pids': [by_id[int(value, 16)] for value in generated['result_genius_ids']],
                     'identity_status': 'unverified'})
    return {'temporary_id_mapping': mapping, 'runs': runs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('track-snapshot', 'identity-map', 'observations', 'genius-reference', 'executable', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--limit', type=int, default=25)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error('Limit must be 1..100')
    inputs = {name: getattr(args, name).resolve() for name in
              ('track_snapshot', 'identity_map', 'observations', 'genius_reference', 'executable')}
    output = args.output.resolve()
    if output.exists() or output in inputs.values() or output.is_relative_to(inputs['executable'].parent):
        parser.error('Output must be new and outside inputs and executable directory')
    tracks, library_hash, provenance = load_library(track_snapshot=inputs['track_snapshot'])
    blobs = {name: path.read_bytes() for name, path in inputs.items() if name not in ('track_snapshot', 'executable')}
    document = json.loads(blobs['identity_map'])
    matcher = IdentityMap(document, tracks, library_hash)
    graph = candidate_graph(json.loads(blobs['observations']), matcher, library_hash)
    from emulate_genius import database_rows
    config, _, _ = database_rows(blobs['genius_reference'])
    input_hashes = {name: hashlib.sha256(data).hexdigest() for name, data in blobs.items()}
    input_hashes['track_snapshot'] = provenance['snapshot_sha256']
    input_hashes['executable'] = hashlib.sha256(inputs['executable'].read_bytes()).hexdigest()
    report = {'schema_version': 1, 'library_sha256': library_hash, 'library_input': provenance,
              'input_sha256': input_hashes,
              'graph': graph, **run_experiment(inputs['executable'], config, graph, matcher, args.limit),
              'grouping_policy': {'genre': 'index 0: zero placeholder; compatible genre filter excluded',
                                  'artist': 'index 1: sorted canonical artist credit sets',
                                  'album': 'index 2: normalized local album label',
                                  'song': 'index 3: canonical credits and normalized original local title',
                                  'relations': 'root only; radio/related source order; deduplicated PID; no reverse edges',
                                  'selection': 'per-video preferred metadata PID; one duration-supported root',
                                  'recording_identity': 'unverified; song groups conservatively suppress repeats'},
              'callback_policy': {'eligibility': 'temporary ID exists in metadata', 'play_and_skip_history': 'all zero',
                                  'current_time': 0, 'random_seed': 0, 'byte_order': 'little endian'},
              'environment': 'isolated actual ARM64 Music core with Python callbacks',
              'network_requests': 0, 'account_authentication_rechecked': False, 'identity_status': 'unverified', 'verified_recordings': 0,
              'music_app_acceptance_verified': False, 'ipod_acceptance_verified': False}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'output': str(output), 'root_pid': graph['root_pid'],
                      'relation_targets': len(graph['ordered_target_pids']), 'verified_recordings': 0}))


if __name__ == '__main__':
    main()
